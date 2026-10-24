"""Freshness must describe verified captures, never just a recent process exit."""

import importlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import duckdb
import pytest

from src.ingest.snapshot_manifest import IngestionManifest, write_manifest
from src.profiles import get_registry

NOW = datetime(2026, 10, 23, 12, tzinfo=UTC)


def marker_payload():
    return {
        "schema_version": 1,
        "attempt_at_utc": NOW.isoformat(),
        "attempt_status": "success",
        "error": None,
        "last_success": {
            "pipeline_completed_at_utc": NOW.isoformat(),
            "source_api_version": "v2",
            "source_release_at_utc": None,
            "profiles": {
                pid: {
                    "run_id": f"20261020T120000Z_{pid}",
                    "captured_at_utc": "2026-10-20T12:00:00+00:00",
                    "row_counts": {
                        "dim_trial": 1,
                        "fct_trial_snapshot": 1,
                        "mart_trial_similarity": 0,
                    },
                }
                for pid in ("adrd", "oncology_nsclc")
            },
            "reconciliation": {"checks_total": 10, "checks_failed": 0},
        },
    }


@pytest.fixture
def freshness(project_root_tmp, monkeypatch):
    monkeypatch.setenv("CTI_REFRESHABLE_PROFILES", "adrd,oncology_nsclc")
    monkeypatch.delenv("CTI_FRESHNESS_MAX_AGE_DAYS", raising=False)
    monkeypatch.delenv("CTI_REFRESH_MIN_FREE_BYTES", raising=False)
    module = importlib.import_module("src.quality.freshness")
    monkeypatch.setattr(module, "utc_now", lambda: NOW)
    return module


def write_marker(freshness, payload):
    path = freshness.marker_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))
    return path


@pytest.mark.parametrize("seconds,ok", [(864000 - 1, True), (864000, True), (864001, False)])
def test_capture_age_ten_day_boundary(freshness, seconds, ok):
    payload = marker_payload()
    for profile in payload["last_success"]["profiles"].values():
        profile["captured_at_utc"] = (NOW - timedelta(seconds=seconds)).isoformat()
    write_marker(freshness, payload)
    assert freshness.status()["ok"] is ok


def test_one_stale_profile_fails_aggregate_but_not_other_profile(freshness):
    payload = marker_payload()
    payload["last_success"]["profiles"]["oncology_nsclc"]["captured_at_utc"] = (
        NOW - timedelta(days=11)
    ).isoformat()
    write_marker(freshness, payload)
    assert not freshness.status()["ok"]
    assert freshness.status("adrd")["ok"]
    assert not freshness.status("oncology_nsclc")["ok"]


@pytest.mark.parametrize(
    "bad_timestamp", [None, "bad", "2026-10-20", "2026-10-24T00:00:00Z", 1792497600]
)
def test_invalid_capture_never_reads_fresh(freshness, bad_timestamp):
    payload = marker_payload()
    payload["last_success"]["profiles"]["adrd"]["captured_at_utc"] = bad_timestamp
    write_marker(freshness, payload)
    assert not freshness.status()["ok"]


@pytest.mark.parametrize("content", [None, "{", "null", "[]", "{}"])
def test_unknown_marker_is_not_fresh(freshness, content):
    if content is not None:
        path = freshness.marker_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    assert not freshness.status()["ok"]


@pytest.mark.parametrize(
    "field", ["attempt", "attempt-time", "pipeline", "reconciliation", "counts", "profile"]
)
def test_invalid_verification_is_not_fresh(freshness, field):
    payload = marker_payload()
    if field == "attempt":
        payload["attempt_status"] = "failed"
    elif field == "attempt-time":
        payload["attempt_at_utc"] = "2026-10-24T00:00:00Z"
    elif field == "pipeline":
        payload["last_success"]["pipeline_completed_at_utc"] = "2026-10-24T00:00:00Z"
    elif field == "reconciliation":
        payload["last_success"]["reconciliation"]["checks_failed"] = 1
    elif field == "counts":
        payload["last_success"]["profiles"]["adrd"]["row_counts"]["dim_trial"] = -1
    else:
        del payload["last_success"]["profiles"]["oncology_nsclc"]
    write_marker(freshness, payload)
    assert not freshness.status()["ok"]


def test_configured_age_and_invalid_threshold(freshness, monkeypatch):
    write_marker(freshness, marker_payload())
    monkeypatch.setenv("CTI_FRESHNESS_MAX_AGE_DAYS", "2")
    assert not freshness.status()["ok"]
    monkeypatch.setenv("CTI_FRESHNESS_MAX_AGE_DAYS", "nan")
    assert not freshness.status()["ok"]


