"""Issue #79: sponsor_hhi is null below min_sponsors_for_hhi.

A one-sponsor segment is HHI 1.0 by definition, so below the threshold the
warehouse emits no value and the dashboard shows "insufficient data
(n sponsors)". These tests pin the suppression edge on segments built with
exactly 0, 1, 2 and 3 distinct sponsors, and the acceptance criterion that
changing the var moves that edge.
"""

from pathlib import Path

import duckdb
import pytest

SEGMENT_QUERY = """
    select sponsor_count, sponsor_hhi
    from main_marts.mart_feasibility_priority_queue
    where indication_profile_id = 'adrd'
      and condition_group = 'alzheimers_disease'
      and state_normalized = ? and phase_normalized = ?
"""

ZERO_SPONSOR_QUERY = """
    select sponsor_count, sponsor_hhi
    from main_marts.mart_feasibility_priority_queue
    where indication_profile_id = 'adrd' and sponsor_count = 0
"""


def _fetch(root: Path, sql: str, params: list[str]) -> list[tuple[int, float | None]]:
    con = duckdb.connect(str(root / "data/warehouse/clinical_trials.duckdb"), read_only=True)
    try:
        return [(int(r[0]), r[1]) for r in con.execute(sql, params).fetchall()]
    finally:
        con.close()


def _segment(root: Path, state: str, phase: str) -> tuple[int, float | None]:
    rows = _fetch(root, SEGMENT_QUERY, [state, phase])
    assert len(rows) == 1, f"expected one {state}/{phase} segment, got {rows}"
    return rows[0]


def test_default_threshold_suppresses_zero_one_and_two_sponsor_segments(
    hhi_threshold_roots: dict[str, Path],
) -> None:
    root = hhi_threshold_roots["default"]
    # n = 0 named sponsors: the null lead-sponsor group contributes no distinct
    # sponsor, so there is no concentration to report.
    zero = _fetch(root, ZERO_SPONSOR_QUERY, [])
    assert zero, "fixture lost its zero-sponsor segment"
    assert all(count == 0 and hhi is None for count, hhi in zero)
    # n = 1: HHI would be 1.0 by definition -- suppressed.
    assert _segment(root, "CA", "PHASE3") == (1, None)
    # n = 2: still below the default threshold of 3.
    assert _segment(root, "NC", "PHASE3") == (2, None)
    # n = 3, the threshold edge itself: emitted, shares {1/4, 1/4, 2/4}.
    assert _segment(root, "NC", "PHASE2") == (3, pytest.approx(0.375))


def test_lowering_the_var_moves_the_suppression_edge(
    hhi_threshold_roots: dict[str, Path],
) -> None:
    root = hhi_threshold_roots["min_two"]
    # The two-sponsor segment suppressed at the default is emitted at 2.
    assert _segment(root, "NC", "PHASE3") == (2, pytest.approx(0.5))
    # The three-sponsor segment is above both thresholds and unchanged.
    assert _segment(root, "NC", "PHASE2") == (3, pytest.approx(0.375))
    # Below-threshold segments stay suppressed at any threshold above them.
    assert _segment(root, "CA", "PHASE3") == (1, None)
    zero = _fetch(root, ZERO_SPONSOR_QUERY, [])
    assert zero and all(count == 0 and hhi is None for count, hhi in zero)
