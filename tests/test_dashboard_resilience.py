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
    data.trials_for_segment.clear()

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

    df = data.trial_explorer()
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

    df = data.trial_similarity("NCT00000001")
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
            nct_id, indication_profile_id, registry_url, current_brief_title,
            current_overall_status, current_phase, current_lead_sponsor,
            study_first_post_date, enrollment_count
        ) values (
            'NCT00000001', 'adrd', 'https://clinicaltrials.gov/study/NCT00000001',
            'ADRD Trial 1', 'RECRUITING', 'PHASE2', 'Test Lead Sponsor', '2024-01-01', 100
        ), (
            'NCT00000002', 'adrd', 'https://clinicaltrials.gov/study/NCT00000002',
            'ADRD Trial 2', 'RECRUITING', 'PHASE2', 'Test Lead Sponsor 2', '2024-01-02', 120
        ), (
            'NCT00000003', 'oncology_nsclc', 'https://clinicaltrials.gov/study/NCT00000003',
            'NSCLC Trial 1', 'RECRUITING', 'PHASE3', 'Oncology Sponsor', '2024-01-03', 200
        )
        """
    )
    con.execute(
        """
        create table main_marts.bridge_trial_condition (
            trial_condition_key varchar,
            trial_key varchar,
            nct_id varchar,
            condition_group varchar,
            dementia_relevance_flag boolean,
            mapping_confidence varchar,
            source_condition_count integer
        )
        """
    )
    con.execute(
        """
        insert into main_marts.bridge_trial_condition (nct_id, condition_group) values
            ('NCT00000001', 'alzheimers_disease'),
            ('NCT00000002', 'alzheimers_disease'),
            ('NCT00000003', 'oncology_nsclc')
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
        insert into main_marts.fct_trial_site values
            ('NCT00000001', '2024-01-01', 'CA'),
            ('NCT00000002', '2024-01-01', 'NY'),
            ('NCT00000003', '2024-01-01', 'TX')
        """
    )
    con.execute(
        """
        create table main_marts.dim_geography (
            state_normalized varchar
        )
        """
    )
    con.execute("insert into main_marts.dim_geography values ('CA'), ('NY'), ('TX')")
    con.execute(
        """
        create table main_marts.mart_site_overlap (
            facility_normalized varchar,
            snapshot_date date,
            recruiting_trial_count integer,
            listed_trial_count integer
        )
        """
    )
    con.execute(
        "insert into main_marts.mart_site_overlap values ('Facility A', '2024-01-01', 2, 2)"
    )
    con.execute(
        """
        create table main_marts.fct_trial_snapshot (
            snapshot_date date
        )
        """
    )
    con.execute("insert into main_marts.fct_trial_snapshot values ('2024-01-01')")
    con.execute(
        """
        create table main_marts.mart_feasibility_priority_queue (
            snapshot_date date,
            condition_group varchar,
            state_normalized varchar,
            phase_normalized varchar,
            feasibility_review_priority_score double,
            priority_band varchar,
            priority_rank integer,
            recruiting_trial_count integer,
            listed_site_count integer,
            new_recruiting_90d integer,
            newly_posted_90d_proxy integer,
            has_multi_snapshot_history boolean,
            recent_growth_input integer,
            growth_uses_registry_proxy_flag boolean,
            sponsor_count integer,
            top_sponsor_share double,
            sponsor_hhi double,
            competition_signal_band varchar,
            site_overlap_share double,
            record_quality_ok_share double,
            data_confidence_share double,
            normalized_recruiting_trial_count double,
            normalized_recent_recruiting_growth double,
            normalized_sponsor_concentration double,
            normalized_site_overlap double,
            normalized_data_confidence_adjustment double,
            weight_recruiting_trial_count double,
            weight_recent_recruiting_growth double,
            weight_sponsor_concentration double,
            weight_site_overlap double,
            weight_data_confidence_adjustment double,
            weighted_recruiting_trial_count double,
            weighted_recent_recruiting_growth double,
            weighted_sponsor_concentration double,
            weighted_site_overlap double,
            weighted_data_confidence_adjustment double,
            priority_explanation varchar,
            interpretation_note varchar
        )
        """
    )
    con.execute(
        """
        insert into main_marts.mart_feasibility_priority_queue values (
            '2024-01-01', 'alzheimers_disease', 'CA', 'PHASE2',
            0.8500, 'priority_review', 1,
            2, 5, 1, 1, false, 1, true,
            2, 0.5, 0.5, 'elevated', 0.5, 1.0, 0.9,
            0.8, 0.5, 0.5, 0.5, 0.9,
            0.35, 0.20, 0.15, 0.15, 0.15,
            0.28, 0.10, 0.075, 0.075, 0.135,
            '2 recruiting listing(s) in segment',
            'Potential competition signal from public registry listings.'
        )
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
    data.trials_for_segment.clear()

    return db_path


