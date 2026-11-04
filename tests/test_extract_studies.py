import pytest

from src.ingest.extract_studies import run_ingestion


class Sentinel(Exception):
    pass


class NoNetworkClient:
    """Stands in for CTGClient; entering it means the guard did not fire."""

    def __init__(self, api):
        pass

    def __enter__(self):
        raise Sentinel("CTGClient entered")

    def __exit__(self, *args):
        return False


def test_full_catalog_profile_rejects_condition(monkeypatch):
    monkeypatch.setattr("src.ingest.extract_studies.CTGClient", NoNetworkClient)
    with pytest.raises(ValueError, match="full-catalog"):
        run_ingestion(condition="Cancer", profile="full-catalog")


def test_default_profile_still_accepts_condition(monkeypatch):
    monkeypatch.setattr("src.ingest.extract_studies.CTGClient", NoNetworkClient)
    with pytest.raises(Sentinel):
        run_ingestion(condition="Alzheimer Disease")


def test_whitespace_only_condition_rejected(monkeypatch):
    monkeypatch.setattr("src.ingest.extract_studies.CTGClient", NoNetworkClient)
    for bad in ("   ", ""):
        with pytest.raises(ValueError, match="whitespace"):
            run_ingestion(condition=bad)


def test_whitespace_only_condition_rejected_before_profile_guard(monkeypatch):
    monkeypatch.setattr("src.ingest.extract_studies.CTGClient", NoNetworkClient)
    with pytest.raises(ValueError, match="whitespace"):
        run_ingestion(condition="   ", profile="full-catalog")


def test_condition_is_stripped_before_use(monkeypatch):
    captured = []

    class CaptureClient:
        def __init__(self, api):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def build_params(self, condition=None):
            captured.append(condition)
            raise Sentinel("stop before any IO")

    monkeypatch.setattr("src.ingest.extract_studies.CTGClient", CaptureClient)
    with pytest.raises(Sentinel):
        run_ingestion(condition="  Alzheimer Disease  ")
    assert captured == ["Alzheimer Disease"]


def test_ingestion_captures_page_retrieval_separately_from_unchanged_raw(tmp_path, monkeypatch):
    import json
    from datetime import UTC, datetime

    from tests.test_build_silver import make_config

    raw_pages = [
        '{ "studies": [], "nextPageToken": "second" }\n',
        '{"studies": []}\n',
    ]

    class OfflineClient:
        def __init__(self, api):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def build_params(self, condition=None):
            return {}

        def fetch_page(self, params, page_token=None):
            raw = raw_pages[0 if page_token is None else 1]
            return json.loads(raw), raw

    monkeypatch.setattr("src.ingest.extract_studies.CTGClient", OfflineClient)
    moments = iter(datetime(2026, 10, 2, 10, minute, tzinfo=UTC) for minute in range(4))
    monkeypatch.setattr("src.ingest.extract_studies.utc_now", lambda: next(moments))
    cfg = make_config(tmp_path)
    manifest = run_ingestion(full_refresh=True, config=cfg)
    run_dir = cfg.paths.bronze_api_responses / f"run_id={manifest.ingestion_run_id}"
    for number, raw in enumerate(raw_pages, start=1):
        assert (run_dir / f"page={number:05d}.json").read_bytes() == raw.encode()
        metadata = json.loads((run_dir / "_page_metadata" / f"page={number:05d}.json").read_text())
        assert metadata["retrieved_at_utc"] == f"2026-10-02T10:0{number}:00+00:00"
    assert manifest.status == "success"


def test_merge_essie_since():
    from src.ingest.extract_studies import merge_essie_since

    assert merge_essie_since(None, "2026-10-20") == "AREA[LastUpdatePostDate]RANGE[2026-10-20,MAX]"
    assert (
        merge_essie_since("AREA[StudyType]INTERVENTIONAL", "2026-10-20")
        == "(AREA[StudyType]INTERVENTIONAL) AND (AREA[LastUpdatePostDate]RANGE[2026-10-20,MAX])"
    )


def test_latest_snapshot_date(tmp_path):
    from datetime import UTC, datetime

    from src.ingest.extract_studies import latest_snapshot_date
    from src.ingest.snapshot_manifest import IngestionManifest, write_manifest

    # No manifests
    assert latest_snapshot_date(tmp_path) is None

    # Failed manifest
    m1 = IngestionManifest(
        ingestion_run_id="run1",
        query_hash="h1",
        endpoint="ep",
        params={},
        status="failed",
        started_at_utc=datetime(2026, 10, 20, 12, 0, tzinfo=UTC),
    )
    write_manifest(tmp_path, m1)
    assert latest_snapshot_date(tmp_path) is None

    # Successful manifest -> start minus 1 day overlap
    m2 = IngestionManifest(
        ingestion_run_id="run2",
        query_hash="h2",
        endpoint="ep",
        params={},
        status="success",
        started_at_utc=datetime(2026, 10, 25, 12, 0, tzinfo=UTC),
    )
    write_manifest(tmp_path, m2)
    assert latest_snapshot_date(tmp_path) == "2026-10-24"