@pytest.mark.parametrize(
    "missing", ["schema_version", "source_api_version", "source_release_at_utc"]
)
def test_incomplete_marker_contract_is_unknown(freshness, missing):
    payload = marker_payload()
    del (payload if missing == "schema_version" else payload["last_success"])[missing]
    write_marker(freshness, payload)
    assert not freshness.status()["ok"]


@pytest.mark.parametrize("field,value", [("schema_version", True), ("error", "refresh failed")])
def test_contradictory_marker_never_reads_fresh(freshness, field, value):
    payload = marker_payload()
    payload[field] = value
    write_marker(freshness, payload)
    assert not freshness.status()["ok"]


@pytest.mark.parametrize("status", ["running", "failed"])
def test_attempt_marker_keeps_prior_success_but_warns(freshness, status):
    path = write_marker(freshness, marker_payload())
    before = json.loads(path.read_text())["last_success"]
    flag = "--record-attempt" if status == "running" else "--record-failure"
    assert freshness.main([flag]) == 0
    after = json.loads(path.read_text())
    assert after["attempt_status"] == status
    assert after["last_success"] == before
    assert not freshness.status()["ok"]


@pytest.mark.parametrize("has_success", [True, False])
def test_failure_diagnostic_is_visible_with_or_without_prior_success(freshness, has_success):
    payload = marker_payload()
    payload["attempt_status"] = "failed"
    payload["error"] = "Reconciliation failed for adrd"
    if not has_success:
        payload["last_success"] = None
    write_marker(freshness, payload)
    result = freshness.status()
    assert not result["ok"]
    assert result["metadata"]["error"] == payload["error"]
    assert payload["error"] in result["errors"]


def test_new_attempt_clears_previous_failure_diagnostic(freshness):
    payload = marker_payload()
    payload["attempt_status"] = "failed"
    payload["error"] = "Reconciliation failed for adrd"
    path = write_marker(freshness, payload)
    for flag in ("--record-attempt", "--record-failure"):
        assert freshness.main([flag]) == 0
        after = json.loads(path.read_text())
        assert after["error"] is None
        assert after["last_success"] == payload["last_success"]


@pytest.fixture
def accepted_root(freshness):
    profiles = get_registry().refreshable()
    warehouse = profiles[0].config.paths.duckdb
    warehouse.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(warehouse)) as con:
        con.execute("create schema main_marts")
        con.execute(
            "create table main_marts.dim_trial(indication_profile_id varchar, nct_id varchar)"
        )
        con.execute(
            "create table main_marts.fct_trial_snapshot("
            "indication_profile_id varchar, ingestion_run_id varchar, current_record_flag boolean)"
        )
        con.execute("create table main_marts.mart_trial_similarity(indication_profile_id varchar)")
        for profile in profiles:
            pid, cfg = profile.profile_id, profile.config
            run_id = f"20261020T120000Z_{pid}"
            write_manifest(
                cfg.paths.bronze_manifests,
                IngestionManifest(
                    ingestion_run_id=run_id,
                    query_hash="fixture",
                    endpoint="https://clinicaltrials.gov/api/v2/studies",
                    params={},
                    profile=pid,
                    status="success",
                    started_at_utc=NOW - timedelta(days=3),
                    ended_at_utc=NOW - timedelta(days=3) + timedelta(minutes=1),
                    page_count=1,
                    record_count=1,
                ),
            )
            silver = cfg.paths.silver / "silver_trials" / f"run_id={run_id}.parquet"
            silver.parent.mkdir(parents=True, exist_ok=True)
            con.execute(
                "copy (select 'NCT00000001' as nct_id) to ? (format parquet)", [str(silver)]
            )
            con.execute("insert into main_marts.dim_trial values (?, 'NCT00000001')", [pid])
            con.execute(
                "insert into main_marts.fct_trial_snapshot values (?, ?, true)", [pid, run_id]
            )
    return warehouse


def test_success_records_actual_scope_counts_and_original_capture(freshness, accepted_root):
    assert freshness.main(["--record-success"]) == 0
    payload = json.loads(freshness.marker_path().read_text())
    assert payload["attempt_status"] == "success"
    success = payload["last_success"]
    assert datetime.fromisoformat(success["pipeline_completed_at_utc"]) == NOW
    assert success["source_release_at_utc"] is None
    assert set(success["profiles"]) == {"adrd", "oncology_nsclc"}
    assert success["profiles"]["adrd"]["row_counts"]["dim_trial"] == 1
    assert datetime.fromisoformat(success["profiles"]["adrd"]["captured_at_utc"]) == (
        NOW - timedelta(days=3)
    )


