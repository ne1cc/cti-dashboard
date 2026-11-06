"""Sidebar preset selector.

Registry-driven on purpose: `page_setup()` runs before `require_warehouse()` on
every page, and a freshly mounted volume has no warehouse to query until the
first-boot pipeline finishes (docs/OPERATIONS.md, "What to expect"). A selector that
read `select distinct ...` from the warehouse would crash every page during
exactly the window where the dashboard has nothing to show yet.

A preset is `src.presets.Preset`: a name, the registry condition nodes it covers
and its default filters. Adding one is a `config/profiles/<id>.yml` file.
"""

from __future__ import annotations

import streamlit as st

from src.presets import Preset, load_presets


def render_preset_selector() -> Preset:
    """Render the sidebar selector and return the chosen `Preset`.

    The widget value is the preset id, so no label→preset table exists to drift
    out of sync. `format_func` only affects what the reader sees. The widget key
    stays `indication_profile_selector` (the smoke tests and saved sessions use it).

    Returns:
        Preset: The selected preset. Stops the page when no preset is configured.
    """
    presets = {p.preset_id: p for p in load_presets()}
    if not presets:
        # The same refusal `orchestrate`, `_refreshable_or_fail()` and both assets
        # make, in the one place a reader can reach it. A zero-option selectbox
        # does not raise: it renders empty and the chosen value becomes None, so the
        # silent form of this failure is pages of zeros under a caption claiming
        # they belong to one preset.
        st.error(
            "No refreshable profiles in config/profiles/*.yml — check the registry "
            "and the `ingest_only` flags before trusting any figure on this page."
        )
        st.stop()
    st.sidebar.header("Scope")
    chosen = st.sidebar.selectbox(
        "Indication preset",
        list(presets),
        format_func=lambda preset_id: presets[preset_id].name,
        key="indication_profile_selector",
    )
    preset = presets[str(chosen)]
    filters = [preset.study_type, *preset.statuses, *preset.phases]
    st.sidebar.caption("Default filters: " + (", ".join(f for f in filters if f) or "none"))
    return preset
