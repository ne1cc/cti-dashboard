"""Turn mart rows into the table and caption of the participation-vs-burden page.

Wording rules (docs/clinical_interpretation_guardrails.md): describe a difference
between reported participation and disease burden; never imply intent, quotas,
causation or sponsor quality. Without a comparable burden denominator the page shows
participant composition only and says so; it never computes a ratio from a proxy.
"""

from __future__ import annotations

import pandas as pd

COMPOSITION_ONLY = "Participant composition only; no comparable burden denominator available."
SUPPRESSED = "suppressed (fewer than 3 trials)"
LOW_N = "low n (fewer than 5 trials)"


def _flag(row: pd.Series) -> str:
    if row["suppressed_flag"]:
        return SUPPRESSED
    return LOW_N if row["low_n_flag"] else ""


def _interval(row: pd.Series) -> str:
    if pd.isna(row["ratio_ci_low"]):
        return ""
    return f"{row['ratio_ci_low']:.2f} to {row['ratio_ci_high']:.2f}"


def ratio_view(frame: pd.DataFrame) -> tuple[pd.DataFrame, str]:
    """Return (display table, denominator caption) for one condition, window and dimension.

    Args:
        frame: Rows of ``mart_participation_prevalence_ratio`` for that selection.
    """
    if frame.empty:
        return frame, COMPOSITION_ONLY
    with_burden = frame[frame["burden_denominator_available"]]
    if with_burden.empty:
        caption = COMPOSITION_ONLY
    else:
        first = with_burden.iloc[0]
        caption = (
            "Ratio = share of participants / share of estimated cases. Burden: "
            f"{first['burden_measure']} ({first['burden_source']}, {first['burden_vintage']}, "
            f"{first['burden_geography']}); the vintage covers the whole table, not each window."
        )
    table = pd.DataFrame(
        {
            "Group": frame["group_label"],
            "Trials": frame["group_trial_count"],
            "Participants": frame["group_participants"],
            "Participation share": frame["participation_share"],
            "Burden share": frame["burden_share"],
            "Ratio": frame["participation_prevalence_ratio"],
            "95% interval": frame.apply(_interval, axis=1),
            "Trials reporting this variable": frame["reporting_trials_share"],
            "Flag": frame.apply(_flag, axis=1),
            "Basis": frame["comparison_basis"],
            "NCT IDs": frame["contributing_nct_ids"],
        }
    )
    return table, caption