def test_incremental_refresh_without_prior_run_raises(tmp_path):
    from tests.test_build_silver import make_config

    cfg = make_config(tmp_path)
    with pytest.raises(ValueError, match="needs a previous successful snapshot"):
        run_ingestion(config=cfg, incremental_refresh=True)


def test_incremental_refresh_builds_since_query_and_marks_manifest(tmp_path, monkeypatch):
    from datetime import UTC, datetime

    from src.ingest.snapshot_manifest import IngestionManifest, write_manifest
    from tests.test_build_silver import make_config

    cfg = make_config(tmp_path)
    m_prior = IngestionManifest(
        ingestion_run_id="run_prev",
        query_hash="h_prev",
        endpoint=cfg.api.studies_url,
        params={},
        status="success",
        started_at_utc=datetime(2026, 10, 25, 12, 0, tzinfo=UTC),
    )
    write_manifest(cfg.paths.bronze_manifests, m_prior)
    silver_trials = cfg.paths.silver / "silver_trials"
    silver_trials.mkdir(parents=True, exist_ok=True)
    (silver_trials / "run_id=run_prev.parquet").touch()

    captured_params = {}

    class MockIncrementalClient:
        def __init__(self, api):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def build_params(self, condition=None):
            return {"filter.advanced": "AREA[StudyType]INTERVENTIONAL"}

        def fetch_page(self, params, page_token=None):
            captured_params.update(params)
            return {"studies": [], "totalCount": 0}, '{"studies": []}'

    monkeypatch.setattr("src.ingest.extract_studies.CTGClient", MockIncrementalClient)
    manifest = run_ingestion(config=cfg, incremental_refresh=True)

    assert manifest.status == "success"
    assert manifest.updated_since_utc == "2026-10-24"
    assert "AREA[LastUpdatePostDate]RANGE[2026-10-24,MAX]" in captured_params["filter.advanced"]


def test_latest_snapshot_date_checks_silver_when_provided(tmp_path):
    from datetime import UTC, datetime

    from src.ingest.extract_studies import latest_snapshot_date
    from src.ingest.snapshot_manifest import IngestionManifest, write_manifest

    manifests_dir = tmp_path / "manifests"
    manifests_dir.mkdir(parents=True, exist_ok=True)
    silver_dir = tmp_path / "silver"

    m = IngestionManifest(
        ingestion_run_id="run_test",
        query_hash="h_test",
        endpoint="ep",
        params={},
        status="success",
        started_at_utc=datetime(2026, 10, 25, 12, 0, tzinfo=UTC),
    )
    write_manifest(manifests_dir, m)

    # Without silver_dir check, it returns the date
    assert latest_snapshot_date(manifests_dir) == "2026-10-24"
    # With silver_dir check, it requires silver_trials to exist
    assert latest_snapshot_date(manifests_dir, silver_dir) is None

    # Now create the silver file
    (silver_dir / "silver_trials").mkdir(parents=True, exist_ok=True)
    (silver_dir / "silver_trials" / "run_id=run_test.parquet").touch()
    assert latest_snapshot_date(manifests_dir, silver_dir) == "2026-10-24"


def test_incremental_refresh_fails_closed_on_count_total_mismatch(tmp_path, monkeypatch):
    """When record_count != totalCount reported by API, incremental refresh fails closed
    and does not advance the watermark."""
    from datetime import UTC, datetime

    from src.ingest.extract_studies import latest_snapshot_date
    from src.ingest.snapshot_manifest import IngestionManifest, load_manifests, write_manifest
    from tests.test_build_silver import make_config

    cfg = make_config(tmp_path)
    m_base = IngestionManifest(
        ingestion_run_id="run_base",
        query_hash="h_base",
        endpoint=cfg.api.studies_url,
        params={},
        status="success",
        started_at_utc=datetime(2026, 10, 10, 12, 0, tzinfo=UTC),
    )
    write_manifest(cfg.paths.bronze_manifests, m_base)
    silver_trials = cfg.paths.silver / "silver_trials"
    silver_trials.mkdir(parents=True, exist_ok=True)
    (silver_trials / "run_id=run_base.parquet").touch()

    class MockMismatchClient:
        def __init__(self, api):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def build_params(self, condition=None):
            return {"filter.advanced": "AREA[StudyType]INTERVENTIONAL"}

        def fetch_page(self, params, page_token=None):
            # API reports 9000 total studies, but returns only 1 study with no nextPageToken
            study = {"protocolSection": {"identificationModule": {"nctId": "NCT00000001"}}}
            return {"studies": [study], "totalCount": 9000}, '{"studies": [{}]}'

    monkeypatch.setattr("src.ingest.extract_studies.CTGClient", MockMismatchClient)

    with pytest.raises(RuntimeError, match="record_count 1 != countTotal 9000"):
        run_ingestion(config=cfg, incremental_refresh=True)

    # Manifest written to disk must be marked "failed"
    manifests = load_manifests(cfg.paths.bronze_manifests)
    failed_runs = [m for m in manifests if m.status == "failed"]
    assert len(failed_runs) == 1
    assert "countTotal 9000" in (failed_runs[0].error or "")

    # Watermark must not have advanced; latest_snapshot_date must still return the base run's date
    assert latest_snapshot_date(cfg.paths.bronze_manifests, cfg.paths.silver) == "2026-10-09"
