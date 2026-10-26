"""Config-driven condition taxonomy mapping.

Deterministic, version-controlled YAML rules (config/condition_taxonomy*.yml).
No runtime LLM classification. Groups are evaluated in order; first match wins.
"""

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from src.utils.paths import project_root
from src.utils.text import normalize_text

DEFAULT_TAXONOMY_PATH = "config/condition_taxonomy.yml"


@dataclass(frozen=True)
class ConditionMapping:
    condition_normalized: str | None
    condition_group: str
    dementia_relevance_flag: bool
    mapping_confidence: str  # high | medium | low


@dataclass(frozen=True)
class TaxonomyGroup:
    condition_group: str
    dementia_relevance: bool
    exact: tuple[str, ...]
    substrings: tuple[str, ...]
    display_name: str | None = None

    @property
    def label(self) -> str:
        return self.display_name or self.condition_group


@dataclass(frozen=True)
class ConditionTaxonomy:
    groups: tuple[TaxonomyGroup, ...]
    default_group: str
    default_dementia_relevance: bool
    default_display_name: str | None = None

    @property
    def default_label(self) -> str:
        return self.default_display_name or self.default_group

    def get_group_label(self, group_key: str) -> str:
        """Resolve a condition group identifier to its human-readable display label.

        Falls back cleanly to the group key if no display label metadata exists.
        """
        for group in self.groups:
            if group.condition_group == group_key:
                return group.display_name or group.condition_group
        if group_key == self.default_group:
            return self.default_display_name or self.default_group
        return group_key

    def map_condition(self, condition_raw: object) -> ConditionMapping:
        cleaned = normalize_text(condition_raw)
        if cleaned is None:
            return ConditionMapping(
                None, self.default_group, self.default_dementia_relevance, "low"
            )
        for group in self.groups:
            if cleaned in group.exact:
                return ConditionMapping(
                    cleaned, group.condition_group, group.dementia_relevance, "high"
                )
        for group in self.groups:
            if any(term in cleaned for term in group.substrings):
                return ConditionMapping(
                    cleaned, group.condition_group, group.dementia_relevance, "medium"
                )
        return ConditionMapping(cleaned, self.default_group, self.default_dementia_relevance, "low")


def load_taxonomy(path: str | Path | None = None) -> ConditionTaxonomy:
    taxonomy_path = Path(path) if path else project_root() / DEFAULT_TAXONOMY_PATH
    raw = yaml.safe_load(taxonomy_path.read_text(encoding="utf-8"))
    groups = tuple(
        TaxonomyGroup(
            condition_group=entry["condition_group"],
            dementia_relevance=bool(entry["dementia_relevance"]),
            exact=tuple(entry.get("exact") or []),
            substrings=tuple(entry.get("substrings") or []),
            display_name=entry.get("display_name") or entry.get("label"),
        )
        for entry in raw["groups"]
    )
    default_entry = raw.get("default", {})
    return ConditionTaxonomy(
        groups=groups,
        default_group=default_entry["condition_group"],
        default_dementia_relevance=bool(default_entry["dementia_relevance"]),
        default_display_name=default_entry.get("display_name") or default_entry.get("label"),
    )


@lru_cache(maxsize=1)
def get_taxonomy() -> ConditionTaxonomy:
    return load_taxonomy()
