"""Raw metric mathematical invariants after removal of derived priority scores."""

from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]


def test_sponsor_hhi_math():
    """HHI of shares {0.5, 0.25, 0.25} = 0.375; single sponsor = 1.0."""
    hhi = duckdb.sql(
        """
        select sum(power(cnt / total, 2))
        from (
            select cnt, sum(cnt) over () as total
            from (values (2.0), (1.0), (1.0)) t(cnt)
        )
        """
    ).fetchone()[0]
    assert abs(hhi - 0.375) < 1e-9
    solo = duckdb.sql("select power(3.0 / 3.0, 2)").fetchone()[0]
    assert solo == 1.0


def test_mart_feasibility_priority_queue_metrics_and_column_bounds(
    fixture_project_root: Path,
) -> None:
    """Verify that mart_feasibility_priority_queue has no score/band columns,
    and all raw metrics (HHI, site overlap) lie within [0.0, 1.0].
    """
    db_path = fixture_project_root / "data" / "warehouse" / "clinical_trials.duckdb"
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        cols = [
            r[0]
            for r in con.execute(
                "select column_name from information_schema.columns "
                "where table_schema = 'main_marts' "
                "and table_name = 'mart_feasibility_priority_queue'"
            ).fetchall()
        ]
        # Assert no score, priority_band, or priority_rank column names
        for col in cols:
            assert "score" not in col.lower()
            assert "priority_band" not in col.lower()
            assert "priority_rank" not in col.lower()

        # Assert bounds on raw metrics and equality of alias
        rows = con.execute(
            """
            select
                sponsor_hhi,
                site_overlap_rate,
                site_overlap_share,
                active_recruiting_trials,
                recruiting_trial_count
            from main_marts.mart_feasibility_priority_queue
            """
        ).fetchall()
        assert rows, "mart_feasibility_priority_queue must contain rows"
        for hhi, overlap_rate, overlap_share, active_recruiting, recruiting_count in rows:
            assert 0.0 <= hhi <= 1.0, f"sponsor_hhi out of bounds: {hhi}"
            assert 0.0 <= overlap_rate <= 1.0, f"site_overlap_rate out of bounds: {overlap_rate}"
            assert 0.0 <= overlap_share <= 1.0, f"site_overlap_share out of bounds: {overlap_share}"
            assert active_recruiting == recruiting_count, (
                f"active_recruiting_trials ({active_recruiting}) != "
                f"recruiting_trial_count ({recruiting_count})"
            )
    finally:
        con.close()
