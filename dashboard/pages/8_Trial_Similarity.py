"""Trial Similarity Explorer — deterministic protocol comparability."""

from typing import Any

import pandas as pd
import streamlit as st
from components import data
from components.guardrails import guarded_footer, page_setup
from components.guidance import render_indication_banner, render_page_guide
from components.profile import render_profile_selector

FACTOR_LABELS = {
    "same_condition": "Same condition",
    "same_phase": "Same phase",
    "geography_overlap": "Geography overlap",
    "intervention_type_overlap": "Intervention type overlap",
    "study_design_match": "Study design match",
    "eligibility_compatible": "Eligibility compatible",
    "enrollment_band_match": "Enrollment band match",
}

page_setup("Trial Similarity Explorer")
profile_id = render_profile_selector()
data.require_warehouse(profile_id)

render_indication_banner(profile_id)
render_page_guide("trial_similarity")

st.info(
    "This page scores **structural trial-design comparability** — "
    "shared phase, geography, intervention type, study design, and "
    "eligibility criteria. It is not a claim of clinical equivalence, "
    "and unlike the Competition Landscape and Priority Queue pages, it "
    "is not itself a competition or recruitment signal."
)

trials = data.trial_explorer(profile_id)

search = st.text_input("Search for an index trial by NCT ID or title")

candidates = trials
if search:
    needle = search.strip().lower()
    mask = candidates["brief_title"].str.lower().str.contains(needle, na=False) | candidates[
        "nct_id"
    ].str.lower().str.contains(needle, na=False)
    candidates = candidates[mask]

if candidates.empty:
    st.warning("No trials match that search.")
    st.stop()


def _format_option(row: Any) -> str:
    """Format a candidate trial row into a human-readable dropdown selection label.

    Args:
        row: Namedtuple row from candidate trials containing ``nct_id``, ``brief_title``,
            and optional ``indication_profile_id``.

    Returns:
        str: Label formatted as ``{nct_id} — {brief_title} [{indication_profile_id}]``.
    """
    ind_tag = (
        f" [{row.indication_profile_id}]"
        if hasattr(row, "indication_profile_id") and row.indication_profile_id
        else ""
    )
    return f"{row.nct_id} — {row.brief_title}{ind_tag}"


options = {_format_option(row): row.nct_id for row in candidates.head(50).itertuples()}
selected_label = st.selectbox("Select the index trial", list(options))
selected_nct_id = options[selected_label]

sim_df = data.trial_similarity(profile_id, selected_nct_id)
if sim_df.empty or "nct_id_b" not in sim_df.columns:
    st.info("No comparable trials found in the current warehouse for this trial.")
    guarded_footer()
    st.stop()

# `indication_profile_id` is deliberately not merged in: mart_trial_similarity
# already carries it, scoped by the query below. Merging a same-named column from
# `trials` would make pandas suffix both to _x/_y and the display column would
# silently vanish from the table.
merge_cols = ["nct_id", "brief_title", "registry_url"]

matches = sim_df.merge(
    trials[merge_cols],
    left_on="nct_id_b",
    right_on="nct_id",
    how="left",
).drop(columns="nct_id")

if matches.empty:
    st.info("No comparable trials found in the current warehouse for this trial.")
    guarded_footer()
    st.stop()

st.subheader(f"Top comparable trials for {selected_nct_id}")
st.caption("Click a row to see its full factor breakdown below.")
match_columns = [
    "similarity_rank",
    "nct_id_b",
]
if "indication_profile_id" in matches.columns:
    match_columns.append("indication_profile_id")
match_columns.extend(
    [
        "brief_title",
        "registry_url",
        "similarity_score",
        "similarity_explanation",
    ]
)

col_cfg: dict = {
    "nct_id_b": st.column_config.TextColumn("NCT ID"),
    "brief_title": st.column_config.TextColumn("Brief title", width="large"),
    "registry_url": st.column_config.LinkColumn(
        "Registry record",
        help="Opens the public ClinicalTrials.gov record",
        display_text="View on ClinicalTrials.gov",
    ),
    "similarity_score": st.column_config.NumberColumn(format="%.4f"),
}
if "indication_profile_id" in match_columns:
    col_cfg["indication_profile_id"] = st.column_config.TextColumn("Indication")

match_event = st.dataframe(
    matches[match_columns],
    hide_index=True,
    width="stretch",
    column_config=col_cfg,
    on_select="rerun",
    selection_mode="single-row",
)

selected_rows = match_event.selection.rows if match_event.selection else []
if selected_rows:
    m = matches.iloc[selected_rows[0]]
    st.subheader(f"Factor breakdown — {selected_nct_id} vs {m['nct_id_b']}")
    breakdown = pd.DataFrame(
        [
            {
                "Factor": label,
                "Match (1=yes)": m[factor] if factor in m else 0,
                "Weight": m[f"weight_{factor}"] if f"weight_{factor}" in m else 0.0,
                "Weighted contribution": (
                    m[f"weighted_{factor}"] if f"weighted_{factor}" in m else 0.0
                ),
            }
            for factor, label in FACTOR_LABELS.items()
        ]
    )
    st.dataframe(breakdown, hide_index=True, width="stretch")
    sim_score = (
        m["similarity_score"]
        if "similarity_score" in m and pd.notna(m["similarity_score"])
        else 0.0
    )
    st.metric(
        "Weighted total",
        f"{float(sim_score):.4f}",
        help=(
            "similarity_score for this pair — the weighted sum of all seven "
            "factors, rounded to 4 decimals. Individual contributions are "
            "rounded first, so their displayed sum can differ in the last decimal."
        ),
    )
    if "similarity_explanation" in m and pd.notna(m["similarity_explanation"]):
        st.caption(m["similarity_explanation"])

guarded_footer()
