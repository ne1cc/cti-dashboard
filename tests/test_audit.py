"""Filter-aware audit decisions at study grain and against real metric inputs."""

import json

import pandas as pd
import pytest

from dashboard.components import audit, data


def inputs():
    studies = pd.DataFrame(
        [
            dict(
                indication_profile_id="adrd",
                snapshot_id="run",
                nct_id=nct,
                snapshot_date="2026-09-01",
                snapshot_ended_at_utc="2026-09-01T10:00:00Z",
                overall_status=status,
                phase_normalized="PHASE2",
                geography_category=geo,
                posted_update_age_days=age,
                enrollment_count=100,
                enrollment_category="estimated",
                missing_facility_location_count=1,
                status_verified_date_raw="2026-03",
                verification_date_precision="month",
                raw_page_reference="run/page=00001.json",
                raw_study_ordinal=i,
                source_json_hash="hash",
                retrieved_at_utc=None,
            )
            for i, (nct, status, geo, age) in enumerate(
                [
                    ("A", "RECRUITING", "usable", 180),
                    ("B", "RECRUITING", "missing", 181),
                    ("C", "UNKNOWN", "missing", None),
                    ("D", "RECRUITING", "usable", 181),
                    ("E", "RECRUITING", "unsupported", None),
                ]
            )
        ]
    )
    locations = pd.DataFrame(
        [
            dict(
                indication_profile_id="adrd",
                snapshot_id="run",
                nct_id=nct,
                location_ordinal=i,
                state_normalized=state,
                usable_geography_flag=nct != "E",
                country="Canada" if nct == "E" else "United States",
                us_location_flag=nct != "E",
                facility_normalized=facility,
                city_normalized="city",
                location_status="COMPLETED",
            )
            for nct, i, state, facility in [
                ("A", 0, "CA", "hospital"),
                ("A", 1, "CA", None),
                ("A", 2, "NY", "hospital"),
                ("D", 0, "TX", "other"),
                ("E", 0, None, "foreign hospital"),
            ]
        ]
    )
    conditions = studies[["indication_profile_id", "snapshot_id", "nct_id"]].assign(
        condition_group="group"
    )
    observations = studies[["indication_profile_id", "snapshot_id", "nct_id"]]
    return studies, locations, conditions, observations


def compute(**kwargs):
    studies, locations, conditions, observations = inputs()
    return audit.compute_audit(
        studies,
        locations,
        conditions,
        profile_id="adrd",
        observations=observations,
        filters=kwargs.pop("filters", {}),
        evaluation_time="2026-09-02T10:00:00Z",
        **kwargs,
    )


def test_state_denominator_and_overlapping_flags_reconcile():
    result = compute(filters={"states": ["CA"], "conditions": ["group"], "phases": []})
    assert result["summary"]["eligible_studies"] == 5
    assert result["summary"]["contributing_studies"] == 1
    assert result["summary"]["region_undetermined_studies"] == 2
    assert result["summary"]["exclusions"] == {
        "missing_geography": 1,
        "not_confirmed_recruiting": 1,
        "outside_selected_region": 1,
        "unsupported_geography": 1,
    }
    assert result["summary"]["recruiting_coverage"] == 0.25
    b = result["studies"].set_index("nct_id").loc["B"]
    assert b["older_posted_update_flag"] and b["region_membership"] == "undetermined"


def test_threshold_warn_retains_and_boundary_is_strict():
    result = compute()
    frame = result["studies"].set_index("nct_id")
    assert not frame.loc["A", "older_posted_update_flag"]
    assert frame.loc["D", "older_posted_update_flag"] and frame.loc["D", "included_flag"]
    assert not compute(update_threshold_days=181)["studies"]["older_posted_update_flag"].any()


def test_distinct_studies_and_enrollment_do_not_sum_segments_or_locations():
    result = compute(filters={"states": ["CA", "NY", "TX"]})
    assert result["summary"]["contributing_studies"] == 2
    assert result["summary"]["enrollment"]["estimated"] == {"studies": 2, "total": 200}
    assert result["contributors"] == ["A", "D"]


def test_exact_empty_filter_identity_zero_denominator_and_profile_scope():
    filters = {"conditions": ["no-match"], "states": ["CA"], "phases": []}
    result = compute(filters=filters)
    assert result["metadata"]["filters"] == filters
    assert result["summary"]["coverage"] is None
    assert result["summary"]["recruiting_coverage"] is None
    assert result["contributors"] == []
    studies, locations, conditions, observations = inputs()
    alien = studies.copy().assign(indication_profile_id="other")
    result = audit.compute_audit(
        pd.concat([studies, alien]),
        locations,
        conditions,
        profile_id="adrd",
        observations=observations,
        filters={},
        evaluation_time="2026-09-02T10:00:00Z",
    )
    assert result["summary"]["eligible_studies"] == 5


