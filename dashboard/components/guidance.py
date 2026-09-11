"""Guidance and decision playbooks for Clinical Trial Intelligence.

Provides standardized indication scope banners, condition taxonomy formatting,
and actionable operational decision playbooks for clinical operations leaders,
feasibility analysts, and study planners.
"""

from __future__ import annotations

from typing import Any

import streamlit as st

# Human-readable clinical labels for ADRD condition taxonomy groups.
ADRD_CONDITION_LABELS: dict[str, str] = {
    "alzheimers_disease": "Alzheimer's Disease",
    "mild_cognitive_impairment": "Mild Cognitive Impairment (MCI)",
    "frontotemporal_dementia": "Frontotemporal Dementia (FTD)",
    "lewy_body_dementia": "Lewy Body Dementia (LBD)",
    "vascular_dementia": "Vascular Dementia (VaD)",
    "parkinsons_disease_dementia": "Parkinson's Disease Dementia (PDD)",
    "dementia_unspecified": "Dementia (Unspecified)",
    "cognitive_impairment_other": "Cognitive Symptoms (Non-Dementia)",
    "non_dementia_other": "Other Conditions",
}


def format_condition_group(group: str | Any) -> str:
    """Format raw condition group identifier into a clean clinical label.

    Args:
        group: Raw condition group string (e.g. ``'alzheimers_disease'``).

    Returns:
        str: Human-readable clinical label (e.g. ``"Alzheimer's Disease"``).
    """
    if not isinstance(group, str):
        return str(group) if group is not None else ""
    return ADRD_CONDITION_LABELS.get(group, group.replace("_", " ").title())