def test_trial_explorer_modern_schema_includes_indication_column(temp_duckdb_modern_schema: Path):
    from components import data

    df = data.trial_explorer()
    assert "indication_profile_id" in df.columns
    assert set(df["indication_profile_id"]) == {"adrd", "oncology_nsclc"}


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

    df = data.trial_similarity("NCT00000001")
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

    assert format_condition_group("alzheimers_disease") == "Alzheimer's Disease"
    assert format_condition_group("mild_cognitive_impairment") == "Mild Cognitive Impairment (MCI)"
    assert format_condition_group("frontotemporal_dementia") == "Frontotemporal Dementia (FTD)"
    assert format_condition_group("lewy_body_dementia") == "Lewy Body Dementia (LBD)"
    assert format_condition_group("vascular_dementia") == "Vascular Dementia (VaD)"
    assert format_condition_group("custom_neuro_group") == "Custom Neuro Group"


def test_guidance_all_pages_have_playbooks():
    from components.guidance import PAGE_PLAYBOOKS

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
    assert set(PAGE_PLAYBOOKS.keys()) == expected_keys
    for _key, playbook in PAGE_PLAYBOOKS.items():
        assert "title" in playbook
        assert "what_it_shows" in playbook
        assert "how_to_analyze" in playbook
        assert "playbook" in playbook
        assert len(playbook["playbook"]) >= 3
        assert "adrd_context" in playbook


def test_clinicaltrials_gov_search_url():
    from components.guidance import clinicaltrials_gov_search_url

    url_ad = clinicaltrials_gov_search_url("alzheimers_disease", "CA")
    assert "cond=Alzheimer+Disease" in url_ad
    assert "locStr=CA" in url_ad
    assert "country=United%20States" in url_ad

    url_mci = clinicaltrials_gov_search_url("mild_cognitive_impairment", "NY")
    assert "cond=Mild+Cognitive+Impairment" in url_mci
    assert "locStr=NY" in url_mci

    url_fallback = clinicaltrials_gov_search_url("custom_unmapped", "TX")
    assert "cond=Alzheimer+Disease" in url_fallback
    assert "locStr=TX" in url_fallback


def test_trials_for_segment_modern_schema(temp_duckdb_modern_schema: Path):
    from components import data

    df = data.trials_for_segment("alzheimers_disease", "CA", "PHASE2")
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 1
    assert df.iloc[0]["nct_id"] == "NCT00000001"
    assert df.iloc[0]["lead_sponsor"] == "Test Lead Sponsor"
    assert df.iloc[0]["phase"] == "PHASE2"
    assert df.iloc[0]["overall_status"] == "RECRUITING"
    assert "registry_url" in df.columns

    # Non-existent segment returns empty DataFrame
    df_empty = data.trials_for_segment("alzheimers_disease", "FL", "PHASE2")
    assert isinstance(df_empty, pd.DataFrame)
    assert df_empty.empty


def test_priority_queue_page_smoke_modern_schema(temp_duckdb_modern_schema: Path):
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(ROOT / "dashboard/pages/1_Priority_Queue.py"), default_timeout=30)
    at.run()
    assert not at.exception, at.exception[0].value if at.exception else ""
    assert any("Ranked queue" in str(header.value) for header in at.subheader)


def test_overview_page_smoke_modern_schema(temp_duckdb_modern_schema: Path):
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(ROOT / "dashboard/app.py"), default_timeout=30)
    at.run()
    assert not at.exception, at.exception[0].value if at.exception else ""
    assert any(
        "Top of the Feasibility Review Priority Queue" in str(header.value)
        for header in at.subheader
    )
