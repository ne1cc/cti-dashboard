"""Data Reliability — run confidence, reconciliation, and scenario explorer."""

import streamlit as st
from components import data
from components.audit_panel import render_metric_audit
from components.guardrails import guarded_footer, page_setup
from components.guidance import render_page_guide
from components.profile import render_profile_selector

from src.analysis.roi_scenarios import compute_scenarios, load_roi_config

page_setup("Data Reliability & Assumptions")
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
            "coordinates_fallback_share",
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
        "coordinates_fallback_share": st.column_config.NumberColumn(
            "Approximate coordinates",
            format="percent",
            help=(
                "Sites without geoPoint that resolve to an offline Census Gazetteer "
                "ZIP or city centroid; distances from these sites are approximate."
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
    col1.metric(
        "Latest run reconciled",
        "yes" if latest["manifest_reconciled_flag"] else "NO",
        help="Verifies that bronze raw manifests perfectly match normalized silver trial counts.",
    )
    col2.metric(
        "Usable U.S. location share",
        f"{latest['usable_location_share']:.1%}",
        help="Proportion of trial sites that resolve to valid 2-letter U.S. postal state codes.",
    )
    col3.metric(
        "Low-confidence condition share",
        f"{latest['low_confidence_condition_share']:.1%}",
        help=(
            "Percentage of studies mapped via fallback or substring rules "
            "rather than exact taxonomy matches."
        ),
    )
    geo_col1, geo_col2 = st.columns(2)
    geo_col1.metric(
        "Exact coordinates share",
        f"{latest['coordinates_exact_share']:.1%}",
        help=("Trial sites with registry geoPoint latitude/longitude (exact coordinates)."),
    )
    geo_col2.metric(
        "Approximate coordinates share",
        f"{latest['coordinates_fallback_share']:.1%}",
        help=(
            "Trial sites without geoPoint that resolve to an offline Census Gazetteer "
            "ZIP or city centroid; distances from these sites are approximate."
        ),
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

st.divider()
st.subheader("Illustrative scenario explorer")

config = load_roi_config()
st.warning(config.disclaimer.strip())

with st.expander("Adjust assumptions (session only — file stays unchanged)"):
    a = config.assumptions
    a.cost_per_feasibility_review = st.number_input(
        "Cost per feasibility review (USD)",
        min_value=0.0,
        value=float(a.cost_per_feasibility_review),
        step=250.0,
    )
    a.deprioritized_review_share = st.slider(
        "Assumed share of reviews deprioritized by triage",
        0.0,
        1.0,
        float(a.deprioritized_review_share),
    )
    a.cost_per_underperforming_site_activation = st.number_input(
        "Cost per under-enrolling site activation (USD)",
        min_value=0.0,
        value=float(a.cost_per_underperforming_site_activation),
        step=1000.0,
    )
    a.activation_decisions_influenced_share = st.slider(
        "Assumed share of activation decisions influenced",
        0.0,
        1.0,
        float(a.activation_decisions_influenced_share),
    )
    config.reviews_per_cycle = st.number_input(
        "Feasibility reviews per planning cycle",
        min_value=0.0,
        value=float(config.reviews_per_cycle),
        step=5.0,
    )

results = compute_scenarios(config)
st.dataframe(
    [
        {
            "Scenario": r.label,
            "Reviews deprioritized (assumed)": r.reviews_deprioritized,
            "Review-effort value (USD)": r.illustrative_review_effort_value,
            "Activations influenced (assumed)": r.activations_influenced,
            "Activation value (USD)": r.illustrative_activation_value,
            "Total, illustrative (USD)": r.illustrative_total_value,
        }
        for r in results
    ],
    hide_index=True,
    width="content",
    column_config={
        "Reviews deprioritized (assumed)": st.column_config.NumberColumn(format="%.1f"),
        "Activations influenced (assumed)": st.column_config.NumberColumn(format="%.1f"),
        "Review-effort value (USD)": st.column_config.NumberColumn(format="dollar"),
        "Activation value (USD)": st.column_config.NumberColumn(format="dollar"),
        "Total, illustrative (USD)": st.column_config.NumberColumn(format="dollar"),
    },
)
st.caption(
    "Every figure above is the product of the editable assumptions in "
    "config/roi_assumptions.yml — no observed outcomes are used."
)

render_metric_audit(
    profile_id,
    metric="latest_study",
    context={
        "scope": (
            "Current studies; ingestion run diagnostics and scenario "
            "assumptions above have separate denominators."
        )
    },
    key="reliability",
)

guarded_footer()
