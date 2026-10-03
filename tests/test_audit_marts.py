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
