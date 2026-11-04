"""Density-proxy falsification test on a tiny hand-built in-memory warehouse."""

from __future__ import annotations

import duckdb

from src.analysis.density_proxy_test import run_test

DAYS = ("2026-09-01", "2026-09-08")


def _con(days: tuple[str, ...]) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    con.execute(
        "create table fct_trial_snapshot (nct_id varchar, indication_profile_id varchar, "
        "snapshot_date date, overall_status varchar, phase_normalized varchar)"
    )
    con.execute(
        "create table fct_trial_site (nct_id varchar, indication_profile_id varchar, "
        "snapshot_date date, state_normalized varchar)"
    )
    con.execute(
        "create table bridge_trial_condition (nct_id varchar, indication_profile_id varchar, "
        "condition_group varchar)"
    )
    con.execute(
        "create table mart_recruiting_competition (indication_profile_id varchar, "
        "snapshot_date date, condition_group varchar, state_normalized varchar, "
        "phase_normalized varchar, recruiting_trial_count integer)"
    )
    for d in days:
        con.execute(
            "insert into fct_trial_snapshot values ('NCT0', 'p', ?, 'RECRUITING', 'P2')", [d]
        )
    return con


def test_single_snapshot_is_inconclusive() -> None:
    assert "fewer than 2 snapshots" in run_test(_con(DAYS[:1]))


def test_bins_report_correct_rates() -> None:
    con = _con(DAYS)
    # State S1 (low density) / S2 (mid) / S3 (high); 30 trials each, all RECRUITING at t.
    # Terminal exits at t+1: low 3, mid 6, high 15; the rest stay RECRUITING.
    exits = {"S1": 3, "S2": 6, "S3": 15}
    for st, count in zip(("S1", "S2", "S3"), (1, 2, 3), strict=True):
        con.execute(
            "insert into mart_recruiting_competition values ('p', ?, 'g', ?, 'P2', ?)",
            [DAYS[0], st, count],
        )
        for i in range(30):
            nct = f"{st}-{i}"
            nxt = "TERMINATED" if i < exits[st] else "RECRUITING"
            con.execute("insert into bridge_trial_condition values (?, 'p', 'g')", [nct])
            for d, status in zip(DAYS, ("RECRUITING", nxt), strict=True):
                con.execute(
                    "insert into fct_trial_snapshot values (?, 'p', ?, ?, 'P2')", [nct, d, status]
                )
            con.execute("insert into fct_trial_site values (?, 'p', ?, ?)", [nct, DAYS[0], st])
    out = run_test(con)
    assert "3 (10.0%)" in out and "15 (50.0%)" in out
    assert "27 (90.0%)" in out and "15 (50.0%)" in out
    assert "+40.0%" in out
    assert "INCONCLUSIVE" not in out


def test_small_bins_flagged() -> None:
    con = _con(DAYS)
    for st, count in zip(("S1", "S2", "S3"), (1, 2, 3), strict=True):
        con.execute(
            "insert into mart_recruiting_competition values ('p', ?, 'g', ?, 'P2', ?)",
            [DAYS[0], st, count],
        )
    con.execute("insert into bridge_trial_condition values ('NCT0', 'p', 'g')")
    con.execute("insert into fct_trial_site values ('NCT0', 'p', ?, 'S1')", [DAYS[0]])
    assert "bins too small" in run_test(con)