def test_actual_observations_choose_run_not_newest_available():
    studies, locations, conditions, observations = inputs()
    newer = studies.copy().assign(snapshot_id="new", overall_status="COMPLETED")
    result = audit.compute_audit(
        pd.concat([studies, newer]),
        locations,
        conditions,
        profile_id="adrd",
        observations=observations,
        filters={},
        evaluation_time="2026-09-02T10:00:00Z",
    )
    assert result["metadata"]["snapshot_ids"] == ["run"]
    assert result["contributors"] == ["A", "D"]
    assert result["studies"]["pipeline_age_hours"].tolist() == [24] * 5


def test_facility_and_history_use_their_own_count_rules():
    assert compute(metric="facility", filters={"facilities": [["hospital", "city", "CA"]]})[
        "contributors"
    ] == ["A"]
    result = compute(metric="history", count_status="all")
    assert result["contributors"] == ["A", "D"]
    studies, locations, conditions, observations = inputs()
    studies.loc[studies.nct_id == "A", "overall_status"] = "COMPLETED"
    result = audit.compute_audit(
        studies,
        locations,
        conditions,
        profile_id="adrd",
        observations=observations,
        filters={},
        metric="history",
        count_status="all",
        evaluation_time="2026-09-02T10:00:00Z",
    )
    assert result["contributors"] == ["A", "D"]


def test_export_round_trips_metadata_flags_and_raw_refs():
    result = compute(rule_config={"taxonomy_sha256": "abc", "geography_rule": "us-state-v1"})
    exported = json.loads(audit.export_audit(result))
    assert exported["metadata"]["update_threshold_days"] == 180
    assert exported["metadata"]["evaluation_time"] == "2026-09-02T10:00:00+00:00"
    assert exported["metadata"]["rule_config"]["taxonomy_sha256"] == "abc"
    assert exported["metadata"]["rule_version"]
    assert exported["contributors"] == ["A", "D"]
    assert exported["studies"][0]["raw_page_reference"] == "run/page=00001.json"
    assert exported["studies"][0]["retrieved_at_utc"] is None
    assert exported["studies"][0]["verification_date_precision"] == "month"
    assert exported["studies"][0]["included_flag"]


@pytest.fixture
def readers(audit_fixture_root, monkeypatch):
    import streamlit as st

    from src.config import get_config

    st.cache_data.clear()
    st.cache_resource.clear()
    monkeypatch.setenv("CTI_PROJECT_ROOT", str(audit_fixture_root))
    monkeypatch.chdir(audit_fixture_root)
    get_config.cache_clear()
    yield data
    st.cache_data.clear()
    st.cache_resource.clear()


@pytest.mark.parametrize("metric", ["competition", "history", "facility", "latest_study"])
def test_real_metric_observation_scope_and_loaders(readers, metric):
    observations = readers.metric_audit_observations("adrd", metric)
    ids = observations.snapshot_id.unique().tolist()
    studies = readers.audit_studies("adrd", ids)
    locations = readers.audit_locations("adrd", ids)
    conditions = readers.audit_condition_mapping("adrd")
    result = audit.compute_audit(
        studies,
        locations,
        conditions,
        profile_id="adrd",
        observations=observations,
        filters={},
        metric=metric,
        evaluation_time="2026-09-02T10:00:00Z",
    )
    assert set(studies.indication_profile_id) == {"adrd"}
    assert set(result["metadata"]["snapshot_ids"]) == set(ids)
    if metric == "competition":
        actual = readers.query(
            "select distinct nct_id from main_intermediate."
            "int_condition_geography_activity where indication_profile_id = ? "
            "and overall_status = 'RECRUITING'",
            ["adrd"],
        )
        assert result["contributors"] == sorted(actual.nct_id.tolist())
    if metric == "facility":
        actual = readers.query(
            "select distinct nct_id from main_intermediate."
            "int_trial_site_activity where indication_profile_id = ? "
            "and overall_status = 'RECRUITING' and facility_normalized is not null",
            ["adrd"],
        )
        assert result["contributors"] == sorted(actual.nct_id.tolist())
    assert readers.audit_studies("adrd", []).empty
    assert readers.audit_locations("adrd", []).empty


def test_missing_audit_marts_returns_guidance(monkeypatch):
    monkeypatch.setattr(data, "_table_exists", lambda *args: False)
    assert data.audit_studies("adrd", ["run"]).empty
    assert "rebuild" in data.audit_studies("adrd", ["run"]).attrs["audit_unavailable"]


