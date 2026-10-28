"""Tests for deterministic, lossless eligibility section splitting."""

import pytest

from src.transform.eligibility_sections import (
    reconstruct,
    split_eligibility_sections,
    strip_bullet_markers,
)


def _tokens_after_marker_strip(text: str) -> list[str]:
    return strip_bullet_markers(text).split()


def test_canonical_sections_and_bullets() -> None:
    text = (
        "Inclusion Criteria:\n\n"
        "* Age 55 to 85 years\n"
        "* Confirmed biomarker evidence of amyloid\n\n"
        "Exclusion Criteria:\n\n"
        "* Current significant psychiatric disorder\n"
    )
    parsed = split_eligibility_sections(text)

    assert parsed is not None
    assert parsed.parsed is True
    assert parsed.inclusion == ["Age 55 to 85 years", "Confirmed biomarker evidence of amyloid"]
    assert parsed.exclusion == ["Current significant psychiatric disorder"]
    assert parsed.unclassified == []


def test_numbered_items() -> None:
    text = (
        "Inclusion Criteria:\n"
        "1. Age 55 to 85 years at screening.\n"
        "2. Meets NIA-AA criteria for probable Alzheimer disease.\n\n"
        "Exclusion Criteria:\n"
        "1. Non-Alzheimer neurodegenerative disorder.\n"
    )
    parsed = split_eligibility_sections(text)

    assert parsed is not None
    assert parsed.inclusion == [
        "Age 55 to 85 years at screening.",
        "Meets NIA-AA criteria for probable Alzheimer disease.",
    ]
    assert parsed.exclusion == ["Non-Alzheimer neurodegenerative disorder."]


def test_parenthesized_numbered_items() -> None:
    text = "Inclusion Criteria:\n(1) Age 18 or older\n(2) Written informed consent\n"
    parsed = split_eligibility_sections(text)

    assert parsed is not None
    assert parsed.inclusion == ["Age 18 or older", "Written informed consent"]


def test_wrapped_continuation_lines_stay_one_item() -> None:
    text = (
        "Inclusion Criteria:\n"
        "* Diagnosis of mild cognitive impairment due to Alzheimer's\n"
        "  disease confirmed by biomarkers\n"
        "* Reliable study partner\n"
    )
    parsed = split_eligibility_sections(text)

    assert parsed is not None
    assert parsed.inclusion == [
        "Diagnosis of mild cognitive impairment "
        "due to Alzheimer's\ndisease confirmed by biomarkers",
        "Reliable study partner",
    ]


def test_decimal_dose_is_not_a_numbered_bullet() -> None:
    text = "Inclusion Criteria:\n* Dose 1.5 mg daily\n"
    parsed = split_eligibility_sections(text)

    assert parsed is not None
    assert parsed.inclusion == ["Dose 1.5 mg daily"]


def test_colonless_heading() -> None:
    text = "Exclusion Criteria\n* Pregnant\n"
    parsed = split_eligibility_sections(text)

    assert parsed is not None
    assert parsed.parsed is True
    assert parsed.exclusion == ["Pregnant"]
    assert parsed.inclusion == []


@pytest.mark.parametrize(
    "heading",
    [
        "INCLUSION CRITERIA:",
        "* INCLUSION CRITERIA:",
        "Inclusion criteria:",
        "Inclusion Criteria",
        "Key Inclusion Criteria:",
        "General inclusion criteria",
        "Core Study: Inclusion Criteria",
        "Extension Phase: Exclusion Criteria:",
        "Exclusion Criteria (for all patients)",
        "Exclusion Criteria (applies to all patients):",
        "Inclusion Criteria for mild-moderate AD patients",
        "INCLUSION CRITERIA for Healthy Volunteers (arm C)",
        "Inclusion Criteria, Participants must meet all of the following:",
        "**Inclusion Criteria:**",
    ],
)
def test_heading_variants(heading: str) -> None:
    text = f"{heading}\n* Age 18 or older\n"
    parsed = split_eligibility_sections(text)

    assert parsed is not None
    assert parsed.parsed is True
    assert "Age 18 or older" in parsed.inclusion or "Age 18 or older" in parsed.exclusion