def render_indication_banner() -> None:
    """Render the active indication scope banner.

    Provides transparent visibility into the therapeutic area scope, included
    diagnostic sub-groups, and unique operational realities of Alzheimer's
    Disease & Related Dementias (ADRD) trials.
    """
    with st.container():
        st.markdown(
            """
            <div style="
                border: 1px solid rgba(59, 130, 246, 0.25);
                border-radius: 8px;
                padding: 12px 16px;
                margin-bottom: 16px;
                background-color: rgba(59, 130, 246, 0.05);
            ">
                <div style="display: flex; justify-content: space-between; align-items: center;">
                    <div>
                        <span style="
                            font-size: 0.75rem;
                            font-weight: 700;
                            text-transform: uppercase;
                            letter-spacing: 0.05em;
                            color: #3b82f6;
                        ">Active Indication Profile</span>
                        <h4 style="margin: 2px 0 0 0; font-size: 1.05rem;">
                            🧠 Alzheimer's Disease & Related Dementias (ADRD)
                        </h4>
                    </div>
                    <div style="text-align: right;">
                        <span style="
                            font-size: 0.75rem;
                            background: rgba(59, 130, 246, 0.15);
                            color: #2563eb;
                            padding: 3px 8px;
                            border-radius: 12px;
                            font-weight: 600;
                        ">Interventional U.S. Trials</span>
                    </div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        with st.expander(
            "🔍 Indication Scope, Taxonomy & Clinical Validity Details", expanded=False
        ):
            st.markdown(
                "**Scope & Diagnostic Boundaries:**\n"
                "- **Primary Coverage:** Interventional clinical trials registered on "
                "ClinicalTrials.gov with U.S. study sites investigating treatments for "
                "Alzheimer's Disease and related neurodegenerative dementias.\n"
                "- **Mapped Conditions:** Categorized deterministically via version-controlled "
                "rule matching (`config/condition_taxonomy.yml`) into:\n"
                "  - *Alzheimer's Disease (AD):* Early, mild, moderate, severe AD and "
                "AD dementia.\n"
                "  - *Mild Cognitive Impairment (MCI):* Prodromal AD and amnestic MCI.\n"
                "  - *Frontotemporal Dementia (FTD):* Behavioral variant FTD, Primary "
                "Progressive Aphasia (PPA), Pick's disease.\n"
                "  - *Lewy Body Dementia (LBD):* Dementia with Lewy bodies.\n"
                "  - *Vascular Dementia (VaD):* Vascular cognitive impairment and "
                "multi-infarct dementia.\n"
                "  - *Parkinson's Disease Dementia (PDD):* Dementia secondary to Parkinson's.\n"
                "- **Conservative Boundary:** General cognitive complaints without an explicit "
                "dementia etiology (`cognitive_impairment_other`) are isolated to avoid "
                "false-positive inflation.\n\n"
                "**Unique Operational Realities of ADRD Clinical Trials:**\n"
                "- **Biomarker Gating & Screen Failures:** Modern ADRD protocols require "
                "confirmed amyloid-beta and tau pathology via amyloid PET imaging, CSF "
                "p-tau/Aβ42, or plasma p-tau217 biomarkers, yielding 50% to 70%+ screen failure.\n"
                "- **Caregiver / Study Partner Burden:** Inclusion criteria universally require "
                "a designated study partner spending ≥10 hours/week with the participant "
                "to attend all clinic visits and complete functional rating scales (CDR-SB).\n"
                "- **Memory Clinic Saturation:** Specialized medical centers possessing PET "
                "scanners, infusion suites, and certified psychometric raters (ADAS-Cog, MMSE) "
                "are routinely approached by multiple competing sponsor protocols.\n"
                "- **Extended Trial Duration:** Phase 2 and 3 disease-modifying trials run "
                "for 18 to 24+ months, keeping investigative sites and cohorts committed."
            )


# Comprehensive decision playbooks for each dashboard page
PAGE_PLAYBOOKS: dict[str, dict[str, Any]] = {
    "overview": {
        "title": "📖 Executive Overview: 3-Step Feasibility Framework",
        "what_it_shows": (
            "Aggregated metrics and high-level priority segments across all tracked "
            "interventional ADRD trials in the United States. Identifies total active studies, "
            "actively recruiting protocols, and listed clinical trial facilities."
        ),
        "how_to_analyze": (
            "Use this overview as an operational health check. Compare total trials against "
            "the 'Currently recruiting' count to gauge protocol turnover, and review the "
            "top-ranked segments to spot immediate regional contention."
        ),
        "playbook": [
            (
                "**Step 1: Identify Opportunity & Risk (Priority Queue):** Filter by target "
                "phase and disease sub-group to pinpoint states where high trial density "
                "or rapid growth warrants thorough protocol differentiation."
            ),
            (
                "**Step 2: Assess Site Congestion (Competition Landscape & Site Overlap):** "
                "Cross-examine candidate states to check whether trial volume is dominated "
                "by a single sponsor (high HHI) or whether memory centers carry 4+ trials."
            ),
            (
                "**Step 3: Benchmark Protocol Design (Trial Similarity & Explorer):** Evaluate "
                "direct competitors with matching phases and study designs to assess eligibility "
                "barriers before finalizing site selection lists."
            ),
        ],
        "adrd_context": (
            "Because Phase 3 ADRD trials typically span 18–24 months of treatment, active sites "
            "remain occupied far longer than in acute indications. High recruiting volume in a "
            "state indicates fierce competition for qualified memory-clinic trial coordinators."
        ),
    },
    "priority_queue": {
        "title": "📖 Priority Queue Playbook: Eliminating Feasibility Guesswork",
        "what_it_shows": (
            "Ranks condition × state × phase segments using a transparent, multi-factor "
            "feasibility review priority score (0.0 to 1.0). Each segment summarizes recruiting "
            "trial volume, recent growth momentum, sponsor concentration (HHI), site overlap, "
            "and data confidence."
        ),
        "how_to_analyze": (
            "The priority score is a relative ranking index, not an enrollment prediction. "
            "Segments in the **Priority Review** band (top 20th percentile) exhibit elevated trial "
            "density or rapid growth; segments in **Review** (50th–80th percentile) represent "
            "moderate activity; segments in **Watch** (bottom 50th percentile) "
            "represent low density."
        ),
        "playbook": [
            (
                "**Priority Review Segments (Top 20%):** Do NOT automatically eliminate these "
                "regions; instead, conduct deep-dive feasibility. Check if competing trials "
                "share identical biomarker entry criteria (e.g. early AD MMSE 22–30 vs mild-to-"
                "moderate MMSE 16–26)."
            ),
            (
                "**Review Segments (Moderate):** Often the most productive expansion targets—"
                "sufficient patient population and clinical infrastructure with manageable overlap."
            ),
            (
                "**Watch Segments (Emerging / Low-Density):** Ideal for expanding geographic "
                "diversity and recruiting treatment-naive subjects through regional community "
                "neurology networks, provided adequate diagnostic infrastructure is accessible."
            ),
            (
                "**Component Inspection:** Click any row in the table to inspect unweighted "
                "and weighted factor contributions, verifying whether high priority is driven "
                "by raw trial count, rapid growth, or heavy sponsor concentration."
            ),
        ],
        "adrd_context": (
            "In Alzheimer's trials, sponsor concentration (HHI) matters intensely: when a single "
            "sponsor holds the majority of trial sites in a state, local PIs and cognitive raters "
            "are often committed to that sponsor's global Phase 3 program."
        ),
    },
    "competition_landscape": {
        "title": "📖 Competition Landscape: The 4-Quadrant Strategy Matrix",
        "what_it_shows": (
            "Scatter plot and segment table plotting Recruiting Trial Density (percentile) "
            "against Sponsor Concentration (Herfindahl-Hirschman Index, HHI). Bubble sizes "
            "represent listed trial facilities in that segment."
        ),
        "how_to_analyze": (
            "The combination of trial density and sponsor concentration reveals distinct "
            "competitive environments across the 4 quadrants of the scatter plot."
        ),
        "playbook": [
            (
                "**Quadrant 1: High Density + High HHI (Dominant Cluster):** A single sponsor "
                "leads multiple trials. Entering this market requires clear protocol "
                "differentiation (subcutaneous vs IV infusion, novel mechanism) or targeting "
                "independent sites."
            ),
            (
                "**Quadrant 2: High Density + Low HHI (Fierce Competition):** Fragmented "
                "landscape with many sponsors competing for the same patient pool. Expect site "
                "initiation delays, coordinator turnover, and slower enrollment velocity."
            ),
            (
                "**Quadrant 3: Low Density + High HHI (Pioneer Territory):** A focused sponsor "
                "program is operating with minimal external rivals. Indicates untapped patient "
                "demand that can be activated with investigator partnerships."
            ),
            (
                "**Quadrant 4: Low Density + Low HHI (Greenfield Frontier):** Minimal trial "
                "activity across the board. Ideal for teams seeking uncontested community memory "
                "centers, though local referral networks must be verified."
            ),
        ],
        "adrd_context": (
            "In dementia protocols, fragmented competition (Quadrant 2) is particularly "
            "challenging because memory clinics have limited capacity to administer lengthy "
            "psychometric batteries (CDR, ADAS-Cog, RBANS) across competing trials simultaneously."
        ),
    },
    "geography_trends": {
        "title": "📖 Geography Trends Playbook: Geographic Optimization & Saturation",
        "what_it_shows": (
            "U.S. choropleth map and regional activity distribution displaying recruiting trial "
            "listings across states for the selected condition group, alongside monthly trends."
        ),
        "how_to_analyze": (
            "Analyze state-level concentration to evaluate geographic diversification. Heavy "
            "clustering in California, Florida, Texas, and the Northeast corridor reflects "
            "memory center density, but also peak competition for eligible subjects."
        ),
        "playbook": [
            (
                "**Balance Academic vs Community Geography:** Counteract academic site "
                "congestion by identifying neighboring states with strong healthcare networks "
                "but lower trial density."
            ),
            (
                "**Evaluate Trial Posting Velocity:** Rising monthly listings in a state signal "
                "an incoming wave of active protocols that will compete for clinical coordinators."
            ),
            (
                "**Audit Registry Date Fallback:** If the trend chart displays a proxy banner, "
                "recognize that historical baseline is still accumulating from weekly snapshots."
            ),
        ],
        "adrd_context": (
            "Patients with cognitive impairment frequently depend on family caregivers for "
            "transportation. Geographic proximity to infusion centers and imaging facilities is "
            "a major determinant of study retention."
        ),
    },
    "site_overlap": {
        "title": "📖 Site Overlap Playbook: Institutional Fatigue vs Site Capacity",
        "what_it_shows": (
            "Facilities listed on multiple recruiting trials within the same indication. "
            "Identifies experienced clinical trial sites and flags institutional congestion "
            "where multiple protocols share the same investigative facility."
        ),
        "how_to_analyze": (
            "Multi-trial facility listings present a dual signal: high experience versus "
            "potential capacity strain. A medical center listed on 5+ recruiting ADRD trials "
            "possesses proven infrastructure, but its investigators may face protocol conflicts."
        ),
        "playbook": [
            (
                "**The 'Proven Site' Advantage:** Facilities with 2–4 trials have demonstrated "
                "GCP compliance, established referral pipelines, and dedicated clinical research "
                "staff."
            ),
            (
                "**The 'Institutional Fatigue' Warning:** When a site is listed on 5+ trials, "
                "ask direct operational questions during feasibility outreach: Does the PI "
                "have dedicated coordinators? Are there competing inclusion/exclusion criteria?"
            ),
            (
                "**Best-Effort Normalization:** Registry facility names are entered free-text. "
                "Treat multi-trial listings as institutional signals, and verify current "
                "investigator allocation during confidential feasibility assessments."
            ),
        ],
        "adrd_context": (
            "Memory centers often designate primary investigators across disease stages (one PI "
            "for preclinical AD, another for moderate AD). Site overlap does not always mean a "
            "single PI is overloaded, but shared infusion chairs and PET scanners "
            "remain bottlenecks."
        ),
    },
    "sponsor_landscape": {
        "title": "📖 Sponsor Landscape: Strategic Benchmarking",
        "what_it_shows": (
            "Lead sponsors ranked by count of actively recruiting interventional trials, broken "
            "down by registry sponsor class (Industry / Commercial vs Other / NIH / Academic)."
        ),
        "how_to_analyze": (
            "Differentiate commercial drug development momentum from academic/consortium research. "
            "Identify which pharmaceutical leaders dominate active trial volume."
        ),
        "playbook": [
            (
                "**Track Competitor Phase Pipelines:** Check whether commercial rivals cluster in "
                "Phase 2 proof-of-concept or broad Phase 3 confirmatory trials."
            ),
            (
                "**Leverage Academic Consortia (ADCS / ACTC):** Academic and NIH-funded networks "
                "frequently pioneer standardized cognitive assessment frameworks and validated "
                "site networks that commercial sponsors can learn from."
            ),
            (
                "**Competitive Positioning:** Assess whether target investigative sites are "
                "heavily aligned with top commercial sponsors or open to emerging "
                "biotech protocols."
            ),
        ],
        "adrd_context": (
            "Following FDA approvals of anti-amyloid monoclonal antibodies, the sponsor landscape "
            "in ADRD is undergoing rapid evolution from monotherapy anti-amyloid studies to "
            "combination therapies, tau targeting, neuroinflammation, and novel "
            "delivery modalities."
        ),
    },
    "data_reliability": {
        "title": "📖 Data Reliability & Scenario Modeling: Risk-Informed Budgeting",
        "what_it_shows": (
            "Pipeline ingestion audit trail, bronze→silver→gold data reconciliation metrics, and "
            "an interactive clinical trial delay cost calculator. Sliders adjust study burn rate "
            "and site startup assumptions to test financial exposure."
        ),
        "how_to_analyze": (
            "Use the ingestion audit logs to verify that only fully successful, validated weekly "
            "snapshots feed analytics. Use the scenario model to replace arbitrary feasibility "
            "budget guesswork with structured low/base/high sensitivity projections."
        ),
        "playbook": [
            (
                "**Quantify Delay Costs:** A 2-to-3 month recruitment lag in a Phase 3 trial "
                "commonly translates to $100k–$500k+ in unbudgeted team burn and site expenses."
            ),
            (
                "**Model Site Startup Velocity:** Use the scenario sliders to test the financial "
                "impact of adding 5 backup sites versus absorbing a 3-month enrollment extension."
            ),
            (
                "**Audit Traceability:** Review the run reconciliation table to verify zero data "
                "loss across bronze API responses, silver relational normalization, and gold marts."
            ),
        ],
        "adrd_context": (
            "Because cognitive decline endpoints require fixed-duration follow-up (e.g. 76 weeks), "
            "an enrollment delay directly extends database lock and NDA/BLA submission timelines. "
            "Feasibility sensitivity modeling is critical for securing adequate "
            "contingency budgets."
        ),
    },
    "trial_explorer": {
        "title": "📖 Trial Explorer Playbook: Protocol Deep-Dive & Direct Verification",
        "what_it_shows": (
            "Comprehensive, searchable registry records for individual clinical trials. Enables "
            "filtering by overall status, phase, indication profile, and text search across "
            "titles, sponsors, and NCT IDs with clickable links to official registry records."
        ),
        "how_to_analyze": (
            "Use Trial Explorer to transition from aggregate geographic signals to specific "
            "protocol details. Examine planned enrollment counts, phase definitions, and "
            "official registry documentation."
        ),
        "playbook": [
            (
                "**Protocol Intelligence Search:** Search by competitor name or drug class to "
                "identify where rivals are currently placing their study sites."
            ),
            (
                "**Verify Inclusion Criteria on Registry:** Click 'View on ClinicalTrials.gov' "
                "to inspect full inclusion/exclusion criteria, cognitive score cutoffs "
                "(MoCA/MMSE), and biomarker requirements."
            ),
            (
                "**Planned Enrollment vs Actual Accrual:** Remember that registry enrollment "
                "reflects the sponsor's target sample size, not observed subject enrollment."
            ),
        ],
        "adrd_context": (
            "When designing an ADRD protocol, search for completed Phase 2 trials to examine why "
            "studies terminated early (e.g., slow accrual, safety signals, lack of efficacy) "
            "and avoid repeating overly restrictive eligibility criteria."
        ),
    },
    "trial_similarity": {
        "title": "📖 Trial Similarity Playbook: Structural Protocol Benchmarking",
        "what_it_shows": (
            "Pairwise similarity scores (0.0 to 1.0) benchmarking a selected index trial "
            "against all other trials in the warehouse across 7 structural protocol factors: "
            "Condition, Phase, Study Type, Allocation, Intervention Model, Masking, and Purpose."
        ),
        "how_to_analyze": (
            "A composite similarity score ≥ 0.80 indicates an almost structurally identical "
            "clinical trial design. These trials compete for the exact same patient persona, "
            "clinical raters, and investigative center infrastructure."
        ),
        "playbook": [
            (
                "**Identify Direct Protocol Competitors:** Select your trial (or a reference "
                "Phase 3 standard) to reveal the top 10 closest design matches in the warehouse."
            ),
            (
                "**Inspect Factor Contributions:** Review the factor breakdown table to identify "
                "where designs diverge (Randomized vs Non-randomized, Double-blind vs Open-label, "
                "Parallel vs Crossover)."
            ),
            (
                "**Mitigate Site Selection Conflict:** Cross-reference the site locations of your "
                "closest similarity matches before site qualification visits to ensure non-"
                "overlapping target clinics."
            ),
        ],
        "adrd_context": (
            "In Alzheimer's disease, trials sharing Phase 3, Parallel Assignment, Quadruple "
            "Masking, and Treatment Purpose compete directly for early AD patients with positive "
            "amyloid biomarker confirmation. Benchmarking protocol similarity is the gold "
            "standard for avoiding direct head-to-head feasibility clashes."
        ),
    },
}


def render_page_guide(page_key: str) -> None:
    """Render the contextual decision playbook for the specified dashboard page.

    Args:
        page_key: Identifier of the page (e.g. ``'priority_queue'``, ``'site_overlap'``).
    """
    guide = PAGE_PLAYBOOKS.get(page_key)
    if not guide:
        return

    with st.expander(guide["title"], expanded=False):
        st.markdown(f"**What This Data Shows:**\n{guide['what_it_shows']}")
        st.markdown(f"**How to Analyze the Data:**\n{guide['how_to_analyze']}")

        st.markdown("**Operational Decision Playbook (Eliminating Guesswork):**")
        for item in guide["playbook"]:
            st.markdown(f"- {item}")

        st.markdown(f"**🧠 ADRD Clinical Feasibility Nuances:**\n{guide['adrd_context']}")

        st.caption(
            "🛡️ **Guardrail Notice:** Signals are computed strictly from public registry listings "
            "to prioritize human feasibility investigation. They are not automated predictions of "
            "enrollment velocity, site quality, or clinical efficacy."
        )
