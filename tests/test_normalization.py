from src.transform.flatten_studies import flatten_study, normalize_phases, trial_quality_flags
from src.transform.normalize_conditions import get_taxonomy
from src.transform.normalize_locations import get_geography_rules
from src.utils.dates import parse_partial_date
from src.utils.text import normalize_text

TAXONOMY = get_taxonomy()
GEOGRAPHY = get_geography_rules()


# --- text + date helpers -----------------------------------------------------


def test_normalize_text_cleans_punctuation_and_case():
    assert normalize_text("  Alzheimer's  Disease ") == "alzheimers disease"
    assert normalize_text("Early-Onset, AD") == "early onset ad"
    assert normalize_text(None) is None
    assert normalize_text("   ") is None


def test_parse_partial_date_handles_all_precisions():
    assert str(parse_partial_date("2020-06-15")) == "2020-06-15"
    assert str(parse_partial_date("2020-06")) == "2020-06-01"
    assert str(parse_partial_date("2020")) == "2020-01-01"
    assert parse_partial_date("junk") is None
    assert parse_partial_date(None) is None


# --- condition taxonomy ------------------------------------------------------


def test_alzheimers_exact_match_high_confidence():
    mapping = TAXONOMY.map_condition("Alzheimer's Disease")
    assert mapping.condition_group == "alzheimers_disease"
    assert mapping.dementia_relevance_flag is True
    assert mapping.mapping_confidence == "high"


def test_specific_group_wins_over_dementia_catchall():
    mapping = TAXONOMY.map_condition("Vascular Dementia")
    assert mapping.condition_group == "vascular_dementia"
    mapping = TAXONOMY.map_condition("Dementia With Lewy Bodies")
    assert mapping.condition_group == "lewy_body_dementia"


def test_unspecified_dementia_falls_to_catchall():
    mapping = TAXONOMY.map_condition("Senile Dementia")
    assert mapping.condition_group == "dementia_unspecified"
    assert mapping.dementia_relevance_flag is True


def test_unrelated_condition_gets_default_low_confidence():
    mapping = TAXONOMY.map_condition("Type 2 Diabetes")
    assert mapping.condition_group == "non_dementia_other"
    assert mapping.dementia_relevance_flag is False
    assert mapping.mapping_confidence == "low"


# --- geography ---------------------------------------------------------------


def test_state_full_name_and_abbreviation_normalize():
    assert GEOGRAPHY.normalize_state("California") == "CA"
    assert GEOGRAPHY.normalize_state("ca") == "CA"
    assert GEOGRAPHY.normalize_state("District of Columbia") == "DC"


def test_non_us_state_is_unknown_and_unusable():
    norm = GEOGRAPHY.normalize_location("Site A", "Toronto", "Ontario", "Canada")
    assert norm.state_normalized == "UNKNOWN"
    assert norm.us_location_flag is False
    assert norm.usable_geography_flag is False


def test_us_location_usable_and_scoped():
    norm = GEOGRAPHY.normalize_location("Mayo Clinic", "Rochester", "Minnesota", "United States")
    assert norm.state_normalized == "MN"
    assert norm.us_location_flag is True
    assert norm.usable_geography_flag is True
    assert norm.geo_scope == "facility"


# --- phases + quality flags --------------------------------------------------


def test_normalize_phases():
    assert normalize_phases(None) == (None, "UNKNOWN")
    assert normalize_phases(["PHASE2"]) == ("PHASE2", "PHASE2")
    assert normalize_phases(["PHASE1", "PHASE2"]) == ("PHASE1|PHASE2", "PHASE1/PHASE2")
    assert normalize_phases(["NA"]) == ("NA", "NOT_APPLICABLE")


def test_quality_flag_start_after_completion():
    flag = trial_quality_flags(
        {
            "overall_status": "COMPLETED",
            "study_type": "INTERVENTIONAL",
            "start_date": "2020-05",
            "completion_date": "2019-01",
        }
    )
    assert "start_after_completion" in flag


def test_quality_flag_ok():
    flag = trial_quality_flags(
        {
            "overall_status": "RECRUITING",
            "study_type": "INTERVENTIONAL",
            "start_date": "2020-01",
            "completion_date": "2022-01",
        }
    )
    assert flag == "ok"


# --- full flatten ------------------------------------------------------------

