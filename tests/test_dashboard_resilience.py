"""Tests verifying dashboard resilience when warehouse schemas differ (e.g. older migrations)."""

import sys
from pathlib import Path

import duckdb
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "dashboard") not in sys.path:
    sys.path.insert(0, str(ROOT / "dashboard"))


@pytest.fixture
def temp_duckdb_legacy_schema(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create minimal DuckDB without indication_profile_id and without mart_trial_similarity."""
    db_path = tmp_path / "legacy.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute("create schema main_marts")
    con.execute(
        """
        create table main_marts.dim_trial (
            trial_key varchar,
            nct_id varchar,
            registry_url varchar,
            current_brief_title varchar,
            current_overall_status varchar,
            current_phase varchar,
            current_study_type varchar,
            current_lead_sponsor varchar,
            current_lead_sponsor_normalized varchar,
            start_date date,
            primary_completion_date date,
            completion_date date,
            study_first_post_date date,
            enrollment_count integer,
            enrollment_type varchar,
            current_has_results_flag boolean,
            record_quality_flag varchar,
            first_seen_snapshot_date date,
            latest_seen_snapshot_date date,
            active_in_latest_snapshot_flag boolean
        )
        """
    )
    con.execute(
        """
        insert into main_marts.dim_trial (
            nct_id, registry_url, current_brief_title,
            current_overall_status, current_phase,
            study_first_post_date, enrollment_count
        ) values (
            'NCT00000001', 'https://clinicaltrials.gov/study/NCT00000001',
            'Test Trial 1', 'RECRUITING', 'PHASE2', '2024-01-01', 100
        )
        """
    )
    con.execute(
        """
        create table main_marts.fct_trial_site (
            nct_id varchar,
            snapshot_date date,
            state_normalized varchar
        )
        """
    )
    con.execute(
        """
        insert into main_marts.fct_trial_site values ('NCT00000001', '2024-01-01', 'CA')
        """
    )
    con.close()

    # Clear cached connection in dashboard.components.data
    from components import data

    monkeypatch.setattr(data, "warehouse_path", lambda: db_path)
    data._connection.clear()
    data.query.clear()
    data._table_exists.clear()
    data._has_column.clear()
    data.trial_similarity.clear()
    data.get_indication_profiles.clear()

    return db_path


def test_schema_helpers_detect_presence_and_absence(temp_duckdb_legacy_schema: Path):
    from components import data

    assert data._table_exists("dim_trial")
    assert not data._table_exists("mart_trial_similarity")
    assert not data._table_exists("nonexistent_table")

    assert data._has_column("dim_trial", "nct_id")
    assert not data._has_column("dim_trial", "indication_profile_id")
    assert not data._has_column("nonexistent_table", "nct_id")


def test_trial_explorer_legacy_schema_omits_indication_column(temp_duckdb_legacy_schema: Path):
    from components import data

    with pytest.raises(TypeError):
        data.trial_explorer()

    df = data.trial_explorer("adrd")
    assert isinstance(df, pd.DataFrame)
    assert not df.empty
    assert "nct_id" in df.columns
    assert "indication_profile_id" not in df.columns
    assert df.iloc[0]["nct_id"] == "NCT00000001"


def test_get_indication_profiles_legacy_schema_returns_empty(temp_duckdb_legacy_schema: Path):
    from components import data

    profiles = data.get_indication_profiles()
    assert profiles == []


def test_trial_similarity_legacy_schema_returns_empty_dataframe(temp_duckdb_legacy_schema: Path):
    from components import data

    with pytest.raises(TypeError):
        data.trial_similarity("NCT00000001")

    df = data.trial_similarity("adrd", "NCT00000001")
    assert isinstance(df, pd.DataFrame)
    assert df.empty
    data._connection.clear()
    con = duckdb.connect(str(temp_duckdb_legacy_schema))
    con.execute(
        "create table main_marts.mart_trial_similarity "
        "(same_condition integer, similarity_score double, similarity_rank integer, "
        "similarity_explanation varchar)"
    )
    con.close()
    data._table_exists.clear()
    data._has_column.clear()
    data.trial_similarity.clear()

    df = data.trial_similarity("adrd", "NCT00000001")
    assert isinstance(df, pd.DataFrame)
    assert df.empty


def test_trial_explorer_page_smoke_legacy_schema(temp_duckdb_legacy_schema: Path):
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(ROOT / "dashboard/pages/7_Trial_Explorer.py"), default_timeout=30)
    at.run()
    assert not at.exception, at.exception[0].value if at.exception else ""


def test_trial_similarity_page_smoke_legacy_schema(temp_duckdb_legacy_schema: Path):
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(ROOT / "dashboard/pages/8_Trial_Similarity.py"), default_timeout=30)
    at.run()
    assert not at.exception, at.exception[0].value if at.exception else ""
    # Should display info message when no comparable trials or table exist
    assert any("No comparable trials found" in str(msg.value) for msg in at.info)


@pytest.fixture
def temp_duckdb_modern_schema(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create DuckDB database with indication_profile_id and mart_trial_similarity populated."""
    db_path = tmp_path / "modern.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute("create schema main_marts")
    con.execute(
        """
        create table main_marts.dim_trial (
            trial_key varchar,
            nct_id varchar,
            indication_profile_id varchar,
            registry_url varchar,
            current_brief_title varchar,
            current_overall_status varchar,
            current_phase varchar,
            current_study_type varchar,
            current_lead_sponsor varchar,
            current_lead_sponsor_normalized varchar,
            start_date date,
            primary_completion_date date,
            completion_date date,
            study_first_post_date date,
            enrollment_count integer,
            enrollment_type varchar,
            current_has_results_flag boolean,
            record_quality_flag varchar,
            first_seen_snapshot_date date,
            latest_seen_snapshot_date date,
            active_in_latest_snapshot_flag boolean
        )
        """
    )
    con.execute(
        """
        insert into main_marts.dim_trial (
            trial_key, nct_id, indication_profile_id, registry_url, current_brief_title,
            current_overall_status, current_phase, current_lead_sponsor,
            study_first_post_date, enrollment_count
        ) values (
            'adrd:NCT00000001', 'NCT00000001', 'adrd', 'https://clinicaltrials.gov/study/NCT00000001',
            'ADRD Trial 1', 'RECRUITING', 'PHASE2', 'Test Lead Sponsor', '2024-01-01', 100
        ), (
            'adrd:NCT00000002', 'NCT00000002', 'adrd', 'https://clinicaltrials.gov/study/NCT00000002',
            'ADRD Trial 2', 'RECRUITING', 'PHASE2', 'Test Lead Sponsor 2', '2024-01-02', 120
        ), (
            'oncology_nsclc:NCT00000003', 'NCT00000003', 'oncology_nsclc', 'https://clinicaltrials.gov/study/NCT00000003',
            'NSCLC Trial 1', 'RECRUITING', 'PHASE3', 'Oncology Sponsor', '2024-01-03', 200
        )
        """
    )
    con.execute("""
        create table main_marts.bridge_trial_condition (
            trial_key varchar, nct_id varchar, condition_group varchar
        );
        insert into main_marts.bridge_trial_condition values
            ('adrd:NCT00000001', 'NCT00000001', 'alzheimers_disease'),
            ('adrd:NCT00000002', 'NCT00000002', 'alzheimers_disease'),
            ('oncology_nsclc:NCT00000003', 'NCT00000003', 'oncology_nsclc')
    """)
    con.execute(
        """
        create table main_marts.fct_trial_site (
            trial_key varchar,
            nct_id varchar,
            indication_profile_id varchar,
            snapshot_date date,
            state_normalized varchar
        )
        """
    )
    con.execute(
        """
        insert into main_marts.fct_trial_site values
            ('adrd:NCT00000001', 'NCT00000001', 'adrd', '2024-01-01', 'CA'),
            ('adrd:NCT00000002', 'NCT00000002', 'adrd', '2024-01-01', 'NY'),
            ('oncology_nsclc:NCT00000003', 'NCT00000003', 'oncology_nsclc', '2024-01-01', 'TX')
        """
    )
    con.execute(
        """
        create table main_marts.mart_trial_similarity (
            trial_similarity_key varchar,
            nct_id_a varchar,
            nct_id_b varchar,
            indication_profile_id varchar,
            similarity_score double,
            similarity_rank integer,
            same_condition integer,
            weight_same_condition double,
            weighted_same_condition double,
            same_phase integer,
            weight_same_phase double,
            weighted_same_phase double,
            geography_overlap integer,
            weight_geography_overlap double,
            weighted_geography_overlap double,
            intervention_type_overlap integer,
            weight_intervention_type_overlap double,
            weighted_intervention_type_overlap double,
            study_design_match integer,
            weight_study_design_match double,
            weighted_study_design_match double,
            eligibility_compatible integer,
            weight_eligibility_compatible double,
            weighted_eligibility_compatible double,
            enrollment_band_match integer,
            weight_enrollment_band_match double,
            weighted_enrollment_band_match double,
            similarity_explanation varchar
        )
        """
    )
    con.execute(
        """
        insert into main_marts.mart_trial_similarity (
            trial_similarity_key, nct_id_a, nct_id_b, indication_profile_id,
            similarity_score, similarity_rank,
            same_condition, weight_same_condition, weighted_same_condition,
            same_phase, weight_same_phase, weighted_same_phase,
            geography_overlap, weight_geography_overlap, weighted_geography_overlap,
            intervention_type_overlap,
            weight_intervention_type_overlap, weighted_intervention_type_overlap,
            study_design_match, weight_study_design_match, weighted_study_design_match,
            eligibility_compatible, weight_eligibility_compatible, weighted_eligibility_compatible,
            enrollment_band_match, weight_enrollment_band_match, weighted_enrollment_band_match,
            similarity_explanation
        ) values (
            'sim_key_1', 'NCT00000001', 'NCT00000002', 'adrd',
            0.8500, 1,
            1, 0.2, 0.2,
            1, 0.2, 0.2,
            0, 0.1, 0.0,
            1, 0.15, 0.15,
            1, 0.15, 0.15,
            1, 0.1, 0.1,
            1, 0.1, 0.1,
            'shared condition mapping; same phase'
        ), (
            'sim_key_2', 'NCT00000003', 'NCT00000001', 'oncology_nsclc',
            0.7500, 1,
            1, 0.2, 0.2,
            0, 0.2, 0.0,
            0, 0.1, 0.0,
            1, 0.15, 0.15,
            1, 0.15, 0.15,
            1, 0.1, 0.1,
            1, 0.1, 0.1,
            'shared condition mapping'
        ), (
            'sim_key_1b', 'NCT00000002', 'NCT00000001', 'adrd',
            0.8500, 1,
            1, 0.2, 0.2,
            1, 0.2, 0.2,
            0, 0.1, 0.0,
            1, 0.15, 0.15,
            1, 0.15, 0.15,
            1, 0.1, 0.1,
            1, 0.1, 0.1,
            'shared condition mapping; same phase'
        )
        """
    )
    con.close()

    from components import data

    monkeypatch.setattr(data, "warehouse_path", lambda: db_path)
    data._connection.clear()
    data.query.clear()
    data._table_exists.clear()
    data._has_column.clear()
    data.trial_similarity.clear()
    data.get_indication_profiles.clear()

    return db_path


def test_trial_explorer_modern_schema_includes_indication_column(temp_duckdb_modern_schema: Path):
    from components import data

    df = data.trial_explorer("adrd")
    assert "indication_profile_id" in df.columns
    assert set(df["indication_profile_id"]) == {"adrd"}
    df_onc = data.trial_explorer("oncology_nsclc")
    assert set(df_onc["indication_profile_id"]) == {"oncology_nsclc"}


def test_get_indication_profiles_modern_schema_resolves_display_names(
    temp_duckdb_modern_schema: Path,
):
    from components import data

    profiles = data.get_indication_profiles()
    assert len(profiles) == 2
    ids = {p["id"] for p in profiles}
    assert ids == {"adrd", "oncology_nsclc"}


def test_trial_similarity_modern_schema_returns_matches(temp_duckdb_modern_schema: Path):
    from components import data

    df = data.trial_similarity("adrd", "NCT00000001")
    assert not df.empty
    assert len(df) == 1
    assert df.iloc[0]["nct_id_b"] == "NCT00000002"
    assert df.iloc[0]["similarity_score"] == 0.85


def test_trial_similarity_page_smoke_modern_schema(temp_duckdb_modern_schema: Path):
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(ROOT / "dashboard/pages/8_Trial_Similarity.py"), default_timeout=30)
    at.run()
    assert not at.exception, at.exception[0].value if at.exception else ""
    # Should render the top comparable trials section
    assert any("Top comparable trials" in str(header.value) for header in at.subheader)


