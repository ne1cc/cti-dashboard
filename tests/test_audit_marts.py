"""Audit contracts tested against isolated, adversarial source snapshots."""

import json
from datetime import date, datetime
from pathlib import Path

from src.utils.hashing import sha256_json
from tests.conftest import AUDIT_FAILED_RUN_ID, AUDIT_RUN2_ID, FIXTURE_RUN_ID
from tests.test_dbt_fixture_build import _rows

STUDIES = "main_marts.mart_study_snapshot_audit"
LOCATIONS = "main_marts.mart_location_snapshot_audit"


def test_successful_snapshot_grains_and_profile_isolation(audit_fixture_root: Path) -> None:
    assert _rows(
        audit_fixture_root, f"select count(*), count(distinct study_audit_key) from {STUDIES}"
    ) == [(30, 30)]
    assert _rows(
        audit_fixture_root,
        f"select indication_profile_id, count(*) from {STUDIES} group by 1 order by 1",
    ) == [("adrd", 20), ("oncology_nsclc", 10)]
    assert _rows(
        audit_fixture_root,
        f"select count(*) from {STUDIES} where snapshot_id = '{AUDIT_FAILED_RUN_ID}'",
    ) == [(0,)]
    assert _rows(
        audit_fixture_root, f"select count(*) from {STUDIES} where snapshot_id = '{AUDIT_RUN2_ID}'"
    ) == [(10,)]
    assert _rows(
        audit_fixture_root,
        f"select count(*) from {LOCATIONS} l anti join {STUDIES} s using (study_audit_key)",
    ) == [(0,)]
    assert _rows(
        audit_fixture_root, f"select count(*) = count(distinct location_audit_key) from {LOCATIONS}"
    ) == [(True,)]


def test_exact_source_lineage_and_nullable_legacy_retrieval(audit_fixture_root: Path) -> None:
    records = _rows(
        audit_fixture_root,
        f"select nct_id, raw_page_reference, raw_study_ordinal, source_json_hash, "
        f"retrieved_at_utc from {STUDIES} where snapshot_id = '{FIXTURE_RUN_ID}' order "
        f"by nct_id",
    )
    bronze = audit_fixture_root / "data/bronze/adrd/api_responses"
    for nct_id, reference, ordinal, content_hash, retrieved in records:
        raw = json.loads((bronze / reference).read_text())["studies"][ordinal]
        assert raw["protocolSection"]["identificationModule"]["nctId"] == nct_id
        assert content_hash == sha256_json(raw)
        assert retrieved == datetime(2026, 9, 1, 10, 1)
    assert _rows(
        audit_fixture_root,
        f"select count(*) from {STUDIES} where snapshot_id = '{AUDIT_RUN2_ID}' and "
        f"retrieved_at_utc is null",
    ) == [(10,)]


def test_posting_clock_and_verification_precision(audit_fixture_root: Path) -> None:
    assert _rows(
        audit_fixture_root,
        f"select nct_id, posted_update_age_days, older_posted_update_flag, "
        f"status_verified_date_raw, verification_date_precision, "
        f"verification_age_months from {STUDIES} where snapshot_id = "
        f"'{FIXTURE_RUN_ID}' and nct_id in ('NCT00000001','NCT00000002','NCT00000003') "
        f"order by nct_id",
    ) == [
        ("NCT00000001", 180, False, "2026-03", "month", 6),
        ("NCT00000002", 181, True, "bad", "unparseable", None),
        ("NCT00000003", None, None, None, "missing", None),
    ]
    assert _rows(
        audit_fixture_root,
        f"select snapshot_started_at_utc, snapshot_ended_at_utc from {STUDIES} where "
        f"snapshot_id = '{FIXTURE_RUN_ID}' limit 1",
    ) == [(datetime(2026, 9, 1, 10), datetime(2026, 9, 1, 10, 30))]


