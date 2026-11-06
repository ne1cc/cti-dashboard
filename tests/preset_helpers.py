"""Shared preset lookup for dashboard tests."""

from __future__ import annotations

from src.presets import Preset, load_presets, registry_condition_key


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
