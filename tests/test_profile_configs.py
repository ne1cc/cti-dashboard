"""Real-config tests for the shipped profiles in config/profiles/.

Hermetic loader mechanics are covered in test_profiles.py; these tests pin
the semantics of the actual shipped profile files so config regressions
(e.g. a silently trimmed status filter) fail CI instead of surfacing in
downstream marts.
"""

import pytest

from src.profiles import ProfileRegistry


@pytest.fixture(scope="module")
def registry() -> ProfileRegistry:
    return ProfileRegistry()


def test_registry_discovers_all_shipped_profiles(registry: ProfileRegistry) -> None:
    ids = {p.profile_id for p in registry.all()}
    expected = {
        "adrd",
        "full_catalog",
        "oncology_nsclc",
        "oncology_breast",
        "oncology_colorectal",
        "oncology_prostate",
        "oncology_melanoma",
        "oncology_ovarian",
        "type2_diabetes",
        "heart_failure",
        "parkinsons",
        "rheumatoid_arthritis",
        "multiple_sclerosis",
    }
    assert expected <= ids


def test_status_filter_matches_scope_statuses(registry: ProfileRegistry) -> None:
    # Regression guard: the API request's filter.overallStatus and the
    # documented scope.statuses must stay in sync for every profile.
    for profile in registry.all():
        api_statuses = profile.config.api.query_params.get("filter.overallStatus") or []
        scope_statuses = profile.config.scope.get("statuses") or []
        assert sorted(api_statuses) == sorted(scope_statuses), (
            f"{profile.profile_id}: api {sorted(api_statuses)} != scope {sorted(scope_statuses)}"
        )


def test_condition_scoped_profiles_include_full_status_filter(
    registry: ProfileRegistry,
) -> None:
    # TERMINATED/WITHDRAWN/SUSPENDED/ENROLLING_BY_INVITATION feed the
    # why-stopped and competition analytics; a condition-scoped profile
    # must not silently narrow the filter to recruiting-only statuses.
    expected = {
        "RECRUITING",
        "ACTIVE_NOT_RECRUITING",
        "NOT_YET_RECRUITING",
        "COMPLETED",
        "TERMINATED",
        "WITHDRAWN",
        "SUSPENDED",
        "ENROLLING_BY_INVITATION",
    }
    for profile_id in ("adrd", "oncology_nsclc", *NEW_PROFILE_IDS):
        profile = registry.get(profile_id)
        api_statuses = set(profile.config.api.query_params["filter.overallStatus"])
        assert api_statuses == expected, profile_id


NEW_PROFILE_IDS = (
    "oncology_breast",
    "oncology_colorectal",
    "oncology_prostate",
    "oncology_melanoma",
    "oncology_ovarian",
    "type2_diabetes",
    "heart_failure",
    "parkinsons",
    "rheumatoid_arthritis",
    "multiple_sclerosis",
)


EXPECTED_QUERIES = {
    "oncology_breast": "Breast Cancer",
    "oncology_colorectal": "Colorectal Cancer",
    "oncology_prostate": "Prostate Cancer",
    "oncology_melanoma": "Melanoma",
    "oncology_ovarian": "Ovarian Cancer",
    "type2_diabetes": "Diabetes Mellitus, Type 2",
    "heart_failure": "Heart Failure",
    "parkinsons": "Parkinson Disease",
    "rheumatoid_arthritis": "Rheumatoid Arthritis",
    "multiple_sclerosis": "Multiple Sclerosis",
}


def test_new_profiles_are_refreshable_and_condition_scoped(registry: ProfileRegistry) -> None:
    refreshable = {profile.profile_id: profile for profile in registry.refreshable()}
    for profile_id in NEW_PROFILE_IDS:
        profile = refreshable[profile_id]
        assert profile.taxonomy is not None
        assert profile.config.api.query_params.get("query.cond") == EXPECTED_QUERIES[profile_id]
        assert profile.config.api.query_params["filter.advanced"] == (
            "AREA[StudyType]INTERVENTIONAL"
        )
        assert profile_id in str(profile.config.paths.bronze_api_responses)
        assert profile_id in str(profile.config.paths.bronze_manifests)


def test_new_profile_taxonomies_map_primary_conditions(registry: ProfileRegistry) -> None:
    cases = {
        "oncology_breast": ("Breast Cancer", "breast_cancer"),
        "oncology_colorectal": ("Colorectal Cancer", "colorectal_cancer"),
        "oncology_prostate": ("Prostate Cancer", "prostate_cancer"),
        "oncology_melanoma": ("Melanoma", "melanoma"),
        "oncology_ovarian": ("Ovarian Cancer", "ovarian_cancer"),
        "type2_diabetes": ("Diabetes Mellitus, Type 2", "type2_diabetes"),
        "heart_failure": ("Heart Failure", "heart_failure"),
        "parkinsons": ("Parkinson Disease", "parkinsons_disease"),
        "rheumatoid_arthritis": ("Rheumatoid Arthritis", "rheumatoid_arthritis"),
        "multiple_sclerosis": ("Multiple Sclerosis", "multiple_sclerosis"),
    }
    for profile_id, (condition, expected_group) in cases.items():
        taxonomy = registry.get(profile_id).taxonomy
        assert taxonomy is not None
        assert taxonomy.map_condition(condition).condition_group == expected_group
        assert taxonomy.map_condition("Unrelated condition").condition_group.startswith("other_")


def test_ingest_only_profiles_have_no_taxonomy(registry: ProfileRegistry) -> None:
    for profile in registry.all():
        if profile.ingest_only:
            assert profile.taxonomy is None, profile.profile_id
        else:
            assert profile.taxonomy is not None, profile.profile_id


def test_profile_bronze_paths_are_profile_scoped(registry: ProfileRegistry) -> None:
    for profile in registry.all():
        assert profile.profile_id in str(profile.config.paths.bronze_api_responses), (
            profile.profile_id
        )
        assert profile.profile_id in str(profile.config.paths.bronze_manifests), profile.profile_id


def test_nsclc_taxonomy_maps_histologies(registry: ProfileRegistry) -> None:
    taxonomy = registry.get("oncology_nsclc").taxonomy
    assert taxonomy is not None

    unspecified = taxonomy.map_condition("Non-Small Cell Lung Cancer")
    assert unspecified.condition_group == "nsclc_unspecified"
    assert unspecified.mapping_confidence == "high"

    biomarker = taxonomy.map_condition("EGFR Mutated Non-Small Cell Lung Cancer")
    assert biomarker.condition_group == "nsclc_biomarker_targeted"
    assert biomarker.mapping_confidence == "medium"

    adenocarcinoma = taxonomy.map_condition("Lung Adenocarcinoma")
    assert adenocarcinoma.condition_group == "nsclc_adenocarcinoma"

    unrelated = taxonomy.map_condition("Pleural Effusion")
    assert unrelated.condition_group == "other_thoracic_oncology"
    assert unrelated.mapping_confidence == "low"


def test_nsclc_query_targets_nsclc(registry: ProfileRegistry) -> None:
    profile = registry.get("oncology_nsclc")
    assert profile.config.api.query_params["query.cond"] == "Non-Small Cell Lung Cancer"
    assert profile.config.api.query_params["filter.advanced"] == "AREA[StudyType]INTERVENTIONAL"