def test_locations_exclusions_and_enrollment_categories(audit_fixture_root: Path) -> None:
    assert _rows(
        audit_fixture_root,
        f"select location_ordinal, usable_geography_flag, missing_facility_flag, "
        f"location_status from {LOCATIONS} where snapshot_id = '{FIXTURE_RUN_ID}' and "
        f"nct_id = 'NCT00000001' order by 1",
    ) == [(0, True, False, "COMPLETED"), (1, True, False, "RECRUITING"), (2, True, True, None)]
    assert _rows(
        audit_fixture_root,
        f"select nct_id, confirmed_recruiting_flag, geography_category, "
        f"state_count_eligible_flag, state_count_exclusion_reason, enrollment_category "
        f"from {STUDIES} where snapshot_id = '{FIXTURE_RUN_ID}' and nct_id <= "
        f"'NCT00000004' order by 1",
    ) == [
        ("NCT00000001", True, "usable", True, None, "estimated"),
        ("NCT00000002", False, "missing", False, "not_confirmed_recruiting", "actual"),
        ("NCT00000003", False, "missing", False, "not_confirmed_recruiting", "missing"),
        ("NCT00000004", False, "unsupported", False, "not_confirmed_recruiting", "unknown_type"),
    ]
    assert _rows(
        audit_fixture_root,
        f"select enrollment_count from {STUDIES} where snapshot_id = "
        f"'{FIXTURE_RUN_ID}' and nct_id = 'NCT00000001'",
    ) == [(101,)]
    assert _rows(
        audit_fixture_root,
        f"select sum(enrollment_count) from {STUDIES} s where snapshot_id = "
        f"'{FIXTURE_RUN_ID}' and exists (select 1 from {LOCATIONS} l where "
        f"l.study_audit_key = s.study_audit_key and usable_geography_flag)",
    ) == _rows(
        audit_fixture_root,
        f"select sum(enrollment_count) from {STUDIES} where snapshot_id = "
        f"'{FIXTURE_RUN_ID}' and geography_category = 'usable'",
    )


def test_partial_invalid_dates_and_recruiting_geography_exclusions(
    audit_fixture_root: Path,
) -> None:
    assert _rows(
        audit_fixture_root,
        f"select nct_id, study_type, last_update_post_date_raw, posted_update_date, "
        f"verification_date_precision, verification_age_months from {STUDIES} where "
        f"snapshot_id = '{FIXTURE_RUN_ID}' and nct_id in ('NCT00000005','NCT00000006') "
        f"order by 1",
    ) == [
        ("NCT00000005", None, "2026-03", None, "year", None),
        ("NCT00000006", "INTERVENTIONAL", "invalid", None, "unparseable", None),
    ]
    assert _rows(
        audit_fixture_root,
        f"select nct_id, state_count_eligible_flag, state_count_exclusion_reason from "
        f"{STUDIES} where snapshot_id = '{FIXTURE_RUN_ID}' and nct_id in "
        f"('NCT00000009','NCT00000010') order by 1",
    ) == [
        ("NCT00000009", False, "missing_geography"),
        ("NCT00000010", False, "unsupported_geography"),
    ]
    assert _rows(
        audit_fixture_root,
        f"select enrollment_category from {STUDIES} where snapshot_id = "
        f"'{FIXTURE_RUN_ID}' and nct_id = 'NCT00000007'",
    ) == [("unknown_type",)]


def test_location_ordinals_resolve_source_rows(audit_fixture_root: Path) -> None:
    bronze = audit_fixture_root / "data/bronze/adrd/api_responses"
    for reference, study_ordinal, location_ordinal, facility, country, status in _rows(
        audit_fixture_root,
        f"select raw_page_reference, raw_study_ordinal, location_ordinal, "
        f"facility_name, country, location_status from {LOCATIONS} where snapshot_id = "
        f"'{FIXTURE_RUN_ID}'",
    ):
        raw = json.loads((bronze / reference).read_text())["studies"][study_ordinal]
        location = raw["protocolSection"]["contactsLocationsModule"]["locations"][location_ordinal]
        assert (facility, country, status) == (
            location.get("facility"),
            location.get("country"),
            location.get("status"),
        )


def test_snapshot_utc_date_does_not_roll_back_to_local_day(audit_fixture_root: Path) -> None:
    # The isolated dbt materialization runs in a pinned non-UTC session.
    assert "TimeZone: America/Los_Angeles" in (audit_fixture_root / "profiles.yml").read_text()
    assert _rows(
        audit_fixture_root,
        "select cast(timezone('America/Los_Angeles', "
        "timestamptz '2026-09-01 00:15:00+00') as date)",
    ) == [(date(2026, 8, 31),)]
    # Audit and staging below must still agree on the explicit UTC day.
    assert _rows(
        audit_fixture_root,
        f"select snapshot_date, snapshot_started_at_utc, snapshot_ended_at_utc "
        f"from {STUDIES} where snapshot_id = '{AUDIT_RUN2_ID}' limit 1",
    ) == [(date(2026, 9, 1), datetime(2026, 9, 1, 0, 15), datetime(2026, 9, 1, 0, 45))]

    assert _rows(
        audit_fixture_root,
        "select snapshot_date, snapshot_timestamp_utc from main_staging.stg_trials "
        f"where snapshot_id = '{AUDIT_RUN2_ID}' limit 1",
    ) == [(date(2026, 9, 1), datetime(2026, 9, 1, 0, 15))]


def test_first_post_source_clock_is_inspectable(audit_fixture_root):
    assert _rows(
        audit_fixture_root,
        f"select study_first_post_date_raw, study_first_post_date from {STUDIES} "
        f"where snapshot_id = '{FIXTURE_RUN_ID}' and nct_id = 'NCT00000001'",
    ) == [("2025-03-15", date(2025, 3, 15))]


