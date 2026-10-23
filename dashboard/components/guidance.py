"""Guidance and contextual data interpretation for Clinical Trial Intelligence.

Provides standardized indication scope banners, dynamic condition taxonomy formatting,
and neutral, three-field guidance for clinical operations leaders, feasibility analysts,
and study planners.
"""

from __future__ import annotations

from typing import Any

import streamlit as st


def format_condition_group(group: str | Any, profile_id: str | None = None) -> str:
    """Format raw condition group identifier into a clean clinical label.

    Resolves human-readable display names dynamically from the active indication
    profile's taxonomy, falling back to clean title-casing.

    Args:
        group: Raw condition group string (e.g. ``'condition_group'``).
        profile_id: Active indication profile ID used to load the taxonomy.

    Returns:
        str: Human-readable clinical label.
    """
    if not isinstance(group, str):
        return str(group) if group is not None else ""

    if profile_id:
        try:
            from src.profiles import get_registry

            reg = get_registry()
            prof = reg.get(profile_id)
            if prof and prof.taxonomy:
                label = prof.taxonomy.get_group_label(group)
                if label != group:
                    return label
                for tg in prof.taxonomy.groups:
                    if tg.condition_group == group and tg.display_name:
                        return tg.display_name
                if prof.taxonomy.default_group == group and prof.taxonomy.default_display_name:
                    return prof.taxonomy.default_display_name
        except Exception:
            pass

    return group.replace("_", " ").title()


def render_indication_banner(profile_id: str | None = None) -> None:
    """Render the active indication scope banner.

    Provides transparent visibility into the therapeutic area scope, included
    diagnostic sub-groups, and data source boundaries.
    """
    display_title = "Clinical Trials"
    description = "Interventional U.S. clinical trials registered on ClinicalTrials.gov."

    if profile_id:
        try:
            from src.profiles import get_registry

            reg = get_registry()
            prof = reg.get(profile_id)
            if prof:
                display_title = prof.display_name
                if prof.config and prof.config.scope:
                    scope_desc = prof.config.scope.get("description")
                    if scope_desc:
                        description = scope_desc
        except Exception:
            display_title = profile_id.replace("_", " ").title()

    with st.container():
        st.markdown(
            f":gray[Active indication profile] &nbsp; **{display_title}** &nbsp; "
            ":blue-badge[Interventional U.S. trials]"
        )

        with st.expander("Indication scope, taxonomy & data boundaries"):
            st.markdown(
                f"**Scope & Diagnostic Boundaries ({display_title}):**\n"
                f"- **Primary Coverage:** {description}\n"
                "- **Data Source:** Public interventional trial records registered on "
                "ClinicalTrials.gov with U.S. study sites.\n"
                "- **Condition Categorization:** Mapped deterministically into diagnostic "
                "categories via version-controlled taxonomy rules.\n"
                "- **Boundary Notice:** Listings reflect sponsor-reported public registry "
                "data; not patient records or validated operational capacity."
            )


