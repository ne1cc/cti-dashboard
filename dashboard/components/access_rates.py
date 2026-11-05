"""Pick the rate and denominator caption for the access-rates page.

The rule this file enforces: a view that asks for burden-adjusted access but has no
burden denominator never degrades to raw counts. It falls back to the
population-adjusted rate and says "no burden denominator available".
"""

from __future__ import annotations

import pandas as pd

NO_BURDEN = "no burden denominator available"
PER_100K = "recruiting_trials_per_100k"
PER_1K_CASES = "recruiting_trials_per_1k_est_cases"


def _population_caption(frame: pd.DataFrame) -> str:
    vintage = (
        frame["population_vintage"].dropna().iloc[0]
        if frame["population_vintage"].notna().any()
        else None
    )
    return f"per 100,000 residents ({vintage or 'no population estimate'})"


def rate_view(frame: pd.DataFrame, per_case: bool) -> tuple[pd.DataFrame, str, str]:
    """Return (rows to show, rate column, denominator caption) for one condition.

    Args:
        frame: Rows of ``mart_condition_access_rates`` for one profile and condition.
        per_case: True when the user asked for the per-estimated-case rate.
    """
    population_caption = _population_caption(frame)
    covered = frame[frame["burden_denominator_available"]]
    if per_case and not covered.empty:
        first = covered.iloc[0]
        omitted = len(frame) - len(covered)
        note = f" {omitted} state(s) have {NO_BURDEN} and are omitted." if omitted else ""
        caption = (
            f"Rate per 1,000 estimated cases: {first['burden_measure']} "
            f"({first['burden_source']}, {first['burden_vintage']}).{note}"
        )
        return covered, PER_1K_CASES, caption
    if per_case:
        return (
            frame,
            PER_100K,
            f"{NO_BURDEN.capitalize()}: showing population-adjusted access only, "
            f"{population_caption}.",
        )
    return frame, PER_100K, f"Rate {population_caption}."
