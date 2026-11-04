"""MeSH condition search, consumer synonym autocomplete, and hierarchy resolution."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from src.api.models import ConceptReference, ConditionNode

logger = logging.getLogger(__name__)

RESOURCE_PATH = Path(__file__).resolve().parent / "resources" / "mesh_conditions.json"


def _clean_str(text: str) -> str:
    """Normalize string for comparison: lowercase, punctuation stripped, whitespace collapsed."""
    cleaned = re.sub(r"[^a-z0-9\s]", " ", text.lower())
    return " ".join(cleaned.split())


class MeSHConditionIndex:
    """In-memory index of MeSH concepts, tree hierarchy, and entry terms/synonyms."""

    def __init__(self, data_path: Path | None = None) -> None:
        self.data_path = data_path or RESOURCE_PATH
        self._concepts: list[dict[str, Any]] = []
        self._by_id: dict[str, dict[str, Any]] = {}
        self._by_tree_code: dict[str, dict[str, Any]] = {}
        self._by_clean_name: dict[str, dict[str, Any]] = {}
        self._search_index: list[tuple[str, str, dict[str, Any]]] = []
        self._load()

    def _load(self) -> None:
        if not self.data_path.exists():
            logger.warning("MeSH dataset not found at %s", self.data_path)
            return

        with open(self.data_path, encoding="utf-8") as f:
            self._concepts = json.load(f)

        for concept in self._concepts:
            mesh_id = concept["mesh_id"]
            term = concept["term"]
            self._by_id[mesh_id.upper()] = concept
            self._by_id[mesh_id.lower()] = concept

            clean_term = _clean_str(term)
            self._by_clean_name[clean_term] = concept
            self._search_index.append((clean_term, "term", concept))

            for code in concept.get("tree_codes", []):
                self._by_tree_code[code] = concept

            for syn in concept.get("synonyms", []):
                clean_syn = _clean_str(syn)
                if clean_syn not in self._by_clean_name:
                    self._by_clean_name[clean_syn] = concept
                self._search_index.append((clean_syn, "synonym", concept))

    def get_by_id(self, mesh_id: str) -> dict[str, Any] | None:
        """Lookup MeSH concept by unique identifier (e.g. D000544)."""
        return self._by_id.get(mesh_id.strip().upper())

    def get_children(self, parent_identifier: str) -> list[dict[str, Any]]:
        """Retrieve narrower / child concepts for a parent MeSH ID or tree code."""
        parent_id = parent_identifier.strip()
        # Check if it's a MeSH ID
        concept = self.get_by_id(parent_id)
        if concept:
            narrower = concept.get("narrower", [])
            children: list[dict[str, Any]] = []
            for item in narrower:
                c = self.get_by_id(item["mesh_id"])
                if c:
                    children.append(c)
            if children:
                return children

        # Check by tree code prefix
        prefix = parent_id + "."
        results: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for code, c in self._by_tree_code.items():
            if code.startswith(prefix) and c["mesh_id"] not in seen_ids:
                seen_ids.add(c["mesh_id"])
                results.append(c)
        return results

    def resolve_concept(self, text: str) -> dict[str, Any] | None:
        """Resolve a consumer phrase, synonym, or keyword to the canonical MeSH concept."""
        clean = _clean_str(text)
        if not clean:
            return None

        # 1. Exact match on term or synonym
        if clean in self._by_clean_name:
            return self._by_clean_name[clean]

        # 2. Check if text is a direct MeSH ID
        if text.strip().upper() in self._by_id:
            return self._by_id[text.strip().upper()]

        return None

    def search(
        self,
        q: str | None = None,
        mesh_id: str | None = None,
        parent_id: str | None = None,
        limit: int | None = 20,
    ) -> list[dict[str, Any]]:
        """Search or browse MeSH concepts by query, mesh_id, or parent hierarchy."""
        if mesh_id:
            c = self.get_by_id(mesh_id)
            return [c] if c else []

        if parent_id:
            children = self.get_children(parent_id)
            return children[:limit] if limit is not None else children

        if not q or not q.strip():
            # Return high-level categories or top conditions up to limit
            return self._concepts[:limit] if limit is not None else list(self._concepts)

        clean_q = _clean_str(q)
        q_tokens = clean_q.split()

        scores: dict[str, tuple[int, dict[str, Any]]] = {}

        for indexed_text, match_type, concept in self._search_index:
            cid = concept["mesh_id"]
            score = 0

            if clean_q == indexed_text:
                score = 100 if match_type == "term" else 90
            elif indexed_text.startswith(clean_q):
                score = 80 if match_type == "term" else 70
            elif any(token.startswith(clean_q) for token in indexed_text.split()):
                score = 65 if match_type == "term" else 55
            elif clean_q in indexed_text:
                score = 50 if match_type == "term" else 40
            elif all(token in indexed_text for token in q_tokens):
                score = 45 if match_type == "term" else 35

            if score > 0:
                existing = scores.get(cid)
                if existing is None or score > existing[0]:
                    scores[cid] = (score, concept)

        sorted_results = sorted(
            scores.values(),
            key=lambda x: (-x[0], len(x[1]["term"]), x[1]["term"]),
        )
        results = [c for _, c in sorted_results]
        return results[:limit] if limit is not None else results


_GLOBAL_INDEX: MeSHConditionIndex | None = None


def get_mesh_index() -> MeSHConditionIndex:
    """Return singleton instance of MeSHConditionIndex."""
    global _GLOBAL_INDEX
    if _GLOBAL_INDEX is None:
        _GLOBAL_INDEX = MeSHConditionIndex()
    return _GLOBAL_INDEX


def _fetch_warehouse_counts(
    warehouse: Any,
    indication_profile_id: str | None = None,
) -> tuple[dict[str, int], dict[str, int], set[str] | None]:
    """Fetch total and active trial counts from DuckDB warehouse per condition / mesh_id."""
    total_counts: dict[str, int] = {}
    active_counts: dict[str, int] = {}
    scoped_conditions: set[str] | None = set() if indication_profile_id is not None else None
    if warehouse is None:
        return total_counts, active_counts, scoped_conditions

    try:
        pool = warehouse.pool
        with pool.acquire() as conn:
            # Check if dim_condition exists
            has_dim_cond = bool(
                conn.execute(
                    "SELECT 1 FROM information_schema.tables WHERE table_name = 'dim_condition';"
                ).fetchone()
            )
            if has_dim_cond:
                query = """
                SELECT
                    mesh_id,
                    lower(condition_normalized) AS norm_name,
                    sum(trial_count) AS total
                FROM dim_condition
                WHERE ($1 IS NULL OR indication_profile_id = $1)
                GROUP BY mesh_id, lower(condition_normalized);
                """
                for row in conn.execute(query, [indication_profile_id]).fetchall():
                    m_id, name, cnt = row
                    count = int(cnt or 0)
                    if m_id:
                        mid_clean = m_id.upper()
                        total_counts[mid_clean] = total_counts.get(mid_clean, 0) + count
                        if scoped_conditions is not None:
                            scoped_conditions.add(mid_clean)
                    if name:
                        name_clean = _clean_str(name)
                        total_counts[name_clean] = total_counts.get(name_clean, 0) + count
                        if scoped_conditions is not None:
                            scoped_conditions.add(name_clean)

            # Check if bridge_trial_condition and dim_trial exist for active counts
            has_bridge = bool(
                conn.execute(
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_name = 'bridge_trial_condition';"
                ).fetchone()
            )
            has_dim_trial = bool(
                conn.execute(
                    "SELECT 1 FROM information_schema.tables WHERE table_name = 'dim_trial';"
                ).fetchone()
            )
            if has_bridge and has_dim_trial:
                active_query = """
                SELECT
                    b.mesh_id,
                    lower(b.condition_group) AS term_norm,
                    count(DISTINCT b.nct_id) AS active_cnt
                FROM bridge_trial_condition b
                JOIN dim_trial t ON b.trial_key = t.trial_key
                WHERE t.current_overall_status IN (\
                    'RECRUITING', 'ENROLLING_BY_INVITATION', 'ACTIVE_NOT_RECRUITING'\
                )
                  AND ($1 IS NULL OR b.indication_profile_id = $1)
                GROUP BY b.mesh_id, lower(b.condition_group);
                """
                for row in conn.execute(active_query, [indication_profile_id]).fetchall():
                    m_id, term, cnt = row
                    count = int(cnt or 0)
                    if m_id:
                        mid_clean = m_id.upper()
                        active_counts[mid_clean] = active_counts.get(mid_clean, 0) + count
                        if scoped_conditions is not None:
                            scoped_conditions.add(mid_clean)
                    if term:
                        term_clean = _clean_str(term)
                        active_counts[term_clean] = active_counts.get(term_clean, 0) + count
                        if scoped_conditions is not None:
                            scoped_conditions.add(term_clean)
    except Exception as err:
        logger.debug("Failed querying warehouse condition counts: %s", err)

    return total_counts, active_counts, scoped_conditions


def search_conditions(
    q: str | None = None,
    mesh_id: str | None = None,
    parent_id: str | None = None,
    indication_profile_id: str | None = None,
    limit: int = 20,
    warehouse: Any = None,
) -> tuple[list[ConditionNode], int]:
    """Search and browse conditions with synonyms, hierarchy, and study counts."""
    index = get_mesh_index()
    matched_raw = index.search(q=q, mesh_id=mesh_id, parent_id=parent_id, limit=None)

    total_counts, active_counts, scoped_conditions = _fetch_warehouse_counts(
        warehouse, indication_profile_id=indication_profile_id
    )

    if scoped_conditions is not None:
        matched_raw = [
            raw
            for raw in matched_raw
            if raw["mesh_id"].upper() in scoped_conditions
            or _clean_str(raw["term"]) in scoped_conditions
        ]

    total_count = len(matched_raw)
    paged_raw = matched_raw[:limit]

    nodes: list[ConditionNode] = []
    for raw in paged_raw:
        mid = raw["mesh_id"]
        term = raw["term"]
        clean_name = _clean_str(term)

        # Lookup warehouse counts by mesh_id or normalized term
        t_cnt = total_counts.get(mid.upper(), total_counts.get(clean_name, 0))
        a_cnt = active_counts.get(mid.upper(), active_counts.get(clean_name, 0))

        broader = [
            ConceptReference(
                mesh_id=b.get("mesh_id"),
                term=b["term"],
                tree_code=b.get("tree_code"),
            )
            for b in raw.get("broader", [])
        ]
        narrower = [
            ConceptReference(
                mesh_id=n.get("mesh_id"),
                term=n["term"],
                tree_code=n.get("tree_code"),
            )
            for n in raw.get("narrower", [])
        ]

        nodes.append(
            ConditionNode(
                condition_name=term,
                condition_normalized=clean_name,
                condition_group=term,
                mesh_id=mid,
                mesh_tree_code=raw.get("tree_codes", [None])[0],
                synonyms=raw.get("synonyms", []),
                trial_count=t_cnt,
                active_trial_count=a_cnt,
                has_children=raw.get("has_children", len(narrower) > 0),
                broader=broader,
                narrower=narrower,
            )
        )

    return nodes, total_count


def resolve_consumer_condition(text: str) -> ConditionNode | None:
    """Resolve consumer phrasing into a ConditionNode with broader and narrower terms."""
    index = get_mesh_index()
    raw = index.resolve_concept(text)
    if not raw:
        return None

    broader = [
        ConceptReference(
            mesh_id=b.get("mesh_id"),
            term=b["term"],
            tree_code=b.get("tree_code"),
        )
        for b in raw.get("broader", [])
    ]
    narrower = [
        ConceptReference(
            mesh_id=n.get("mesh_id"),
            term=n["term"],
            tree_code=n.get("tree_code"),
        )
        for n in raw.get("narrower", [])
    ]

    return ConditionNode(
        condition_name=raw["term"],
        condition_normalized=_clean_str(raw["term"]),
        condition_group=raw["term"],
        mesh_id=raw["mesh_id"],
        mesh_tree_code=raw.get("tree_codes", [None])[0],
        synonyms=raw.get("synonyms", []),
        trial_count=0,
        active_trial_count=0,
        has_children=raw.get("has_children", len(narrower) > 0),
        broader=broader,
        narrower=narrower,
    )
