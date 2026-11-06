"""The scheduled uptime/freshness check: a fresh marker passes, a stale or broken one reports."""

import importlib.util
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tests.test_freshness import marker_payload

SPEC = importlib.util.spec_from_file_location(
    "check_deployment", Path(__file__).resolve().parents[1] / "scripts/check_deployment.py"
)
assert SPEC and SPEC.loader
check_deployment = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(check_deployment)

NOW = datetime.now(UTC)


def marker_aged(days):
    payload = marker_payload()
    for profile in payload["last_success"]["profiles"].values():
        profile["captured_at_utc"] = (NOW - timedelta(days=days)).isoformat()
    return json.dumps(payload)


@pytest.fixture
def healthy(monkeypatch):
    monkeypatch.setattr(check_deployment, "_get", lambda url, attempts=3: (200, "ok"))


def run(tmp_path, raw, capsys):
    marker = tmp_path / "freshness.json"
    marker.write_text(raw)
    code = check_deployment.main(["https://example.test", "--marker-file", str(marker)])
    return code, capsys.readouterr().out


def test_fresh_marker_passes(healthy, tmp_path, capsys):
    assert run(tmp_path, marker_aged(2), capsys) == (0, "OK: healthy and fresh\n")


def test_stale_marker_reports_each_profile(healthy, tmp_path, capsys):
    code, out = run(tmp_path, marker_aged(11), capsys)
    assert code == 1
    assert "adrd: capture is stale" in out and "oncology_nsclc: capture is stale" in out


@pytest.mark.parametrize(
    "raw", ["[]", "{}", json.dumps({**marker_payload(), "last_success": None})]
)
def test_unreadable_marker_reports(healthy, tmp_path, capsys, raw):
    code, out = run(tmp_path, raw, capsys)
    assert code == 1 and "unreadable" in out


@pytest.mark.parametrize("raw", ["{", "<!doctype html><html></html>"])
def test_non_json_body_reports_missing_or_not_served(healthy, tmp_path, capsys, raw):
    code, out = run(tmp_path, raw, capsys)
    assert code == 1 and "missing or not served" in out


def test_script_crash_exits_2_not_1(monkeypatch, capsys):
    def boom(*args):
        raise RuntimeError("boom")

    monkeypatch.setattr(check_deployment, "check", boom)
    assert check_deployment.main(["https://example.test"]) == 2
    captured = capsys.readouterr()
    assert captured.out == "" and "RuntimeError: boom" in captured.err


def test_failed_attempt_marker_is_not_a_success(healthy, tmp_path, capsys):
    raw = json.dumps({**json.loads(marker_aged(1)), "attempt_status": "failed"})
    assert run(tmp_path, raw, capsys)[0] == 1


def test_health_failure_and_missing_marker_report(monkeypatch):
    monkeypatch.setattr(check_deployment, "_get", lambda url, attempts=3: (404, ""))
    problems = check_deployment.check("https://example.test", None, 10)
    assert len(problems) == 2 and "HTTP 404" in problems[0]

    def down(url, attempts=3):
        raise OSError("refused")

    monkeypatch.setattr(check_deployment, "_get", down)
    assert "unreachable" in check_deployment.check("https://example.test", None, 10)[0]