FIXTURE_STUDY = {
    "hasResults": True,
    "protocolSection": {
        "identificationModule": {
            "nctId": "NCT00000001",
            "briefTitle": "Test Trial",
            "officialTitle": "An Official Test Trial",
        },
        "statusModule": {
            "overallStatus": "RECRUITING",
            "statusVerifiedDate": "2026-01",
            "expandedAccessInfo": {"hasExpandedAccess": False},
            "startDateStruct": {"date": "2025-03"},
            "primaryCompletionDateStruct": {"date": "2027-06", "type": "ESTIMATED"},
            "completionDateStruct": {"date": "2027-12", "type": "ESTIMATED"},
            "studyFirstPostDateStruct": {"date": "2025-01-10", "type": "ACTUAL"},
            "lastUpdatePostDateStruct": {"date": "2026-06-01", "type": "ACTUAL"},
        },
        "sponsorCollaboratorsModule": {
            "responsibleParty": {"type": "SPONSOR"},
            "leadSponsor": {"name": "Acme Pharma, Inc.", "class": "INDUSTRY"},
            "collaborators": [{"name": "University Hospital", "class": "OTHER"}],
        },
        "designModule": {
            "studyType": "INTERVENTIONAL",
            "phases": ["PHASE2", "PHASE3"],
            "designInfo": {
                "allocation": "RANDOMIZED",
                "primaryPurpose": "TREATMENT",
            },
            "enrollmentInfo": {"count": 300, "type": "ESTIMATED"},
        },
        "conditionsModule": {"conditions": ["Alzheimer Disease", "Mild Cognitive Impairment"]},
        "armsInterventionsModule": {
            "interventions": [{"type": "DRUG", "name": "Compound-X", "description": "Oral tablet"}]
        },
        "outcomesModule": {
            "primaryOutcomes": [
                {"measure": "ADAS-Cog change", "description": "d", "timeFrame": "52 weeks"}
            ],
            "secondaryOutcomes": [{"measure": "CDR-SB change", "timeFrame": "52 weeks"}],
        },
        "eligibilityModule": {
            "eligibilityCriteria": "Inclusion: ...",
            "healthyVolunteers": False,
            "sex": "ALL",
            "minimumAge": "50 Years",
            "maximumAge": "85 Years",
        },
        "contactsLocationsModule": {
            "locations": [
                {
                    "facility": "Memory Center",
                    "city": "Phoenix",
                    "state": "Arizona",
                    "zip": "85001",
                    "country": "United States",
                    "geoPoint": {"lat": 33.44, "lon": -112.07},
                },
                {
                    "facility": "Overseas Clinic",
                    "city": "Toronto",
                    "state": "Ontario",
                    "country": "Canada",
                },
            ]
        },
    },
}


def test_flatten_study_produces_all_entities():
    rows = flatten_study(FIXTURE_STUDY, "run1", "2026-07-24T00:00:00+00:00", TAXONOMY, GEOGRAPHY)

    trial = rows["silver_trials"][0]
    assert trial["nct_id"] == "NCT00000001"
    assert trial["overall_status"] == "RECRUITING"
    assert trial["phase_normalized"] == "PHASE2/PHASE3"
    assert trial["enrollment_count"] == 300
    assert trial["allocation"] == "RANDOMIZED"
    assert trial["primary_purpose"] == "TREATMENT"
    assert trial["lead_sponsor_name"] == "Acme Pharma, Inc."
    assert trial["has_results_flag"] is True
    assert trial["record_quality_flag"] == "ok"
    assert trial["source_json_hash"]
    # Profile id defaults to "adrd" when not supplied
    assert trial["indication_profile_id"] == "adrd"

    assert len(rows["silver_trial_conditions"]) == 2
    groups = {r["condition_group"] for r in rows["silver_trial_conditions"]}
    assert groups == {"alzheimers_disease", "mild_cognitive_impairment"}
    # Profile id propagates to all child entities via base dict
    for row in rows["silver_trial_conditions"]:
        assert row["indication_profile_id"] == "adrd"

    assert len(rows["silver_trial_sponsors"]) == 2
    roles = {r["sponsor_role"] for r in rows["silver_trial_sponsors"]}
    assert roles == {"lead_sponsor", "collaborator"}

    locations = rows["silver_trial_locations"]
    assert len(locations) == 2
    us = next(loc for loc in locations if loc["country"] == "United States")
    assert us["state_normalized"] == "AZ" and us["usable_geography_flag"]
    ca = next(loc for loc in locations if loc["country"] == "Canada")
    assert ca["usable_geography_flag"] is False  # preserved, flagged

    assert len(rows["silver_trial_interventions"]) == 1
    assert rows["silver_trial_interventions"][0]["intervention_normalized"] == "compound x"

    outcomes = rows["silver_trial_outcomes"]
    assert [(o["outcome_type"], o["outcome_index"]) for o in outcomes] == [
        ("primary", 0),
        ("secondary", 0),
    ]


def test_flatten_preserves_duplicate_location_ordinals_and_partial_dates():
    import copy

    study = copy.deepcopy(FIXTURE_STUDY)
    locations = study["protocolSection"]["contactsLocationsModule"]["locations"]
    locations.append(copy.deepcopy(locations[0]))
    original = copy.deepcopy(study)
    rows = flatten_study(study, "run1", "2026-10-02T00:00:00+00:00", TAXONOMY, GEOGRAPHY)
    assert [row["location_ordinal"] for row in rows["silver_trial_locations"]] == [0, 1, 2]
    assert rows["silver_trials"][0]["status_verified_date"] == "2026-01"
    assert rows["silver_trials"][0]["start_date"] == "2025-03"
    assert study == original
    assert rows["silver_trials"][0]["retrieved_at_utc"] is None
