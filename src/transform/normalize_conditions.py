"""Config-driven ADRD condition taxonomy mapping.

Deterministic, version-controlled YAML rules (config/condition_taxonomy.yml).
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


@dataclass(frozen=True)
class ConditionTaxonomy:
    groups: tuple[TaxonomyGroup, ...]
    default_group: str
    default_dementia_relevance: bool

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
        )
        for entry in raw["groups"]
    )
    return ConditionTaxonomy(
        groups=groups,
        default_group=raw["default"]["condition_group"],
        default_dementia_relevance=bool(raw["default"]["dementia_relevance"]),
    )


# The registry-wide profile (full_catalog) has no indication taxonomy: every
# condition maps to "uncategorized" with no dementia flag. Keeping this as a
# taxonomy — not a special case in flatten — is what lets one silver path serve
# both indication profiles and the whole registry (issue #53).
REGISTRY_TAXONOMY = ConditionTaxonomy(
    groups=(),
    default_group="uncategorized",
    default_dementia_relevance=False,
)


@lru_cache(maxsize=1)
def get_taxonomy() -> ConditionTaxonomy:
    return load_taxonomy()
