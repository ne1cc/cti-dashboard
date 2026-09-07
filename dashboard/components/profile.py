"""Sidebar indication-profile selector.

Registry-driven on purpose: `page_setup()` runs before `require_warehouse()` on
every page, and a freshly mounted Fly volume has no warehouse to query until the
first-boot pipeline finishes (docs/DEPLOY_FLY.md, "First boot"). A selector that
read `select distinct indication_profile_id` would crash all nine pages during
exactly the window where the dashboard has nothing to show yet.
"""

from __future__ import annotations

import streamlit as st

from src.profiles import get_registry


def refreshable_profiles() -> list[tuple[str, str]]:
    """`(profile_id, display_name)` for every profile the refresh actually runs.

    `refreshable()` excludes ingest_only profiles, so `full_catalog` never
    appears: it has bronze and no silver, and an empty tab for it would read as
    a data-quality finding rather than a scope decision.

    The display name is a registry read, not a warehouse read — this is the
    lookup the dashboard's former warehouse-derived profile filter did against
    ``dim_trial``, which discovered profiles from the data instead of from the
    registry that defines them. A warehouse id the registry did not know would
    have raised ``KeyError`` there; here no such id can reach the lookup, because
    the ids come from the registry itself.
    """
    return [(p.profile_id, p.display_name) for p in get_registry().refreshable()]


def render_profile_selector() -> str:
    """Render the sidebar selector and return the chosen `profile_id`.

    The widget value *is* the profile id, so no label→id lookup table exists to
    drift out of sync. `format_func` only affects what the reader sees.

    Returns:
        str: The selected indication profile id, e.g. `"adrd"`.
    """
    profiles = refreshable_profiles()
    if not profiles:
        # The same refusal `orchestrate`, `_refreshable_or_fail()` and both assets
        # make, in the one place a reader can reach it. A zero-option selectbox
        # does not raise — it renders empty and `str(None)` becomes the chosen id,
        # so the silent form of this failure is nine pages of zeros under a caption
        # claiming they belong to one profile.
        st.error(
            "No refreshable profiles in config/profiles/*.yml — check the registry "
            "and the `ingest_only` flags before trusting any figure on this page."
        )
        st.stop()
    display_names = dict(profiles)
    st.sidebar.header("Scope")
    chosen = st.sidebar.selectbox(
        "Indication profile",
        [profile_id for profile_id, _ in profiles],
        format_func=lambda profile_id: display_names.get(profile_id, profile_id),
        key="indication_profile_selector",
    )
    st.sidebar.caption(
        "All figures cover this indication profile only. Profiles are **not** "
        "additive: a trial listed under two profiles is counted once in each, "
        "because each profile is a separate registry query with its own "
        "condition taxonomy."
    )
    return str(chosen)