def test_guidance_format_condition_group():
    from components.guidance import format_condition_group

    assert format_condition_group("alzheimers_disease", "adrd") == "Alzheimer's Disease"
    assert format_condition_group("mild_cognitive_impairment", "adrd") == (
        "Mild Cognitive Impairment (MCI)"
    )
    assert format_condition_group("frontotemporal_dementia", "adrd") == (
        "Frontotemporal Dementia (FTD)"
    )
    assert format_condition_group("lewy_body_dementia", "adrd") == "Lewy Body Dementia (LBD)"
    assert format_condition_group("vascular_dementia", "adrd") == "Vascular Dementia (VaD)"
    assert format_condition_group("custom_neuro_group", "adrd") == "Custom Neuro Group"
    assert format_condition_group("nsclc_adenocarcinoma", "oncology_nsclc") == (
        "NSCLC Adenocarcinoma"
    )
    assert format_condition_group("nsclc_biomarker_targeted", "oncology_nsclc") == (
        "Biomarker-Targeted NSCLC"
    )
    assert format_condition_group("heart_failure_reduced_ef", "heart_failure") == (
        "Heart Failure with Reduced Ejection Fraction (HFrEF)"
    )


def test_guidance_all_pages_have_guidance():
    from components.guidance import PAGE_GUIDANCE

    expected_keys = {
        "overview",
        "priority_queue",
        "competition_landscape",
        "geography_trends",
        "site_overlap",
        "sponsor_landscape",
        "data_reliability",
        "trial_explorer",
        "trial_similarity",
    }
    assert set(PAGE_GUIDANCE.keys()) == expected_keys
    for _key, guide in PAGE_GUIDANCE.items():
        assert "title" in guide
        assert "what" in guide
        assert "why" in guide
        assert "how_to_verify" in guide


