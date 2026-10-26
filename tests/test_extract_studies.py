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
