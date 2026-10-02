"""Dashboard audits retain displayed scope, decisions and export provenance."""

import json
import sys
from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dashboard"))

from components import data  # noqa: E402
from components.audit_panel import build_display_audit  # noqa: E402

from src.config import get_config  # noqa: E402


@pytest.fixture
def warehouse(audit_fixture_root, monkeypatch):
    import streamlit as st

    monkeypatch.setenv("CTI_PROJECT_ROOT", str(audit_fixture_root))
    monkeypatch.chdir(audit_fixture_root)
    get_config.cache_clear()
    st.cache_data.clear()
    st.cache_resource.clear()
    yield
    st.cache_data.clear()
    st.cache_resource.clear()
    get_config.cache_clear()


def test_filter_scope_and_export(warehouse):
    from components.audit import export_audit

    observations = data.metric_audit_observations("adrd", "competition")
    result = build_display_audit("adrd", observations, {"states": ["NC"]})
    other = build_display_audit("adrd", observations, {"states": ["XX"]})
    assert result["contributors"] != other["contributors"]
    for audit in (result, other):
        payload = json.loads(export_audit(audit))
        assert payload["summary"]["contributing_studies"] == len(payload["contributors"])
        assert payload["metadata"]["snapshot_ids"]
        assert payload["metadata"]["rule_config"]["taxonomy"]["sha256"]
        assert payload["metadata"]["rule_config"]["geography"]["sha256"]
        assert payload["metadata"]["rule_version"]
        assert all("flag_reasons" in row for row in payload["studies"])


def test_profile_and_period_scope(warehouse):
    for profile in ("adrd", "oncology_nsclc"):
        observations = data.metric_audit_observations(profile, "history")
        latest = pd.to_datetime(observations.snapshot_date).max()
        selected = observations[pd.to_datetime(observations.snapshot_date) == latest]
        audit = build_display_audit(profile, selected, {}, metric="history", count_status="all")
        assert set(audit["studies"].indication_profile_id) == {profile}
        assert audit["metadata"]["snapshot_ids"] == sorted(selected.snapshot_id.unique())
        assert audit["metadata"]["count_status"] == "all"


def test_panel_filter_rerun(warehouse):
    at = AppTest.from_file(
        str(ROOT / "dashboard/pages/2_Competition_Landscape.py"), default_timeout=60
    ).run()
    assert not at.exception
    state = next(w for w in at.sidebar.multiselect if w.label == "State")
    state.set_value(["NC"])
    at.run()
    assert not at.exception
    metadata = next(j.value for j in at.json if "rule_version" in j.value)
    assert json.loads(metadata)["filters"]["states"] == ["NC"]
    assert any(x.label == "Data coverage and caveats" for x in at.expander)
    assert any("Download metric audit" in x.proto.label for x in at.get("download_button"))


def test_missing_and_empty_guidance(warehouse, monkeypatch):
    missing = pd.DataFrame()
    missing.attrs["audit_unavailable"] = "Rebuild audit marts with make pipeline."
    monkeypatch.setattr(data, "metric_audit_observations", lambda *args: missing)
    at = AppTest.from_file(str(ROOT / "dashboard/app.py"), default_timeout=60).run()
    assert not at.exception
    assert any("Rebuild audit marts" in x.value for x in at.warning)
    monkeypatch.setattr(data, "metric_audit_observations", lambda *args: pd.DataFrame())
    at = AppTest.from_file(str(ROOT / "dashboard/app.py"), default_timeout=60).run()
    assert not at.exception
    assert any("No captured observations" in x.value for x in at.info)


@pytest.mark.parametrize(
    "script",
    [
        "dashboard/app.py",
        "dashboard/pages/1_Priority_Queue.py",
        "dashboard/pages/2_Competition_Landscape.py",
        "dashboard/pages/3_Geography_Trends.py",
        "dashboard/pages/4_Site_Overlap.py",
        "dashboard/pages/5_Sponsor_Landscape.py",
        "dashboard/pages/6_Data_Reliability.py",
    ],
)
def test_every_metric_page_exposes_profile_audit(script, warehouse):
    from components.audit import DISCLAIMER

    for profile in ("adrd", "oncology_nsclc"):
        at = AppTest.from_file(str(ROOT / script), default_timeout=60).run()
        at.sidebar.selectbox(key="indication_profile_selector").set_value(profile)
        at.run()
        assert not at.exception
        assert any(x.value == DISCLAIMER for x in at.markdown)
        metadata = [json.loads(j.value) for j in at.json if "rule_version" in j.value]
        assert metadata
        for item in metadata:
            assert item["profile_id"] == profile
            assert item["snapshot_ids"] or "Site_Overlap" in script
        for table in at.dataframe:
            frame = table.value
            if "audit_rule_version" in frame.columns:
                assert set(frame.indication_profile_id) <= {profile}
                assert "snapshot_id" in frame.columns
        if "Priority_Queue" in script:
            assert "Data Confidence" not in str(at.dataframe[0].proto)
            assert "data confidence" not in str(at.dataframe[0].value).lower()


def test_growth_input_window_export_metadata(warehouse):
    at = AppTest.from_file(
        str(ROOT / "dashboard/pages/2_Competition_Landscape.py"), default_timeout=60
    ).run()
    next(w for w in at.selectbox if w.label == "Audit metric inputs").set_value(
        "90-day growth observations"
    )
    at.run()
    assert not at.exception
    metadata = next(json.loads(j.value) for j in at.json if "rule_version" in j.value)
    assert metadata["metric"] == "history"
    assert metadata["rule_config"]["display_context"]["growth_window_start"]
    assert metadata["rule_config"]["display_context"]["growth_window_end"]


def test_empty_facility_export_keeps_snapshot_scope(warehouse):
    from components.audit import export_audit

    observations = data.metric_audit_observations("adrd", "facility")
    result = build_display_audit(
        "adrd",
        observations,
        {"facilities": []},
        metric="facility",
        context={"empty_facility_selection": True},
    )
    payload = json.loads(export_audit(result))
    assert payload["metadata"]["snapshot_ids"] == sorted(observations.snapshot_id.unique())
    assert payload["summary"]["coverage"] is None
    assert payload["contributors"] == []


def test_all_status_mode_matches_current_study_count(warehouse):
    observations = data.metric_audit_observations("adrd", "latest_study")
    result = build_display_audit(
        "adrd", observations, {}, metric="latest_study", count_status="all"
    )
    assert result["summary"]["contributing_studies"] == observations.nct_id.nunique()
    assert result["metadata"]["count_status"] == "all"
