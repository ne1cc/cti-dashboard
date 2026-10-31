"""Unit tests for baseline-demographics extraction (issue #74).

The fixture page tests/fixtures/bronze_snapshot_results/page=00001.json models
five real ClinicalTrials.gov studies' resultsSection.baselineCharacteristicsModule
shapes as captured from the live API v2 on 2026-10-31: canonical NIH/OMB
categories (NCT03723707), a combined "Race/Ethnicity, Customized" table with
labels in category titles (NCT05310071), two combined tables with labels in
class titles (NCT03491150), a "Race and Ethnicity Not Collected" marker
(NCT03451591), and "Race:/Ethnicity:"-prefixed custom labels (NCT05236725).
NCT IDs are synthetic per the repo's fixture convention; every label and
measurement value mirrors a real capture.
"""

import json
from pathlib import Path

import pytest

from src.transform.flatten_studies import (
    _participant_count,
    flatten_study,
    iter_bronze_studies,
)
from src.transform.normalize_conditions import get_taxonomy
from src.transform.normalize_locations import get_geography_rules

FIXTURE_PAGE = Path(__file__).parent / "fixtures/bronze_snapshot_results/page=00001.json"


@pytest.fixture(scope="module")
def results_studies() -> dict[str, dict]:
    page = json.loads(FIXTURE_PAGE.read_text(encoding="utf-8"))
    return {
        study["protocolSection"]["identificationModule"]["nctId"]: study
        for study in page["studies"]
    }


def _flatten(study: dict) -> dict[str, list[dict]]:
    return flatten_study(
        study,
        ingestion_run_id="run_test",
        snapshot_timestamp_utc="2026-09-01T12:00:00+00:00",
        taxonomy=get_taxonomy(),
        geography=get_geography_rules(),
    )


def _rows(study: dict, measure_title: str) -> list[dict]:
    return [
        row
        for row in _flatten(study)["silver_trial_baseline_demographics"]
        if row["measure_title"] == measure_title
    ]


class TestCanonicalOMB:
    """NCT10000001: standard NIH/OMB race, ethnicity, sex, and age measures."""

    def test_row_count_and_entities_present(self, results_studies) -> None:
        rows = _flatten(results_studies["NCT10000001"])["silver_trial_baseline_demographics"]
        # age 1 category x 2 groups + sex 2x2 + ethnicity 3x2 + race 7x2 = 26.
        assert len(rows) == 26

    def test_group_titles_resolved_from_module_groups(self, results_studies) -> None:
        rows = _rows(results_studies["NCT10000001"], "Sex: Female, Male")
        by_group = {row["group_id"]: row for row in rows if row["category_title"] == "Female"}
        assert by_group["BG000"]["group_title"] == "Donepezil 10 mg maintenance"
        assert by_group["BG001"]["group_title"] == "Placebo"
        assert by_group["BG000"]["participant_count"] == 30
        assert by_group["BG001"]["participant_count"] == 38

    def test_group_denominator_from_denoms_module(self, results_studies) -> None:
        rows = _rows(results_studies["NCT10000001"], "Ethnicity (NIH/OMB)")
        assert {row["group_denominator"] for row in rows} == {68, 78}

    def test_age_median_values_are_not_participant_counts(self, results_studies) -> None:
        rows = _rows(results_studies["NCT10000001"], "Age, Continuous")
        assert all(row["participant_count"] is None for row in rows)
        assert {row["value_raw"] for row in rows} == {"79.5", "78"}
        assert {row["value_spread"] for row in rows} == {"7.2", "6.9"}
        assert {row["param_type"] for row in rows} == {"MEDIAN"}
        assert {row["unit_of_measure"] for row in rows} == {"years"}

    def test_labels_stored_exactly_as_reported(self, results_studies) -> None:
        labels = {
            row["category_title"] for row in _rows(results_studies["NCT10000001"], "Race (NIH/OMB)")
        }
        assert labels == {
            "American Indian or Alaska Native",
            "Asian",
            "Native Hawaiian or Other Pacific Islander",
            "Black or African American",
            "White",
            "More than one race",
            "Unknown or Not Reported",
        }

    def test_measure_and_category_ordinals(self, results_studies) -> None:
        rows = _rows(results_studies["NCT10000001"], "Sex: Female, Male")
        assert {row["measure_index"] for row in rows} == {1}
        assert {row["category_index"] for row in rows} == {0, 1}