def test_empty_inputs_and_nullable_status():
    result = audit.compute_audit(
        pd.DataFrame(),
        pd.DataFrame(),
        pd.DataFrame(),
        profile_id="adrd",
        observations=pd.DataFrame(),
        filters={},
        evaluation_time="2026-09-02T10:00:00Z",
    )
    assert result["summary"]["coverage"] is None
    studies, locations, conditions, observations = inputs()
    studies.loc[studies.nct_id == "A", "overall_status"] = pd.NA
    result = audit.compute_audit(
        studies,
        locations,
        conditions,
        profile_id="adrd",
        observations=observations,
        filters={},
        evaluation_time="2026-09-02T10:00:00Z",
    )
    assert result["contributors"] == ["D"]


def test_snapshot_identity_survives_filter_with_no_matching_studies():
    result = compute(filters={"conditions": ["no-match"]})
    assert result["metadata"]["snapshot_ids"] == ["run"]


def test_history_union_reconciliation_and_per_snapshot_enrollment():
    studies, locations, conditions, observations = inputs()
    second = studies.copy().assign(snapshot_id="second", snapshot_date="2026-09-02")
    second.loc[second.nct_id == "A", "overall_status"] = "COMPLETED"
    result = audit.compute_audit(
        pd.concat([studies, second]),
        pd.concat([locations, locations.assign(snapshot_id="second")]),
        pd.concat([conditions, conditions.assign(snapshot_id="second")]),
        profile_id="adrd",
        metric="history",
        observations=pd.concat([observations, observations.assign(snapshot_id="second")]),
        filters={},
        evaluation_time="2026-09-03T10:00:00Z",
    )
    summary = result["summary"]
    assert summary["eligible_studies"] == summary["contributing_studies"] + sum(
        summary["exclusions"].values()
    )
    assert summary["enrollment_by_snapshot"]["run"]["estimated"]["total"] == 200
    assert summary["enrollment_by_snapshot"]["second"]["estimated"]["total"] == 100


def test_condition_activity_requires_mapping_even_without_condition_filter():
    studies, locations, conditions, observations = inputs()
    conditions = conditions.loc[conditions.nct_id != "A"]
    result = audit.compute_audit(
        studies,
        locations,
        conditions,
        profile_id="adrd",
        observations=observations,
        filters={},
        evaluation_time="2026-09-02T10:00:00Z",
    )
    assert result["summary"]["eligible_studies"] == 5
    assert result["contributors"] == ["D"]
    assert (
        result["studies"].set_index("nct_id").loc["A", "exclusion_reason"]
        == "missing_condition_mapping"
    )


@pytest.mark.parametrize("mixed", [False, True])
def test_unresolved_original_locations_are_not_known_outside(mixed):
    studies, locations, conditions, observations = inputs()
    studies.loc[studies.nct_id == "D", "geography_category"] = "usable" if mixed else "unsupported"
    if not mixed:
        locations = locations.loc[locations.nct_id != "D"]
    unresolved = dict(
        indication_profile_id="adrd",
        snapshot_id="run",
        nct_id="D",
        location_ordinal=9,
        state_normalized=None if mixed else "Atlantis",
        country="United States",
        us_location_flag=True,
        usable_geography_flag=False,
        facility_normalized="hospital",
        city_normalized="city",
        location_status="RECRUITING",
    )
    locations = pd.concat([locations, pd.DataFrame([unresolved])], ignore_index=True)
    result = audit.compute_audit(
        studies,
        locations,
        conditions,
        profile_id="adrd",
        observations=observations,
        filters={"states": ["CA"]},
        evaluation_time="2026-09-02T10:00:00Z",
    )
    row = result["studies"].set_index("nct_id").loc["D"]
    assert row.region_membership == "undetermined"
    assert not row.included_flag
    assert row.exclusion_reason == ("outside_selected_region" if mixed else "unsupported_geography")
    assert result["contributors"] == ["A"]


@pytest.mark.parametrize("empty_studies", [True, False])
def test_selected_snapshot_identity_survives_unavailable_study_rows(empty_studies):
    studies, locations, conditions, observations = inputs()
    if empty_studies:
        studies = pd.DataFrame()
    observations = observations.assign(snapshot_id="absent-run", snapshot_date="2026-09-03")
    observations = pd.concat(
        [observations, observations.assign(indication_profile_id="other", snapshot_id="other-run")]
    )
    result = audit.compute_audit(
        studies,
        locations,
        conditions,
        profile_id="adrd",
        observations=observations,
        filters={},
        evaluation_time="2026-09-04T10:00:00Z",
    )
    assert result["metadata"]["snapshot_ids"] == ["absent-run"]
    assert result["metadata"]["snapshots"] == [
        {"snapshot_id": "absent-run", "snapshot_date": "2026-09-03"}
    ]
    assert json.loads(audit.export_audit(result))["metadata"]["snapshots"] == [
        {"snapshot_id": "absent-run", "snapshot_date": "2026-09-03"}
    ]
