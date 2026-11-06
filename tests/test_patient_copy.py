"""Authored patient-page wording (Issue #66): one test per mapping."""

import pytest

from src.api import patient_copy as copy


def _assert_plain(table: dict) -> None:
    assert table and all(isinstance(v, str) and v.strip() for v in table.values())


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("EARLY_PHASE1", "First test in people (Early Phase 1)"),
        ("PHASE1", "Early safety study (Phase 1)"),
        ("PHASE1/PHASE2", "Early safety and effectiveness study (Phase 1/Phase 2)"),
        ("PHASE2", "Study of whether a treatment works (Phase 2)"),
        (
            "PHASE2/PHASE3",
            "Study of whether a treatment works, in a larger group (Phase 2/Phase 3)",
        ),
        ("PHASE3", "Large study to confirm a treatment works (Phase 3)"),
        ("PHASE4", "Study of a treatment that is already approved (Phase 4)"),
        ("NOT_APPLICABLE", "This kind of study has no phase"),
        ("UNKNOWN", "Phase not stated"),
        (None, "Phase not stated"),
        ("PHASE5", "PHASE5"),  # unmapped registry value stays visible, never invented
    ],
)
def test_phase_label(value, expected):
    assert copy.phase_label(value) == expected


def test_status_mapping():
    _assert_plain(copy.STATUS)
    assert copy.lookup(copy.STATUS, "RECRUITING") == "Recruiting"
    assert copy.lookup(copy.STATUS, "TERMINATED") == "Stopped early"
    assert copy.lookup(copy.STATUS, "TEMPORARILY_NOT_AVAILABLE") == "Temporarily not available"
    assert copy.lookup(copy.STATUS, None) is None


def test_purpose_mapping():
    _assert_plain(copy.PURPOSE)
    assert copy.lookup(copy.PURPOSE, "PREVENTION") == "Preventing a condition"
    assert copy.lookup(copy.PURPOSE, "NEW_THING") == "New thing"


def test_allocation_mapping():
    _assert_plain(copy.ALLOCATION)
    assert "Chance decides" in copy.lookup(copy.ALLOCATION, "RANDOMIZED")
    assert "not randomized" in copy.lookup(copy.ALLOCATION, "NON_RANDOMIZED")
    assert "only one group" in copy.lookup(copy.ALLOCATION, "NA")


def test_masking_mapping():
    _assert_plain(copy.MASKING)
    assert copy.lookup(copy.MASKING, "NONE").startswith("Open label")
    for key, count in (
        ("SINGLE", "one"),
        ("DOUBLE", "two"),
        ("TRIPLE", "three"),
        ("QUADRUPLE", "four"),
    ):
        assert f"{count} group" in copy.lookup(copy.MASKING, key)


def test_intervention_type_mapping():
    _assert_plain(copy.INTERVENTION_TYPE)
    assert copy.lookup(copy.INTERVENTION_TYPE, "DRUG") == "Drug"
    assert copy.lookup(copy.INTERVENTION_TYPE, "DIETARY_SUPPLEMENT") == "Dietary supplement"


def test_placebo_mapping():
    assert set(copy.PLACEBO) == {True, False, None}
    assert "has a placebo group" in copy.PLACEBO[True]
    assert "does not list" in copy.PLACEBO[False]
    assert "does not say" in copy.PLACEBO[None]  # unknown is never asserted as "no placebo"


def test_sex_and_healthy_volunteer_mappings():
    _assert_plain(copy.SEX)
    assert copy.lookup(copy.SEX, "FEMALE") == "Female only"
    assert set(copy.HEALTHY_VOLUNTEERS) == {True, False, None}
    assert copy.HEALTHY_VOLUNTEERS[None] == "Not stated"


def test_age_group_mapping():
    _assert_plain(copy.AGE_GROUP)
    assert copy.lookup(copy.AGE_GROUP, "OLDER_ADULT") == "Older adult (65 and over)"


def test_age_text_uses_registry_wording():
    assert copy.age_text("18 Years", "65 Years") == "18 Years to 65 Years"
    assert copy.age_text("18 Years", None) == "18 Years and older"
    assert copy.age_text(None, "17 Years") == "Up to 17 Years"
    assert copy.age_text(None, None) == copy.NO_AGE_LIMITS


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        ("2024-01-10", "2024-01-30", "about a month or less"),
        ("2024-01", "2025-03", "about 14 months"),
        ("2024-01-10", "2026-06-01", "about 2 years"),
        ("2026-06", "2024-01", None),
        ("2024-01", None, None),
        (None, "2024-01", None),
        ("garbage", "2024-01", None),
    ],
)
def test_span_words(start, end, expected):
    assert copy.span_words(start, end) == expected


def test_standard_text_states_required_caveats():
    assert "not medical advice" in copy.PAGE_NOTICE
    assert "clinician and the study team" in copy.ELIGIBILITY_NOTE
    assert "official registry record" in copy.CONTACT_TEXT