def test_page_guide_renders_neutral_guidance_across_profiles():
    from streamlit.testing.v1 import AppTest

    def page(profile_id):
        from components.guidance import render_page_guide

        render_page_guide("overview", profile_id)

    for profile_id in ("adrd", "oncology_nsclc"):
        at = AppTest.from_function(page, args=(profile_id,)).run()
        assert not at.exception, at.exception
        assert not any("ADRD clinical feasibility nuances" in m.value for m in at.markdown)
        assert any("What This Data Shows:" in m.value for m in at.markdown)
        assert any("How It Is Computed:" in m.value for m in at.markdown)
        assert any("How to Verify & Caveats:" in m.value for m in at.markdown)


def test_guidance_copy_does_not_overclaim():
    from components.guidance import PAGE_GUIDANCE, PAGE_PLAYBOOKS

    assert "guesswork" not in str(PAGE_PLAYBOOKS).lower()
    assert "guesswork" not in str(PAGE_GUIDANCE).lower()


def test_clinicaltrials_gov_search_url():
    from urllib.parse import parse_qs, urlparse

    from components.guidance import clinicaltrials_gov_search_url

    url_ad = clinicaltrials_gov_search_url("alzheimers_disease", "CA", "adrd")
    assert parse_qs(urlparse(url_ad).query)["cond"] == ["Alzheimer's Disease"]
    assert "locStr=CA" in url_ad
    assert "country=United%20States" in url_ad

    url_mci = clinicaltrials_gov_search_url("mild_cognitive_impairment", "NY", "adrd")
    assert parse_qs(urlparse(url_mci).query)["cond"] == ["Mild Cognitive Impairment (MCI)"]
    assert "locStr=NY" in url_mci

    url_fallback = clinicaltrials_gov_search_url("multiple_sclerosis", "TX", "multiple_sclerosis")
    assert parse_qs(urlparse(url_fallback).query)["cond"] == ["Multiple Sclerosis (MS)"]
    assert "locStr=TX" in url_fallback


