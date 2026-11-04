"""Trial Transparency — early stopped trials and results posting rates."""

from __future__ import annotations

import math

import pandas as pd
import plotly.express as px
import streamlit as st
from components import data
from components.audit_panel import render_metric_audit
from components.guardrails import guarded_footer, page_setup
from components.guidance import format_condition_group, render_page_guide
from components.profile import render_profile_selector

page_setup("Trial Transparency")
profile_id = render_profile_selector()
data.require_warehouse(profile_id)

render_page_guide("trial_transparency", profile_id)


def _parse_nct_ids(val: pd.Series | str | None) -> set[str]:
    """Extract distinct NCT IDs from a string or series of comma-delimited NCT strings."""
    ncts: set[str] = set()
    if isinstance(val, pd.Series):
        for item in val.dropna():
            for nct in str(item).split(","):
                cleaned = nct.strip()
                if cleaned:
                    ncts.add(cleaned)
    elif isinstance(val, str):
        for nct in val.split(","):
            cleaned = nct.strip()
            if cleaned:
                ncts.add(cleaned)
    return ncts


stopped_df = data.early_stop_transparency(profile_id)
results_df = data.results_posting_transparency(profile_id)

tab_stopped, tab_results = st.tabs(
    ["Early-stopped trials (reported reasons)", "Results-posting rates by sponsor class"]
)

with tab_stopped:
    st.subheader("Early-stopped trials & sponsor-stated reasons")
    st.caption(
        "Registry listings with overall status TERMINATED, WITHDRAWN, or SUSPENDED. "
        "All stop reasons are recorded verbatim from the registry "
        "(`statusModule.whyStopped`) — never categorized into 'recruitment failure' "
        "or quality judgments."
    )

    if stopped_df.empty:
        st.info("No terminated, withdrawn, or suspended trials recorded for this profile.")
    else:
        total_stopped_ncts = _parse_nct_ids(stopped_df["contributing_nct_ids"])
        total_stopped_trials = len(total_stopped_ncts)
        with_reason_df = stopped_df[
            stopped_df["why_stopped"].notna() & (stopped_df["why_stopped"].str.strip() != "")
        ]
        with_reason_ncts = _parse_nct_ids(with_reason_df["contributing_nct_ids"])
        with_reason_count = len(with_reason_ncts)
        no_reason_count = total_stopped_trials - with_reason_count

        kpi1, kpi2, kpi3 = st.columns(3)
        kpi1.metric(
            "Early-stopped listings",
            f"{total_stopped_trials:,}",
            help=(
                "Total distinct terminated, withdrawn, and suspended "
                "registry records in this profile."
            ),
        )
        kpi2.metric(
            "With reported reason",
            f"{with_reason_count:,}",
            help="Distinct trials where the sponsor supplied a whyStopped reason in the registry.",
        )
        kpi3.metric(
            "No reason reported",
            f"{no_reason_count:,}",
            help="Distinct trials where whyStopped was left blank or null in the public registry.",
        )

        # Filters
        filter_col1, filter_col2 = st.columns(2)
        classes = sorted(stopped_df["sponsor_class"].dropna().unique().tolist())
        selected_classes = filter_col1.multiselect(
            "Filter by Sponsor Class",
            options=classes,
            default=classes,
            format_func=lambda c: str(c).replace("_", " ").title(),
            key="stopped_class_filter",
        )
        statuses = sorted(stopped_df["overall_status"].dropna().unique().tolist())
        selected_statuses = filter_col2.multiselect(
            "Filter by Status",
            options=statuses,
            default=statuses,
            key="stopped_status_filter",
        )

        filtered_stopped = stopped_df.copy()
        if selected_classes:
            filtered_stopped = filtered_stopped[
                filtered_stopped["sponsor_class"].isin(selected_classes)
            ]
        if selected_statuses:
            filtered_stopped = filtered_stopped[
                filtered_stopped["overall_status"].isin(selected_statuses)
            ]

        # Visualization
        if not filtered_stopped.empty:
            chart_rows = []
            for (s_class, status), sub_df in filtered_stopped.groupby(
                ["sponsor_class", "overall_status"]
            ):
                distinct_ncts = _parse_nct_ids(sub_df["contributing_nct_ids"])
                chart_rows.append(
                    {
                        "sponsor_class": s_class,
                        "overall_status": status,
                        "Trials": len(distinct_ncts),
                    }
                )
            agg_chart = pd.DataFrame(chart_rows)
            agg_chart["Sponsor Class"] = (
                agg_chart["sponsor_class"].str.replace("_", " ").str.title()
            )
            fig = px.bar(
                agg_chart,
                x="Sponsor Class",
                y="Trials",
                color="overall_status",
                barmode="group",
                labels={"overall_status": "Status"},
                height=340,
            )
            st.plotly_chart(fig, width="stretch")

        # Table display
        display_stopped = filtered_stopped.copy()
        display_stopped["display_condition"] = display_stopped["condition_group"].map(
            lambda cg: format_condition_group(cg, profile_id)
        )
        display_stopped["display_sponsor_class"] = (
            display_stopped["sponsor_class"].str.replace("_", " ").str.title()
        )
        display_stopped["display_why_stopped"] = display_stopped["why_stopped"].fillna(
            "(No reason reported)"
        )

        st.dataframe(
            display_stopped,
            column_order=[
                "indication_profile_id",
                "display_condition",
                "display_sponsor_class",
                "stopped_year",
                "overall_status",
                "display_why_stopped",
                "stopped_trial_count",
                "contributing_nct_ids",
            ],
            hide_index=True,
            width="content",
            column_config={
                "indication_profile_id": None,
                "display_condition": st.column_config.TextColumn("Condition Group", width="medium"),
                "display_sponsor_class": st.column_config.TextColumn(
                    "Sponsor Class", width="small"
                ),
                "stopped_year": st.column_config.NumberColumn("Year", format="%d", width="small"),
                "overall_status": st.column_config.TextColumn("Status", width="small"),
                "display_why_stopped": st.column_config.TextColumn(
                    "Sponsor-reported reason (verbatim)", width="large"
                ),
                "stopped_trial_count": st.column_config.NumberColumn("Trials", format="%d"),
                "contributing_nct_ids": st.column_config.TextColumn(
                    "Contributing NCT IDs", width="medium"
                ),
            },
        )

        st.caption(
            "Every count connects to contributing NCT IDs above. "
            "Note: Breakdown by condition group is non-additive across condition groups "
            "because individual trials may map to multiple condition groups. "
            "Profile-level KPIs and sponsor-class totals count distinct trials. "
            "Public records reflect sponsor reporting on ClinicalTrials.gov."
        )


