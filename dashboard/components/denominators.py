"""Denominator captions for rate charts and tables (Issue #71).

Intent: one place that turns `mart_condition_state_rates` rows into the sentence
every chart or table must carry, so a rate is never shown without saying what it
is divided by.

Limits: the helper only writes text. Wiring it into existing pages is left to the
page work that adopts this mart.
"""

from __future__ import annotations

import pandas as pd

NO_BURDEN = "no burden denominator available"


def denominator_caption(rows: pd.DataFrame) -> str:
    """Caption naming the population and burden denominators behind `rows`.

    Contract:
    - `rows` is a slice of `mart_condition_state_rates` (one condition node).
    - Always names the population source and vintage.
    - Says "no burden denominator available" unless every row has
      `denominator_available`; it never substitutes a raw trial count.
    - An empty slice yields the no-denominator caption, not an exception.
    """
    if rows.empty:
        return f"Denominator: {NO_BURDEN}."
    population = str(rows["population_source"].iloc[0])
    covered = bool(rows["denominator_available"].all())
    burden = str(rows["denominator_label"].iloc[0]) if covered else NO_BURDEN
    note = ""
    if not covered and rows["denominator_note"].notna().any():
        note = f" {rows['denominator_note'].dropna().iloc[0]}"
    suffix = (
        "" if covered else ". Rates per 100k residents measure population-adjusted access, not need"
    )
    return f"Per 100,000 residents: {population}. Burden: {burden}{suffix}.{note}"