class TestCombinedCategoryTitled:
    """NCT10000002: one combined table, labels in category titles."""

    def test_custom_labels_preserved_verbatim(self, results_studies) -> None:
        labels = {
            row["category_title"]
            for row in _rows(results_studies["NCT10000002"], "Race/Ethnicity, Customized")
        }
        assert "Not Reported Due to Confidentiality Regulations" in labels
        assert "Multiple: American Indian or Alaska Native and White" in labels
        assert "Other" in labels

    def test_number_participants_values_parse_as_counts(self, results_studies) -> None:
        rows = [
            row
            for row in _rows(results_studies["NCT10000002"], "Race/Ethnicity, Customized")
            if row["category_title"] == "White"
        ]
        assert {row["participant_count"] for row in rows} == {35, 36}


class TestCombinedClassTitled:
    """NCT10000003: two combined tables whose labels live in class titles."""

    def test_class_title_used_when_category_title_missing(self, results_studies) -> None:
        rows = [
            row
            for row in _flatten(results_studies["NCT10000003"])[
                "silver_trial_baseline_demographics"
            ]
            if row["measure_title"] == "Race/Ethnicity, Customized"
        ]
        ethnicity_rows = [row for row in rows if row["class_title"] == "Hispanic or Latino"]
        assert ethnicity_rows
        assert all(row["category_title"] is None for row in ethnicity_rows)
        assert {row["participant_count"] for row in ethnicity_rows} == {3}


class TestNotCollectedMarker:
    """NCT10000004: explicit "Race and Ethnicity Not Collected" measure."""

    def test_marker_rows_kept_with_null_labels(self, results_studies) -> None:
        rows = _rows(results_studies["NCT10000004"], "Race and Ethnicity Not Collected")
        assert len(rows) == 2
        assert all(row["category_title"] is None and row["class_title"] is None for row in rows)
        assert {row["participant_count"] for row in rows} == {30, 32}


class TestPrefixedCustomLabels:
    """NCT10000005: "Race: ..." / "Ethnicity: ..." prefixed custom categories."""

    def test_prefixed_labels_stored_verbatim(self, results_studies) -> None:
        labels = {
            row["category_title"]
            for row in _rows(results_studies["NCT10000005"], "Race/Ethnicity, Customized")
        }
        assert {"Race: White", "Ethnicity: Hispanic or Latino", "Ethnicity: Prefer not to say"} <= (
            labels
        )

    def test_mean_age_not_parsed_as_count(self, results_studies) -> None:
        rows = _rows(results_studies["NCT10000005"], "Age, Continuous")
        assert all(row["participant_count"] is None for row in rows)
        assert {row["param_type"] for row in rows} == {"MEAN"}


class TestStudyWithoutResults:
    def test_no_results_section_yields_no_rows(self) -> None:
        page = json.loads(
            (Path(__file__).parent / "fixtures/bronze_snapshot/page=00001.json").read_text(
                encoding="utf-8"
            )
        )
        study = page["studies"][0]
        assert study.get("resultsSection") is None
        assert _flatten(study)["silver_trial_baseline_demographics"] == []


class TestParticipantCountParsing:
    @pytest.mark.parametrize(
        ("param_type", "unit", "value", "expected"),
        [
            ("COUNT_OF_PARTICIPANTS", "Participants", "68", 68),
            ("NUMBER", "participants", "12", 12),
            ("NUMBER", "Participants", "79.5", None),
            ("NUMBER", "years", "68", None),
            ("MEDIAN", "Participants", "79.5", None),
            ("MEAN", "Participants", "abc", None),
            (None, "Participants", "68", None),
            ("COUNT_OF_PARTICIPANTS", None, "68", None),
            ("COUNT_OF_PARTICIPANTS", "Participants", None, None),
        ],
    )
    def test_gating(self, param_type, unit, value, expected) -> None:
        assert _participant_count(param_type, unit, value) is expected


class TestFixturePageSanity:
    def test_all_studies_have_results(self) -> None:
        studies = list(iter_bronze_studies(FIXTURE_PAGE.parent))
        assert len(studies) == 5
        for study in studies:
            assert study["hasResults"] is True
            module = study.get("resultsSection", {}).get("baselineCharacteristicsModule")
            nct_id = study["protocolSection"]["identificationModule"]["nctId"]
            assert module, f"{nct_id} lacks results"