@pytest.mark.parametrize("failure", ["pipeline", "reconciliation", "missing", "failed", "skipped"])
def test_failed_attempt_preserves_last_success(freshness, accepted_root, failure):
    assert freshness.main(["--record-success"]) == 0
    before = json.loads(freshness.marker_path().read_text())["last_success"]
    if failure == "reconciliation":
        with duckdb.connect(str(accepted_root)) as con:
            con.execute("delete from main_marts.dim_trial where indication_profile_id='adrd'")
    elif failure in {"missing", "failed"}:
        path = next(
            get_registry().get("adrd").config.paths.bronze_manifests.glob("manifest_*.json")
        )
        if failure == "missing":
            path.unlink()
        else:
            manifest = json.loads(path.read_text())
            manifest["status"] = "failed"
            path.write_text(json.dumps(manifest))
    elif failure == "skipped":
        with duckdb.connect(str(accepted_root)) as con:
            con.execute("update main_marts.fct_trial_snapshot set ingestion_run_id='older'")
    code = freshness.main(["--record-failure" if failure == "pipeline" else "--record-success"])
    assert code == (0 if failure == "pipeline" else 1)
    after = json.loads(freshness.marker_path().read_text())
    assert after["last_success"] == before
    assert after["attempt_status"] == "failed"
    assert not freshness.status()["ok"]


def test_reusing_capture_does_not_reset_its_age(freshness, accepted_root, monkeypatch):
    monkeypatch.setattr(freshness, "utc_now", lambda: NOW + timedelta(days=8))
    assert freshness.main(["--record-success"]) == 0
    assert not freshness.status()["ok"]


def test_atomic_write_failure_preserves_old_marker(freshness, accepted_root, monkeypatch):
    path = write_marker(freshness, marker_payload())
    before = path.read_bytes()

    def full_disk(*args):
        raise OSError("simulated ENOSPC")

    monkeypatch.setattr(freshness.os, "replace", full_disk)
    assert freshness.main(["--record-success"]) == 1
    assert path.read_bytes() == before
    assert list(path.parent.glob("*.tmp")) == []


@pytest.mark.parametrize("free,ok", [(499999999, False), (500000000, True), (500000001, True)])
def test_disk_floor_boundary(freshness, monkeypatch, capsys, free, ok):
    monkeypatch.setattr(freshness.shutil, "disk_usage", lambda path: SimpleNamespace(free=free))
    assert freshness.main(["--preflight"]) == (0 if ok else 1)
    output = capsys.readouterr()
    assert str(free) in output.out + output.err
    assert "500000000" in output.out + output.err


@pytest.mark.parametrize("floor", ["0", "-1", "bad"])
def test_disk_floor_must_be_positive(freshness, monkeypatch, floor):
    monkeypatch.setenv("CTI_REFRESH_MIN_FREE_BYTES", floor)
    assert freshness.main(["--preflight"]) == 1


def test_preflight_measures_the_warehouse_filesystem(freshness, monkeypatch):
    from src.profiles import load_shared_paths

    warehouse_dir = load_shared_paths().duckdb.parent
    warehouse_dir.mkdir(parents=True, exist_ok=True)

    def disk_usage(path):
        return SimpleNamespace(free=1 if path == warehouse_dir else 1000000000)

    monkeypatch.setattr(freshness.shutil, "disk_usage", disk_usage)
    assert freshness.main(["--preflight"]) == 1


def test_cli_check_is_read_only_and_returns_failure_for_stale_capture(freshness, accepted_root):
    assert freshness.main(["--record-success"]) == 0
    path = freshness.marker_path()
    before = path.read_bytes()
    result = subprocess.run(
        [sys.executable, "-m", "src.quality.freshness", "--check"],
        env={**os.environ, "CTI_FRESHNESS_MAX_AGE_DAYS": "0.00001"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert not json.loads(result.stdout)["ok"]
    assert path.read_bytes() == before


def test_check_prints_json_even_with_invalid_disk_configuration(freshness, monkeypatch, capsys):
    write_marker(freshness, marker_payload())
    monkeypatch.setenv("CTI_REFRESH_MIN_FREE_BYTES", "invalid")
    assert freshness.main(["--check"]) == 1
    assert not json.loads(capsys.readouterr().out)["ok"]