with tab_results:
    st.subheader("Results-posting transparency & reporting rates")

    with st.expander(
        "Methodology, conservative heuristic & legal applicability limits", expanded=False
    ):
        st.markdown(
            "**Heuristic definition:**\n"
            "- **Evaluable trials denominator ($n$):** Completed or terminated study protocols "
            "whose Primary Completion Date (PCD) was more than 12 months before the latest "
            "captured snapshot date.\n"
            "- **Results posted ($x$):** Studies with `hasResults = true` on ClinicalTrials.gov.\n"
            "- **Results overdue:** Studies past primary completion by >12 months with no results "
            "posted as of the snapshot date.\n"
            "- **Confidence intervals:** 95% two-sided Wilson score confidence intervals "
            "$[\\text{CI}_\\text{lower}, \\text{CI}_\\text{upper}]$ are reported for all rates.\n"
            "- **Small-sample ranking exclusion:** Sponsors with fewer than 5 evaluable trials "
            "($n < 5$) are marked as **Unranked** to prevent deceptive small-sample percentages "
            "from distorting comparisons.\n\n"
            "**Legal and statutory compliance boundaries:**\n"
            "> **Important:** Results overdue status is an **informational reporting benchmark** "
            "(>12 months post-primary completion), **NOT** a legal determination of non-compliance "
            "under FDAAA 801 or 42 CFR Part 11.\n"
            ">\n"
            '> Legal applicability (e.g. determining whether a protocol is an "Applicable Clinical '
            'Trial" [ACT]) **cannot be derived from public registry fields**. While the registry '
            "provides self-reported oversight module fields (`isFdaRegulatedDrug`, "
            "`isFdaRegulatedDevice`), these are unverified and partial. Determining ACT status "
            "requires non-public information such as Investigational New Drug (IND) or "
            "Investigational Device Exemption (IDE) numbers, product manufacturing/export "
            "certificates under FDC Act § 802, and statutory certificates of confidentiality or "
            "exemption waivers. Observational studies and Phase 1 drug trials are not ACTs under "
            "FDAAA 801."
        )

    if results_df.empty:
        st.info("No completed or results-eligible trials found for this profile.")
    else:
        total_completed = int(results_df["total_completed_trials"].sum())
        total_evaluable = int(results_df["total_evaluable_trials"].sum())
        total_posted = int(results_df["results_posted_evaluable_count"].sum())
        total_overdue = int(results_df["results_overdue_count"].sum())

        overall_rate = (total_posted / total_evaluable) if total_evaluable > 0 else 0.0

        r_col1, r_col2, r_col3, r_col4 = st.columns(4)
        r_col1.metric("Completed protocols", f"{total_completed:,}")
        r_col2.metric(
            "Evaluable (>12m post-PCD)",
            f"{total_evaluable:,}",
            help="Denominator: Completed/terminated trials >12 months past primary completion.",
        )
        r_col3.metric(
            "Results posted",
            f"{total_posted:,} ({overall_rate:.1%})",
            help="Evaluable trials with results posted on ClinicalTrials.gov.",
        )
        r_col4.metric(
            "No results posted as of snapshot",
            f"{total_overdue:,}",
            help="Evaluable trials with no posted results (>12 months past PCD).",
        )

        st.markdown("#### Results posting by sponsor class")

        # Roll-up by sponsor class
        class_summary_rows = []
        for s_class, group in results_df.groupby("sponsor_class"):
            n = int(group["total_evaluable_trials"].sum())
            x = int(group["results_posted_evaluable_count"].sum())
            overdue = int(group["results_overdue_count"].sum())
            rate = (x / n) if n > 0 else None

            # Wilson CI
            ci_l: float | None = None
            ci_u: float | None = None
            if n > 0:
                z = 1.96
                denom = n + z * z
                center = (x + z * z / 2.0) / denom
                margin = z * math.sqrt((x * (n - x) / n if n > 0 else 0) + (z * z / 4.0)) / denom
                ci_l = max(0.0, center - margin)
                ci_u = min(1.0, center + margin)

            evaluable_ncts = sorted(_parse_nct_ids(group["evaluable_nct_ids"]))
            posted_ncts = sorted(_parse_nct_ids(group["posted_nct_ids"]))
            overdue_ncts = sorted(_parse_nct_ids(group["overdue_nct_ids"]))

            class_summary_rows.append(
                {
                    "indication_profile_id": profile_id,
                    "sponsor_class": str(s_class).replace("_", " ").title(),
                    "total_evaluable_trials": n,
                    "evaluable_nct_ids": ", ".join(evaluable_ncts) if evaluable_ncts else None,
                    "results_posted_count": x,
                    "posted_nct_ids": ", ".join(posted_ncts) if posted_ncts else None,
                    "results_overdue_count": overdue,
                    "overdue_nct_ids": ", ".join(overdue_ncts) if overdue_ncts else None,
                    "results_posted_rate": rate,
                    "ci_lower": ci_l,
                    "ci_upper": ci_u,
                    "ci_range": (
                        f"{ci_l:.1%} – {ci_u:.1%}" if ci_l is not None and ci_u is not None else "—"
                    ),
                }
            )

        class_summary_df = pd.DataFrame(class_summary_rows).sort_values(
            "total_evaluable_trials", ascending=False
        )

        st.dataframe(
            class_summary_df,
            column_order=[
                "indication_profile_id",
                "sponsor_class",
                "total_evaluable_trials",
                "evaluable_nct_ids",
                "results_posted_count",
                "posted_nct_ids",
                "results_overdue_count",
                "overdue_nct_ids",
                "results_posted_rate",
                "ci_range",
            ],
            hide_index=True,
            width="content",
            column_config={
                "indication_profile_id": None,
                "sponsor_class": st.column_config.TextColumn("Sponsor Class", width="medium"),
                "total_evaluable_trials": st.column_config.NumberColumn(
                    "Evaluable (n)", format="%d", help="Denominator: PCD >12 months ago"
                ),
                "evaluable_nct_ids": st.column_config.TextColumn(
                    "Evaluable NCT IDs", width="medium"
                ),
                "results_posted_count": st.column_config.NumberColumn(
                    "Results Posted (x)", format="%d"
                ),
                "posted_nct_ids": st.column_config.TextColumn("Posted NCT IDs", width="medium"),
                "results_overdue_count": st.column_config.NumberColumn("Overdue", format="%d"),
                "overdue_nct_ids": st.column_config.TextColumn("Overdue NCT IDs", width="medium"),
                "results_posted_rate": st.column_config.NumberColumn(
                    "Posting Rate", format="percent"
                ),
                "ci_range": st.column_config.TextColumn("95% Wilson CI", width="medium"),
            },
        )

        st.markdown("#### Lead sponsor reporting transparency")

        # Separate ranked (n >= 5) and unranked (n < 5)
        evaluable_sponsors = results_df.copy()
        evaluable_sponsors["display_sponsor_class"] = (
            evaluable_sponsors["sponsor_class"].str.replace("_", " ").str.title()
        )
        evaluable_sponsors["ci_range"] = evaluable_sponsors.apply(
            lambda r: (
                f"{r['ci_lower']:.1%} – {r['ci_upper']:.1%}"
                if pd.notna(r["ci_lower"]) and pd.notna(r["ci_upper"])
                else "—"
            ),
            axis=1,
        )

        ranked = evaluable_sponsors[evaluable_sponsors["is_ranked"]].copy()
        unranked = evaluable_sponsors[~evaluable_sponsors["is_ranked"]].copy()

        st.markdown(
            f"**Ranked sponsors ($n \\ge 5$ evaluable trials, {len(ranked)} organizations)**"
        )
        if ranked.empty:
            st.info("No lead sponsors have 5 or more evaluable trials in this profile.")
        else:
            ranked = ranked.sort_values(
                ["results_posted_rate", "total_evaluable_trials"], ascending=[False, False]
            )
            st.dataframe(
                ranked,
                column_order=[
                    "indication_profile_id",
                    "lead_sponsor",
                    "display_sponsor_class",
                    "total_evaluable_trials",
                    "evaluable_nct_ids",
                    "results_posted_evaluable_count",
                    "posted_nct_ids",
                    "results_overdue_count",
                    "overdue_nct_ids",
                    "results_posted_rate",
                    "ci_range",
                ],
                hide_index=True,
                width="content",
                column_config={
                    "indication_profile_id": None,
                    "lead_sponsor": st.column_config.TextColumn("Lead Sponsor", width="large"),
                    "display_sponsor_class": st.column_config.TextColumn("Class", width="small"),
                    "total_evaluable_trials": st.column_config.NumberColumn(
                        "Evaluable (n)", format="%d"
                    ),
                    "evaluable_nct_ids": st.column_config.TextColumn(
                        "Evaluable NCT IDs", width="medium"
                    ),
                    "results_posted_evaluable_count": st.column_config.NumberColumn(
                        "Posted (x)", format="%d"
                    ),
                    "posted_nct_ids": st.column_config.TextColumn("Posted NCT IDs", width="medium"),
                    "results_overdue_count": st.column_config.NumberColumn("Overdue", format="%d"),
                    "overdue_nct_ids": st.column_config.TextColumn(
                        "Overdue NCT IDs", width="medium"
                    ),
                    "results_posted_rate": st.column_config.NumberColumn(
                        "Posting Rate", format="percent"
                    ),
                    "ci_range": st.column_config.TextColumn("95% Wilson CI", width="medium"),
                },
            )

        with st.expander(
            f"Unranked sponsors with small sample sizes (n < 5, {len(unranked)} organizations)",
            expanded=False,
        ):
            st.caption(
                "Sponsors with fewer than 5 evaluable trials are displayed unranked. "
                "Small denominators generate wide uncertainty intervals and cannot support "
                "meaningful comparative rankings."
            )
            if not unranked.empty:
                st.dataframe(
                    unranked.sort_values("total_evaluable_trials", ascending=False),
                    column_order=[
                        "indication_profile_id",
                        "lead_sponsor",
                        "display_sponsor_class",
                        "total_evaluable_trials",
                        "evaluable_nct_ids",
                        "results_posted_evaluable_count",
                        "posted_nct_ids",
                        "results_overdue_count",
                        "overdue_nct_ids",
                        "results_posted_rate",
                        "ci_range",
                    ],
                    hide_index=True,
                    width="content",
                    column_config={
                        "indication_profile_id": None,
                        "lead_sponsor": st.column_config.TextColumn("Lead Sponsor", width="large"),
                        "display_sponsor_class": st.column_config.TextColumn(
                            "Class", width="small"
                        ),
                        "total_evaluable_trials": st.column_config.NumberColumn(
                            "Evaluable (n)", format="%d"
                        ),
                        "evaluable_nct_ids": st.column_config.TextColumn(
                            "Evaluable NCT IDs", width="medium"
                        ),
                        "results_posted_evaluable_count": st.column_config.NumberColumn(
                            "Posted", format="%d"
                        ),
                        "posted_nct_ids": st.column_config.TextColumn(
                            "Posted NCT IDs", width="medium"
                        ),
                        "results_overdue_count": st.column_config.NumberColumn(
                            "Overdue", format="%d"
                        ),
                        "overdue_nct_ids": st.column_config.TextColumn(
                            "Overdue NCT IDs", width="medium"
                        ),
                        "results_posted_rate": st.column_config.NumberColumn(
                            "Posting Rate", format="percent"
                        ),
                        "ci_range": st.column_config.TextColumn("95% Wilson CI", width="medium"),
                    },
                )


render_metric_audit(profile_id, metric="latest_study", key="transparency")

guarded_footer()
