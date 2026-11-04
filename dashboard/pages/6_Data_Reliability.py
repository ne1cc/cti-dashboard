"""Data Reliability — run confidence, reconciliation, and ingestion diagnostics."""

import streamlit as st
from components import data
from components.audit_panel import render_provenance_drawer
from components.guardrails import guarded_footer, page_setup
from components.guidance import render_page_guide
from components.profile import render_profile_selector

page_setup("Data Reliability")
profile_id = render_profile_selector()
data.require_warehouse(profile_id)

render_page_guide("data_reliability", profile_id)

reliability = data.data_reliability(profile_id)

st.subheader("Ingestion runs")
st.dataframe(
    reliability[
        [
            "snapshot_date",
            "status",
            "page_count",
            "manifest_record_count",
            "trial_row_count",
            "manifest_reconciled_flag",
            "unique_nct_flag",
            "quarantined_record_count",
            "flagged_record_share",
            "usable_location_share",
            "coordinates_exact_share",
            "coordinates_fallback_eligible_share",
            "low_confidence_condition_share",
            "ingestion_run_id",
        ]
    ],
    hide_index=True,
    width="content",
    column_config={
        "ingestion_run_id": st.column_config.TextColumn("Run ID"),
        "snapshot_date": st.column_config.DateColumn("Captured on", format="MMM D, YYYY"),
        "status": st.column_config.TextColumn("Status"),
        "page_count": st.column_config.NumberColumn("Pages", format="%d"),
        "manifest_record_count": st.column_config.NumberColumn("Captured records", format="%d"),
        "trial_row_count": st.column_config.NumberColumn("Study records", format="%d"),
        "manifest_reconciled_flag": st.column_config.CheckboxColumn("Record counts match"),
        "unique_nct_flag": st.column_config.CheckboxColumn("No duplicate studies"),
        "quarantined_record_count": st.column_config.NumberColumn("Rejected records", format="%d"),
        "flagged_record_share": st.column_config.NumberColumn(
            "Records flagged for review", format="percent"
        ),
        "usable_location_share": st.column_config.NumberColumn(
            "Usable US Locations", format="percent"
        ),
        "coordinates_exact_share": st.column_config.NumberColumn(
            "Exact coordinates",
            format="percent",
            help="Sites with registry geoPoint latitude/longitude (exact coordinates).",
        ),
        "coordinates_fallback_eligible_share": st.column_config.NumberColumn(
            "Approximate coordinates (eligible)",
            format="percent",
            help=(
                "Sites without geoPoint that carry a ZIP or city+state the offline "
                "Census Gazetteer fallback can use. Fallback-eligible only: actual "
                "resolution happens at query time, so this is an upper bound."
            ),
        ),
        "low_confidence_condition_share": st.column_config.NumberColumn(
            "Uncertain condition mapping", format="percent"
        ),
    },
)
st.caption(
    "Only status = success runs feed analytics. Partial runs (page-capped "
    "smoke tests) appear here for transparency but carry no silver metrics."
)

success = reliability[reliability["status"] == "success"]
if not success.empty:
    latest = success.iloc[0]
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric(
            "Latest run reconciled",
            "yes" if latest["manifest_reconciled_flag"] else "NO",
            help=(
                "Verifies that bronze raw manifests perfectly match normalized silver trial counts."
            ),
        )
        render_provenance_drawer(
            profile_id,
            metric="manifest_reconciled",
            key="rel_reconciled",
            label="How this number is computed: Latest run reconciled",
            context={
                "displayed_measure": (
                    f"Latest run reconciled: verifies bronze raw manifests match normalized "
                    f"silver trial counts in run {latest['ingestion_run_id']}."
                )
            },
        )
    with col2:
        st.metric(
            "Usable U.S. location share",
            f"{latest['usable_location_share']:.1%}",
            help=(
                "Proportion of trial sites that resolve to valid 2-letter U.S. postal state codes."
            ),
        )
        render_provenance_drawer(
            profile_id,
            metric="usable_location_share",
            key="rel_location",
            label="How this number is computed: Usable U.S. location share",
            context={
                "displayed_measure": (
                    f"Usable U.S. location share ({latest['usable_location_share']:.1%}): "
                    "proportion of trial sites resolving to valid 2-letter U.S. postal state codes."
                )
            },
        )
    with col3:
        st.metric(
            "Low-confidence condition share",
            f"{latest['low_confidence_condition_share']:.1%}",
            help=(
                "Percentage of studies mapped via fallback or substring rules "
                "rather than exact taxonomy matches."
            ),
        )
        render_provenance_drawer(
            profile_id,
            metric="low_confidence_condition_share",
            key="reliability",
            label="How this number is computed: Low-confidence condition share",
            context={
                "displayed_measure": (
                    f"Low-confidence condition share "
                    f"({latest['low_confidence_condition_share']:.1%}): "
                    "percentage of studies mapped via fallback or substring rules "
                    "rather than exact taxonomy matches."
                )
            },
        )
    geo_col1, geo_col2 = st.columns(2)
    with geo_col1:
        geo_col1.metric(
            "Exact coordinates share",
            f"{latest['coordinates_exact_share']:.1%}",
            help=("Trial sites with registry geoPoint latitude/longitude (exact coordinates)."),
        )
        render_provenance_drawer(
            profile_id,
            metric="coordinates_exact_share",
            key="rel_exact_geo",
            label="How this number is computed: Exact coordinates share",
            context={
                "displayed_measure": (
                    f"Exact coordinates share ({latest['coordinates_exact_share']:.1%}): "
                    "trial sites with registry geoPoint latitude/longitude."
                )
            },
        )
    with geo_col2:
        geo_col2.metric(
            "Approximate coordinates (eligible)",
            f"{latest['coordinates_fallback_eligible_share']:.1%}",
            help=(
                "Trial sites without geoPoint that carry a ZIP or city+state the "
                "offline Census Gazetteer fallback can use (fallback-eligible, an "
                "upper bound; actual resolution happens at query time and is "
                "flagged per site as coordinate_approximate)."
            ),
        )
        render_provenance_drawer(
            profile_id,
            metric="coordinates_fallback_eligible_share",
            key="rel_approx_geo",
            label="How this number is computed: Approximate coordinates (eligible)",
            context={
                "displayed_measure": (
                    f"Approximate coordinates eligible "
                    f"({latest['coordinates_fallback_eligible_share']:.1%}): "
                    "sites without geoPoint carrying ZIP or city+state fallback fields."
                )
            },
        )

st.subheader("Known limitations")
st.markdown(
    """
- The registry API serves **current records only**; history is built from
  this project's own snapshots and deepens over time.
- Facility names are free text; site matching is best-effort.
- Site coordinates are exact only when the registry publishes them; sites
  without them use offline Census Gazetteer ZIP/city centroids, so their
  distances are approximate (shares reported above).
- Condition grouping is a deterministic, version-controlled taxonomy —
  low-confidence mappings are counted above, never hidden.
- Nothing here measures patient availability or predicts enrollment.
    """
)

guarded_footer()