def test_trials_for_segment_modern_schema(temp_duckdb_modern_schema: Path):
    from components import data

    df = data.trials_for_segment("adrd", "alzheimers_disease", "CA", "PHASE2")
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 1
    assert df.iloc[0]["nct_id"] == "NCT00000001"
    assert df.iloc[0]["lead_sponsor"] == "Test Lead Sponsor"
    assert df.iloc[0]["phase"] == "PHASE2"
    assert df.iloc[0]["overall_status"] == "RECRUITING"
    assert "registry_url" in df.columns

    # Non-existent segment returns empty DataFrame
    df_empty = data.trials_for_segment("adrd", "alzheimers_disease", "FL", "PHASE2")
    assert isinstance(df_empty, pd.DataFrame)
    assert df_empty.empty


def test_trials_for_segment_keeps_profile_keys_and_snapshot_scope(temp_duckdb_modern_schema: Path):
    from components import data

    with duckdb.connect(str(temp_duckdb_modern_schema)) as con:
        con.execute("""
            insert into main_marts.dim_trial
            select 'oncology_nsclc:NCT00000001', nct_id, 'oncology_nsclc', registry_url,
                   current_brief_title, current_overall_status, current_phase, current_study_type,
                   'Other profile sponsor', current_lead_sponsor_normalized, start_date,
                   primary_completion_date, completion_date, study_first_post_date,
                   enrollment_count, enrollment_type, current_has_results_flag, record_quality_flag,
                   first_seen_snapshot_date, latest_seen_snapshot_date,
                   active_in_latest_snapshot_flag
            from main_marts.dim_trial where nct_id = 'NCT00000001'
        """)
        con.execute("""
            insert into main_marts.bridge_trial_condition (trial_key, nct_id, condition_group)
            values ('oncology_nsclc:NCT00000001', 'NCT00000001', 'oncology_nsclc')
        """)
        con.execute("""
            insert into main_marts.fct_trial_site values
                ('oncology_nsclc:NCT00000001', 'NCT00000001', 'oncology_nsclc', '2024-02-01', 'TX')
        """)
    data.trials_for_segment.clear()
    adrd = data.trials_for_segment("adrd", "alzheimers_disease", "CA", "PHASE2")
    assert adrd["lead_sponsor"].tolist() == ["Test Lead Sponsor"]
    assert data.trials_for_segment("adrd", "alzheimers_disease", "TX", "PHASE2").empty
    assert data.trials_for_segment("adrd", "oncology_nsclc", "CA", "PHASE2").empty
    oncology = data.trials_for_segment("oncology_nsclc", "oncology_nsclc", "TX", "PHASE2")
    assert oncology["lead_sponsor"].tolist() == ["Other profile sponsor"]