# Neutral three-field guidance for each dashboard page
PAGE_GUIDANCE: dict[str, dict[str, Any]] = {
    "overview": {
        "title": "Executive Overview: Feasibility Review Scope",
        "what": (
            "Aggregated metrics and high-level priority segments across tracked "
            "interventional clinical trials in the United States for the active "
            "indication profile. Shows total active studies, actively recruiting "
            "protocols, and listed clinical trial facilities."
        ),
        "why": (
            "Summarizes trial listings from the latest completed snapshot. Trial counts "
            "and facility listings are grouped by normalized state and condition taxonomy."
        ),
        "how_to_verify": (
            "Compare totals against official registry records on ClinicalTrials.gov. "
            "Signals reflect registered interventional listings and do not measure "
            "patient enrollment or clinical site capacity."
        ),
        "what_it_shows": (
            "Aggregated metrics and high-level priority segments across tracked "
            "interventional clinical trials in the United States for the active "
            "indication profile. Shows total active studies, actively recruiting "
            "protocols, and listed clinical trial facilities."
        ),
        "how_to_analyze": (
            "Summarizes trial listings from the latest completed snapshot. Trial counts "
            "and facility listings are grouped by normalized state and condition taxonomy."
        ),
    },
    "priority_queue": {
        "title": "Priority Queue: Feasibility Review Prioritization",
        "what": (
            "Ranks condition × state × phase segments using a multi-factor "
            "feasibility review priority score (0.0 to 1.0). Each segment summarizes "
            "recruiting trial volume, recent growth momentum, sponsor concentration "
            "(HHI), site overlap, and the legacy operational completeness adjustment."
        ),
        "why": (
            "Computed as a weighted composite: 0.35 recruiting count + 0.20 growth "
            "momentum + 0.20 sponsor concentration + 0.15 site overlap + 0.10 legacy "
            "operational completeness. Bands are defined by fixed score thresholds: "
            "Priority Review (≥ 0.70), Review (0.45 to 0.70), and Watch (< 0.45)."
        ),
        "how_to_verify": (
            "The score is a relative ranking index of registry activity to organize "
            "workflow review, not a predictive forecast of recruitment velocity or "
            "trial success. Inspect component values and verify site availability "
            "directly with prospective investigators."
        ),
        "what_it_shows": (
            "Ranks condition × state × phase segments using a multi-factor "
            "feasibility review priority score (0.0 to 1.0)."
        ),
        "how_to_analyze": (
            "Computed as a weighted composite of recruiting count, growth momentum, "
            "sponsor concentration, site overlap, and operational completeness."
        ),
    },
    "competition_landscape": {
        "title": "Competition Landscape: Listing Volume & Sponsor Concentration",
        "what": (
            "Plots actively recruiting trial count against sponsor concentration "
            "(Herfindahl-Hirschman Index, HHI) across condition, state, and phase "
            "segments. Points are sized by listed facilities in each segment."
        ),
        "why": (
            "HHI is calculated as the sum of squared sponsor listing shares within "
            "each segment, ranging from 0.0 (fragmented) to 1.0 (monopoly). Recruiting "
            "counts reflect studies with overall_status = 'RECRUITING'."
        ),
        "how_to_verify": (
            "A high HHI or high trial count indicates concentrated registry activity, "
            "not verified investigator exclusivity or patient saturation. Confirm "
            "actual site staffing and competing protocols during direct site qualification."
        ),
        "what_it_shows": (
            "Plots actively recruiting trial count against sponsor concentration "
            "(Herfindahl-Hirschman Index, HHI)."
        ),
        "how_to_analyze": (
            "Examines the distribution of trials and sponsor concentration across segments."
        ),
    },
    "geography_trends": {
        "title": "Geography Trends: Geographic Distribution & Posting Activity",
        "what": (
            "State-level distribution of actively recruiting clinical trial listings "
            "and historical monthly listing counts for the selected diagnostic condition group."
        ),
        "why": (
            "Choropleth map displays recruiting trial counts by normalized U.S. postal "
            "state codes. Monthly trend lines aggregate newly posted or active listings "
            "across snapshot dates."
        ),
        "how_to_verify": (
            "Registry listings indicate where sponsors have registered trial locations, "
            "not participant residence or referral draw. Verify geographic accessibility "
            "and site participation directly on ClinicalTrials.gov."
        ),
        "what_it_shows": (
            "State-level distribution of actively recruiting clinical trial listings."
        ),
        "how_to_analyze": (
            "Analyzes state-level concentrations and historical monthly posting activity."
        ),
    },
    "site_overlap": {
        "title": "Site Overlap: Multi-Trial Facility Listings",
        "what": (
            "Lists clinical trial facilities named on multiple actively recruiting "
            "protocols within the selected indication profile."
        ),
        "why": (
            "Aggregates facility records normalized by state and standardized name "
            "strings across currently recruiting study records."
        ),
        "how_to_verify": (
            "Facility names on ClinicalTrials.gov are entered as free text; string "
            "normalization is best-effort. Multi-trial listings indicate institutional "
            "research presence, not investigator workload or clinical coordinator availability."
        ),
        "what_it_shows": ("Facilities named on multiple actively recruiting protocols."),
        "how_to_analyze": ("Evaluates facility participation across concurrent study protocols."),
    },
    "sponsor_landscape": {
        "title": "Sponsor Landscape: Lead Sponsor Activity",
        "what": (
            "Lead sponsors ranked by count of actively recruiting interventional "
            "clinical trials, categorized by registry sponsor class (Industry vs "
            "Other / Academic / NIH)."
        ),
        "why": (
            "Aggregates study records by lead_sponsor and sponsor_class for studies "
            "currently in recruiting status within the selected profile."
        ),
        "how_to_verify": (
            "Listing counts reflect primary registered sponsorship and do not represent "
            "total R&D expenditure, portfolio market share, or trial enrollment speed. "
            "Verify sponsor affiliations on official registry records."
        ),
        "what_it_shows": (
            "Lead sponsors ranked by count of actively recruiting interventional clinical trials."
        ),
        "how_to_analyze": (
            "Differentiates commercial trial activity from academic and consortium research."
        ),
    },
    "data_reliability": {
        "title": "Data Reliability: Ingestion Audit & Data Integrity",
        "what": (
            "Ingestion run audit trail, data reconciliation status, and pipeline "
            "validation metrics tracking study records from bronze extraction through "
            "silver normalization and gold marts."
        ),
        "why": (
            "Reconciliation confirms row count parity, unique study identifiers, "
            "and location/condition confidence metrics across each automated pipeline "
            "snapshot run."
        ),
        "how_to_verify": (
            "Review individual run IDs and validation status flags. Only runs marked "
            "'success' feed analytical marts; partial test runs are recorded for "
            "transparency but excluded from analytics."
        ),
        "what_it_shows": (
            "Ingestion run audit trail, data reconciliation status, and pipeline metrics."
        ),
        "how_to_analyze": ("Verifies pipeline ingestion integrity and snapshot validation status."),
    },
    "trial_explorer": {
        "title": "Trial Explorer: Protocol Record Search",
        "what": (
            "Searchable, filterable registry records for individual clinical trials "
            "within the active profile, with direct links to official ClinicalTrials.gov "
            "study pages."
        ),
        "why": (
            "Queries the normalized dim_trial dimension joined with active U.S. location "
            "records from the latest successful snapshot."
        ),
        "how_to_verify": (
            "Click 'View on ClinicalTrials.gov' to inspect official protocol "
            "documentation, primary endpoints, and detailed eligibility criteria. "
            "Listed enrollment represents the sponsor's target accrual goal, "
            "not confirmed enrollment."
        ),
        "what_it_shows": (
            "Searchable, filterable registry records for individual clinical trials."
        ),
        "how_to_analyze": ("Transitions from aggregate indicators to specific protocol records."),
    },
    "trial_similarity": {
        "title": "Trial Similarity: Protocol Design Comparability",
        "what": (
            "Pairwise protocol design similarity scores (0.0 to 1.0) benchmarking a "
            "selected trial against other trials in the profile across structural "
            "design attributes."
        ),
        "why": (
            "Evaluates seven categorical protocol attributes: Phase, Study Type, "
            "Allocation, Intervention Model, Masking, Primary Purpose, and Condition "
            "Group. Scores reflect structural design alignment only."
        ),
        "how_to_verify": (
            "Similarity scores reflect shared categorical registry metadata, not "
            "therapeutic equivalence, biological mechanism, or identical patient "
            "populations. Confirm protocol compatibility by reviewing full "
            "inclusion/exclusion criteria."
        ),
        "what_it_shows": (
            "Pairwise protocol design similarity scores across structural attributes."
        ),
        "how_to_analyze": (
            "Benchmarks protocol design alignment against other trials in the warehouse."
        ),
    },
}

PAGE_PLAYBOOKS = PAGE_GUIDANCE


def render_page_guide(page_key: str, profile_id: str) -> None:
    """Render the neutral contextual guidance for the specified dashboard page.

    Args:
        page_key: Identifier of the page (e.g. ``'priority_queue'``, ``'site_overlap'``).
        profile_id: Active indication profile identifier.
    """
    guide = PAGE_GUIDANCE.get(page_key)
    if not guide:
        return

    with st.expander(guide["title"], expanded=False):
        st.markdown(f"**What This Data Shows:**\n{guide['what']}")
        st.markdown(f"**How It Is Computed:**\n{guide['why']}")
        st.markdown(f"**How to Verify & Caveats:**\n{guide['how_to_verify']}")
        st.caption(
            "**Guardrail notice:** Signals are computed strictly from public registry listings "
            "to prioritize human feasibility investigation. They are not automated predictions of "
            "enrollment velocity, site quality, or clinical efficacy."
        )
