"""Dashboard presets: a name, registry condition nodes and default filters.

Intent: one place that turns `config/profiles/*.yml` (plus the profile's condition
taxonomy) into the preset list the dashboard offers, so adding a preset is one
YAML file and no code or pipeline change (epic #54, step 3).

Contract:
    - A preset belongs to exactly one profile (`profile_id`, its YAML id). Readers filter
      on that profile as well as on the keys, because the marts are profile-grained
      (`trial_key` is md5(nct_id, profile)) and two profiles may share a node id.
    - A preset's nodes are the `group:<slug>` ids of its taxonomy (every group plus
      the default group). `registry_condition_keys` are those ids hashed exactly as
      the dbt `generate_surrogate_key` macro hashes `registry_condition_key`.
    - `default_filters` (study type, statuses, phases) restate the profile's `scope`;
      the pipeline already applied them, so the dashboard only displays them.

Limits:
    - MeSH nodes are not resolved: `preset.mesh_terms` holds descriptor labels, the
      marts carry descriptor ids. A profile whose taxonomy has no groups therefore
      resolves to the single `group:<default>` node, which every such preset shares.
      shortcut: group nodes only, until the marts re-key on MeSH ids (#149).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from src.profiles import IndicationProfile, ProfileRegistry, get_registry


def registry_condition_key(node_id: str) -> str:
    """Mirror dbt's `generate_surrogate_key([node_id])`: md5 of the single value."""
    return hashlib.md5(node_id.encode(), usedforsecurity=False).hexdigest()


@dataclass(frozen=True)
class Preset:
    """One selectable dashboard scope. Frozen and tuple-only so Streamlit can cache on it."""

    preset_id: str
    name: str
    node_ids: tuple[str, ...]
    registry_condition_keys: tuple[str, ...]
    mesh_terms: tuple[str, ...]
    study_type: str
    statuses: tuple[str, ...]
    phases: tuple[str, ...]

    @property
    def profile_id(self) -> str:
        """The owning profile (`preset_id`): the only `indication_profile_id` readers may touch."""
        return self.preset_id


def preset_from_profile(profile: IndicationProfile) -> Preset:
    """Derive a preset from a refreshable profile (its taxonomy must be loaded)."""
    taxonomy = profile.taxonomy
    if taxonomy is None:
        raise ValueError(f"Profile {profile.profile_id!r} has no taxonomy; it cannot be a preset")
    groups = [g.condition_group for g in taxonomy.groups] + [taxonomy.default_group]
    node_ids = tuple(dict.fromkeys(f"group:{g}" for g in groups))
    scope = profile.config.scope
    return Preset(
        preset_id=profile.profile_id,
        name=profile.display_name,
        node_ids=node_ids,
        registry_condition_keys=tuple(registry_condition_key(n) for n in node_ids),
        mesh_terms=profile.mesh_terms,
        study_type=str(scope.get("study_type") or ""),
        statuses=tuple(scope.get("statuses") or ()),
        phases=tuple(scope.get("phases") or ()),
    )


def load_presets(registry: ProfileRegistry | None = None) -> list[Preset]:
    """Every preset the refresh runs, in registry order (`ingest_only` profiles excluded)."""
    reg = registry or get_registry()
    return [preset_from_profile(p) for p in reg.refreshable()]