@pytest.mark.parametrize(
    "prose",
    [
        "Meeting inclusion criteria was required for entry.",
        "The main inclusion criteria include but are not limited to the following:\n* Item one",
        "* Other protocol-defined Inclusion/Exclusion criteria apply.",
        "Note: other protocol defined inclusion / exclusion criteria apply",
        "-There are no exclusion criteria to participate in MK-3475-587.",
        "Patients met inclusion criteria and were enrolled in the study.",
    ],
)
def test_prose_mentioning_criteria_is_not_a_heading(prose: str) -> None:
    parsed = split_eligibility_sections(prose)

    assert parsed is not None
    assert parsed.parsed is False
    assert parsed.inclusion == []
    assert parsed.exclusion == []


def test_inline_heading_text_becomes_first_item() -> None:
    text = "Inclusion Criteria: Age 55 to 85.\nExclusion Criteria: none."
    parsed = split_eligibility_sections(text)

    assert parsed is not None
    assert parsed.parsed is True
    assert parsed.inclusion == ["Age 55 to 85."]
    assert parsed.exclusion == ["none."]


def test_missing_headings_fall_back_to_unclassified() -> None:
    text = "Sub-study 1 (Healthy individuals):\n\n* Age 23-50 years\n* BMI 20.0-26.9\n"
    parsed = split_eligibility_sections(text)

    assert parsed is not None
    assert parsed.parsed is False
    assert parsed.inclusion == []
    assert parsed.exclusion == []
    assert parsed.unclassified == [
        "Sub-study 1 (Healthy individuals):",
        "Age 23-50 years",
        "BMI 20.0-26.9",
    ]


def test_preamble_before_first_heading_is_unclassified() -> None:
    text = "Note: review all criteria with the study team.\nInclusion Criteria:\n* Age 18+\n"
    parsed = split_eligibility_sections(text)

    assert parsed is not None
    assert parsed.parsed is True
    assert parsed.unclassified == ["Note: review all criteria with the study team."]
    assert parsed.inclusion == ["Age 18+"]


def test_placeholder_blob_falls_back() -> None:
    parsed = split_eligibility_sections("* Pending Web Eligibility")

    assert parsed is not None
    assert parsed.parsed is False
    assert parsed.unclassified == ["Pending Web Eligibility"]


@pytest.mark.parametrize("text", [None, "", "   \n  "])
def test_missing_or_blank_text_returns_none(text: str | None) -> None:
    assert split_eligibility_sections(text) is None


def test_repeated_headings_accumulate() -> None:
    text = (
        "Baseline: Inclusion Criteria:\n* Age 18+\n\n"
        "Extension Phase: Inclusion Criteria:\n* Completed baseline\n"
    )
    parsed = split_eligibility_sections(text)

    assert parsed is not None
    assert parsed.inclusion == ["Age 18+", "Completed baseline"]


def test_no_text_is_dropped_inclusion_and_exclusion_share_a_line() -> None:
    text = (
        "Inclusion Criteria: diagnosis of mild Alzheimer disease. "
        "Exclusion: severe renal impairment."
    )
    parsed = split_eligibility_sections(text)

    assert parsed is not None
    # The whole blob stays somewhere; nothing vanishes.
    joined = " ".join([*parsed.inclusion, *parsed.exclusion, *parsed.unclassified])
    assert "mild Alzheimer disease" in joined
    assert "severe renal impairment" in joined


def test_lossless_reconstruction_on_gnarly_blob() -> None:
    text = (
        "Key Inclusion Criteria:\n\n"
        "* CKD eGFR 20 \\<45 mL/min/1.73m²\n"
        "  (irrespective of diabetes status);\n"
        "1. Age ≥18 years\n"
        "\n"
        "* Pending Web Eligibility footnote\n"
        "\n"
        "Exclusion Criteria (applies to all patients):\n"
        "* HbA1c \\> 53 mmol/mol\n"
    )
    parsed = split_eligibility_sections(text)

    assert parsed is not None
    assert reconstruct(parsed).split() == _tokens_after_marker_strip(text)


