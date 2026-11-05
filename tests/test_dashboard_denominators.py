"""Captions state their denominator and never fall back to raw counts (Issue #71)."""

import pandas as pd

from dashboard.components.denominators import NO_BURDEN, denominator_caption

POP = "U.S. Census Bureau ACS 2020-2024 5-year (table B01001)"


def _frame(available: list[bool], note: str | None = None) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "population_source": [POP] * len(available),
            "denominator_available": available,
            "denominator_label": [
                "Per 1,000 estimated cases: lung (SEER, 2019-2023)" if a else NO_BURDEN
                for a in available
            ],
            "denominator_note": [note] * len(available),
        }
    )


def test_caption_cites_population_and_burden_when_available() -> None:
    caption = denominator_caption(_frame([True, True]))
    assert POP in caption
    assert "Per 1,000 estimated cases: lung (SEER, 2019-2023)" in caption
    assert NO_BURDEN not in caption


def test_caption_says_no_burden_denominator_when_any_state_lacks_one() -> None:
    caption = denominator_caption(_frame([True, False], note="Self-reported proxy."))
    assert NO_BURDEN in caption
    assert "not need" in caption
    assert "Self-reported proxy." in caption
    assert "SEER" not in caption


def test_empty_slice_does_not_raise() -> None:
    assert NO_BURDEN in denominator_caption(_frame([]))