def _norm_cell(value: object) -> object:
    return value if value is None or isinstance(value, bool | int | float | str) else str(value)


def test_registry_audit_and_reliability_baseline_parity(tmp_path: Path) -> None:
    """Phase 4b canonical reliability & audit marts match #146 baseline for ADRD & NSCLC."""
    from dataclasses import replace

    import duckdb

    from tests.fixture_warehouse import base_adrd_run, base_nsclc_run, build_fixture_warehouse
    from tests.mart_baseline import cells_match, load_baseline

    baseline = load_baseline()
    cases = [
        ("adrd", {"id": "D000544", "term": "Alzheimer Disease"}),
        ("oncology_nsclc", {"id": "D002289", "term": "Carcinoma, Non-Small-Cell Lung"}),
    ]
    for preset_id, mesh_entry in cases:
        base = base_adrd_run() if preset_id == "adrd" else base_nsclc_run()
        mesh = {"conditionBrowseModule": {"meshes": [mesh_entry]}}
        run = replace(
            base,
            profile="full_catalog",
            condition="",
            study_overrides={f"NCT{i:08d}": {"derivedSection": mesh} for i in range(1, 11)},
        )
        root = tmp_path / f"parity_4b_{preset_id}"
        build_fixture_warehouse(root, runs=[run])
        warehouse = root / "data/warehouse/clinical_trials.duckdb"
        with duckdb.connect(str(warehouse), read_only=True) as con:
            # 1. Reliability parity (preset denominator + source-wide totals)
            rel_base = baseline["mart_data_reliability"]
            rel_cols = rel_base["columns"]
            p_idx = rel_cols.index("indication_profile_id")
            expected_rel = [r for r in rel_base["rows"] if r[p_idx] == preset_id]
            assert len(expected_rel) == 1
            expected_row = dict(zip(rel_cols, expected_rel[0], strict=True))

            cur = con.execute(
                "select * from main_marts.mart_registry_data_reliability where preset_id = ?",
                [preset_id],
            )
            reg_cols = [d[0] for d in cur.description]
            reg_rows = cur.fetchall()
            assert len(reg_rows) == 1
            actual_row = {c: _norm_cell(v) for c, v in zip(reg_cols, reg_rows[0], strict=True)}
            assert actual_row["membership_scope"] == "preset"
            assert actual_row["source_profile"] == "full_catalog"
            assert actual_row["condition"] == ""
            # Source-wide totals equal preset totals here because all 10 studies carry preset MeSH
            assert actual_row["source_trial_row_count"] == actual_row["trial_row_count"] == 10
            assert actual_row["source_distinct_trial_count"] == actual_row["distinct_trial_count"]
            assert actual_row["source_location_row_count"] == actual_row["location_row_count"] == 16
            rel_skip = {"indication_profile_id", "condition"}
            for col in rel_cols:
                if col in rel_skip:
                    continue
                assert cells_match(expected_row[col], actual_row[col]), (
                    f"{preset_id} reliability {col}: {expected_row[col]!r} != {actual_row[col]!r}"
                )

            # 2. Study snapshot audit parity
            study_base = baseline["mart_study_snapshot_audit"]
            s_cols = study_base["columns"]
            sp_idx = s_cols.index("indication_profile_id")
            s_skip = {"study_audit_key", "indication_profile_id", "source_json_hash"}
            s_compare_idx = [i for i, c in enumerate(s_cols) if c not in s_skip]

            def s_sort(row: list[object], idx: list[int] = s_compare_idx) -> str:
                return json.dumps([row[i] for i in idx], sort_keys=True)

            want_studies = sorted(
                (r for r in study_base["rows"] if r[sp_idx] == preset_id),
                key=s_sort,
            )
            s_proj = [
                (
                    f"'{preset_id}' as indication_profile_id"
                    if c == "indication_profile_id"
                    else f"s.{c}"
                )
                for c in s_cols
            ]
            got_studies = sorted(
                (
                    [_norm_cell(v) for v in row]
                    for row in con.execute(
                        f"select {', '.join(s_proj)} "
                        "from main_marts.mart_registry_study_snapshot_audit s "
                        "inner join main_marts.bridge_preset_trial b "
                        "on b.snapshot_id = s.snapshot_id and b.nct_id = s.nct_id "
                        f"and b.preset_id = '{preset_id}'"
                    ).fetchall()
                ),
                key=s_sort,
            )
            assert len(got_studies) == len(want_studies) == 10
            for want, got in zip(want_studies, got_studies, strict=True):
                for col, w, g in zip(s_cols, want, got, strict=True):
                    if col in s_skip:
                        continue
                    assert cells_match(w, g), f"{preset_id} study_audit {col}: {w!r} != {g!r}"

            # 3. Location snapshot audit parity
            loc_base = baseline["mart_location_snapshot_audit"]
            l_cols = loc_base["columns"]
            lp_idx = l_cols.index("indication_profile_id")
            l_skip = {
                "location_audit_key",
                "study_audit_key",
                "indication_profile_id",
                "source_json_hash",
            }
            l_compare_idx = [i for i, c in enumerate(l_cols) if c not in l_skip]

            def l_sort(row: list[object], idx: list[int] = l_compare_idx) -> str:
                return json.dumps([row[i] for i in idx], sort_keys=True)

            want_locs = sorted(
                (r for r in loc_base["rows"] if r[lp_idx] == preset_id),
                key=l_sort,
            )
            l_proj = [
                (
                    f"'{preset_id}' as indication_profile_id"
                    if c == "indication_profile_id"
                    else f"l.{c}"
                )
                for c in l_cols
            ]
            got_locs = sorted(
                (
                    [_norm_cell(v) for v in row]
                    for row in con.execute(
                        f"select {', '.join(l_proj)} "
                        "from main_marts.mart_registry_location_snapshot_audit l "
                        "inner join main_marts.bridge_preset_trial b "
                        "on b.snapshot_id = l.snapshot_id and b.nct_id = l.nct_id "
                        f"and b.preset_id = '{preset_id}'"
                    ).fetchall()
                ),
                key=l_sort,
            )
            assert len(got_locs) == len(want_locs) == 16
            for want, got in zip(want_locs, got_locs, strict=True):
                for col, w, g in zip(l_cols, want, got, strict=True):
                    if col in l_skip:
                        continue
                    assert cells_match(w, g), f"{preset_id} loc_audit {col}: {w!r} != {g!r}"