def test_exclusion_before_inclusion_order_preserved() -> None:
    text = "Exclusion Criteria:\n* Prior therapy\nInclusion Criteria:\n* Age 18+\n"
    parsed = split_eligibility_sections(text)

    assert parsed is not None
    assert parsed.exclusion == ["Prior therapy"]
    assert parsed.inclusion == ["Age 18+"]


def test_midline_exclusion_heading_splits_sections() -> None:
    # Real-world shape (NCT07259369): the Exclusion heading sits mid-line after
    # the last inclusion criterion.
    text = (
        "Inclusion Criteria: The patients are\n\n"
        "* 18 years old or over\n"
        "* can provide written informed consent. Exclusion Criteria: The patients who have\n"
        "* advanced cancer\n"
    )
    parsed = split_eligibility_sections(text)

    assert parsed is not None
    assert parsed.parsed is True
    assert "can provide written informed consent." in parsed.inclusion
    assert "advanced cancer" in parsed.exclusion
    assert parsed.exclusion[0] == "The patients who have"
    assert reconstruct(parsed).split() == _tokens_after_marker_strip(text)


def test_prefix_style_whole_line_heading_not_split_midline() -> None:
    text = "Baseline: Inclusion Criteria:\n* Age 18+\n"
    parsed = split_eligibility_sections(text)

    assert parsed is not None
    assert parsed.sections[0].heading_raw == "Baseline: Inclusion Criteria:"
    assert parsed.unclassified == []


def test_inline_heading_with_parenthetical_qualifier() -> None:
    # Real-world shape (NCT07812337): 'Exclusion Criteria (PCP): none'.
    text = "Inclusion Criteria:\n* Age 50 or older\n\nExclusion Criteria (PCP): none\n"
    parsed = split_eligibility_sections(text)

    assert parsed is not None
    assert parsed.parsed is True
    assert parsed.inclusion == ["Age 50 or older"]
    assert parsed.exclusion == ["none"]
    assert parsed.sections[1].heading_raw == "Exclusion Criteria (PCP):"


@pytest.mark.parametrize(
    "line",
    [
        "* Prior exclusion from study:",
        "* Previous inclusion in this study:",
        "* Inclusion criteria were met previously:",
    ],
)
def test_bulleted_colon_line_with_keyword_is_not_a_heading(line: str) -> None:
    text = f"Exclusion Criteria:\n{line}\n* x\n"
    parsed = split_eligibility_sections(text)

    assert parsed is not None
    assert parsed.parsed is True
    assert parsed.inclusion == []
    assert line.removeprefix("* ").removeprefix("- ") in parsed.exclusion
    assert "x" in parsed.exclusion


def test_blank_lines_do_not_separate_items() -> None:
    # Documented behavior: blank lines are ignored; only bullet markers start
    # new items, so consecutive non-bullet lines join into one item.
    text = "Inclusion Criteria:\n\nAge 18 or older\n\nSigned consent\n"
    parsed = split_eligibility_sections(text)

    assert parsed is not None
    assert parsed.inclusion == ["Age 18 or older\nSigned consent"]


def test_trailing_note_joins_last_item() -> None:
    text = "Exclusion Criteria:\n* Prior therapy\n\nNote: discuss with the study team.\n"
    parsed = split_eligibility_sections(text)

    assert parsed is not None
    assert parsed.exclusion == ["Prior therapy\nNote: discuss with the study team."]


def test_nested_bullets_flatten_to_sibling_items() -> None:
    # Documented limitation: indentation is ignored, so sub-bullets become
    # sibling items and any-of grouping is not preserved structurally.
    text = "Inclusion Criteria:\n* Has one of:\n  * A\n  * B\n"
    parsed = split_eligibility_sections(text)

    assert parsed is not None
    assert parsed.inclusion == ["Has one of:", "A", "B"]
