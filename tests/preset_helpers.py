"""Shared preset lookup for dashboard tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from typing import Any

from src.presets import Preset, load_presets, registry_condition_key
from tests.fixture_warehouse import FixtureRun, base_adrd_run


def preset_for(preset_id: str) -> Preset:
    """The shipped preset for `preset_id`, or an empty-scope stand-in for any other id.

    `full_catalog` is `ingest_only`, so no preset exists for it; its stand-in names a
    node no mart carries, which is the "scope with no rows" callers need.
    """
    shipped = {p.preset_id: p for p in load_presets()}
    if preset_id in shipped:
        return shipped[preset_id]
    node = f"group:no-such-node-for-{preset_id}"
    return Preset(preset_id, preset_id, (node,), (registry_condition_key(node),), (), "", (), ())


def shared_trial_extra_profiles() -> dict[str, str]:
    """Define a second preset that shares the MeSH node 'Alzheimer Disease' with adrd."""
    return {
        "shared_preset.yml": (
            "profile:\n"
            "  id: shared_preset\n"
            "  display_name: 'Shared Preset'\n"
            "  ingest_only: false\n"
            "  taxonomy: config/condition_taxonomy.yml\n"
            "preset:\n"
            "  mesh_terms:\n"
            "    - 'Alzheimer Disease'\n"
        )
    }


def shared_trial_runs() -> list[FixtureRun]:
    """Return independent profile observations with shared nodes and divergent versions.

    - Returns three synthetic bronze runs on September 1, 8 and 15.
    - Uses the ADRD page under two real profile taxonomies; no dimension rows or
      generated keys are copied. NCT00000010 deliberately has no condition/MeSH.
    - NCT00000001 and NCT00000002 share the 'Alzheimer Disease' MeSH node across
      both 'adrd' and 'shared_preset'.
    - NCT00000008 is only present through September 8, testing distinct snapshot dates.
    """
    # 1. Keep a shared registry node, including an ancestor, independent of overlays.
    mesh = {
        "conditionBrowseModule": {
            "meshes": [{"id": "D000544", "term": "Alzheimer Disease"}],
            "ancestors": [{"id": "D001523", "term": "Mental Disorders"}],
        }
    }
    base = base_adrd_run()
    runs = []
    # 2. Different dates and payloads expose global-latest and cross-profile joins.
    for index, (profile, status, enrollment, sponsor) in enumerate(
        (
            ("adrd", "RECRUITING", 500, "NeuroPharm Inc"),
            ("oncology_nsclc", "ACTIVE_NOT_RECRUITING", 600, "Variant Sponsor"),
            ("adrd", "COMPLETED", 700, "Latest Sponsor"),
        )
    ):
        study_overrides: dict[str, Any] = {
            "NCT00000001": {
                "derivedSection": mesh,
                "designModule": {"enrollmentInfo": {"count": enrollment, "type": "Anticipated"}},
                "sponsorCollaboratorsModule": {"leadSponsor": {"name": sponsor}},
            },
            "NCT00000002": {
                "derivedSection": mesh,
            },
            "NCT00000010": {"conditionsModule": {"conditions": []}},
        }
        runs.append(
            replace(
                base,
                profile=profile,
                run_id=f"202609{1 + index * 7:02d}T100000Z_shared{index}",
                started_at=base.started_at + timedelta(days=index * 7),
                status_overrides={"NCT00000001": status},
                study_overrides=study_overrides,
            )
        )
    return runs