def test_registry_reliability_and_audit_preset_denominators_and_unassigned(
    registry_preset_project_root: Path,
) -> None:
    """Canonical reliability & audit marts separate preset denominators from source totals."""
    import duckdb

    warehouse = registry_preset_project_root / "data/warehouse/clinical_trials.duckdb"
    with duckdb.connect(str(warehouse), read_only=True) as con:
        # Profile-only pull is excluded from canonical reliability & audit marts
        assert (
            con.execute(
                "select count(*) from main_marts.mart_registry_data_reliability "
                "where ingestion_run_id = '20260922T100000Z_profile_only'"
            ).fetchone()[0]
            == 0
        )
        assert (
            con.execute(
                "select count(*) from main_marts.mart_registry_study_snapshot_audit "
                "where nct_id = 'NCT00000095' or snapshot_id = '20260922T100000Z_profile_only'"
            ).fetchone()[0]
            == 0
        )
        # Canonical study audit keeps 1 row per (snapshot_id, nct_id) with no window functions
        assert con.execute(
            "select count(*) = count(distinct study_audit_key), "
            "count(*) = count(distinct (snapshot_id, nct_id)) "
            "from main_marts.mart_registry_study_snapshot_audit"
        ).fetchone() == (True, True)
        assert con.execute(
            "select count(*) = count(distinct location_audit_key) "
            "from main_marts.mart_registry_location_snapshot_audit"
        ).fetchone() == (True,)

        # Latest canonical run exposes adrd, shared_preset, and explicit unassigned rows
        latest_rel = con.execute(
            "select preset_id, membership_scope, source_trial_row_count, "
            "source_distinct_trial_count, trial_row_count, distinct_trial_count, "
            "source_location_row_count, location_row_count "
            "from main_marts.mart_registry_data_reliability "
            "where ingestion_run_id = '20260915T100000Z_shared2' order by preset_id"
        ).fetchall()
        assert latest_rel == [
            ("adrd", "preset", 10, 10, 2, 2, 16, 3),
            ("shared_preset", "preset", 10, 10, 2, 2, 16, 3),
            ("unassigned", "unassigned", 10, 10, 8, 8, 16, 13),
        ]
        # No profile column on canonical study/location audit tables
        for tbl in (
            "mart_registry_study_snapshot_audit",
            "mart_registry_location_snapshot_audit",
        ):
            cols = {r[0] for r in con.execute(f"describe main_marts.{tbl}").fetchall()}
            assert "indication_profile_id" not in cols
            assert "registry_trial_key" in cols

    # Verify the compiled reliability trend analysis query has no window functions and valid columns
    trend_sql = (
        Path(__file__).resolve().parents[1]
        / "dbt_clinical_trials/analyses/analysis_registry_data_reliability_trend.sql"
    ).read_text(encoding="utf-8")
    assert "mart_registry_data_reliability" in trend_sql
    assert "over (" not in trend_sql.lower()
