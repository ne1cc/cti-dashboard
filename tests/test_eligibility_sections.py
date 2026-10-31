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


def test_bulleted_canonical_heading_without_colon_recognized() -> None:
    # Review round 2 finding 1: bulleted canonical lines without colon
    # (e.g. '* INCLUSION CRITERIA' in NCT02262806, NCT05713799) must parse.
    text = "- Inclusion Criteria\n- a\n- Exclusion Criteria\n- b\n"
    parsed = split_eligibility_sections(text)

    assert parsed is not None
    assert parsed.parsed is True
    assert parsed.inclusion == ["a"]
    assert parsed.exclusion == ["b"]
    assert parsed.unclassified == []


def test_end_of_line_exclusion_heading_splits_sections() -> None:
    # Review round 2 finding 2: exclusion heading at end of content line
    # must open an exclusion section rather than merging into inclusion.
    text = (
        "Inclusion Criteria:\n"
        "* consent given. Exclusion Criteria:\n"
        "* advanced cancer\n"
        "* pregnancy\n"
    )
    parsed = split_eligibility_sections(text)

    assert parsed is not None
    assert parsed.parsed is True
    assert parsed.inclusion == ["consent given."]
    assert parsed.exclusion == ["advanced cancer", "pregnancy"]
    assert [s.heading_raw for s in parsed.sections] == [
        "Inclusion Criteria:",
        "Exclusion Criteria:",
    ]


def test_end_of_line_bold_exclusion_heading_splits_sections() -> None:
    # Review round 2 finding 2 bold variant.
    text = (
        "Inclusion Criteria:\n"
        "* consent given. **Exclusion Criteria:**\n"
        "* advanced cancer\n"
        "* pregnancy\n"
    )
    parsed = split_eligibility_sections(text)

    assert parsed is not None
    assert parsed.parsed is True
    assert parsed.inclusion == ["consent given."]
    assert parsed.exclusion == ["advanced cancer", "pregnancy"]
    assert [s.heading_raw for s in parsed.sections] == [
        "Inclusion Criteria:",
        "**Exclusion Criteria:**",
    ]


def test_midline_prose_with_exclusion_word_is_not_a_heading() -> None:
    # Review round 2 finding 3 negative probe 1: prose mentioning 'exclusion:'
    # without sentence punctuation / title-casing must not split.
    text = "Inclusion Criteria:\n* No other exclusion: patients may enroll after washout\n* B\n"
    parsed = split_eligibility_sections(text)

    assert parsed is not None
    assert parsed.parsed is True
    assert parsed.inclusion == [
        "No other exclusion: patients may enroll after washout",
        "B",
    ]
    assert parsed.exclusion == []


def test_bulleted_prose_lead_in_is_not_a_heading() -> None:
    # Review round 2 finding 3 negative probe 2: descriptive prose line
    # starting with bullet is not a heading.
    text = "* Patients meeting all of the following inclusion criteria: age 18+\n"
    parsed = split_eligibility_sections(text)

    assert parsed is not None
    assert parsed.parsed is False
    assert parsed.inclusion == []
    assert parsed.exclusion == []
    assert parsed.unclassified == [
        "Patients meeting all of the following inclusion criteria: age 18+"
    ]


def test_bulleted_no_exclusion_criteria_is_an_item_not_heading() -> None:
    # Round 3 finding 1: '* No exclusion criteria' is an item, never an exclusion heading.
    text = "Inclusion Criteria:\n* Age 18+\n* No exclusion criteria\n* Signed consent\n"
    parsed = split_eligibility_sections(text)

    assert parsed is not None
    assert parsed.parsed is True
    assert parsed.inclusion == ["Age 18+", "No exclusion criteria", "Signed consent"]
    assert parsed.exclusion == []


def test_bulleted_lead_in_words_are_items_not_headings() -> None:
    # Round 3 finding 1: criteria starting with negative/lead-in words stay items.
    text = (
        "Inclusion Criteria:\n"
        "* Meets inclusion criteria for DBS implant in OCD\n"
        "* Prior exclusion from study: not allowed\n"
        "* Note: exclusion may apply\n"
        "* Exclusion\n"
    )
    parsed = split_eligibility_sections(text)

    assert parsed is not None
    assert parsed.parsed is True
    assert parsed.inclusion == [
        "Meets inclusion criteria for DBS implant in OCD",
        "Prior exclusion from study: not allowed",
        "Note: exclusion may apply",
        "Exclusion",
    ]
    assert parsed.exclusion == []


def test_unbulleted_no_exclusion_criteria_is_not_a_heading() -> None:
    # Round 3 finding 1: unbulleted 'No exclusion criteria' stays an item.
    text = "No exclusion criteria\n"
    parsed = split_eligibility_sections(text)

    assert parsed is not None
    assert parsed.parsed is False
    assert parsed.unclassified == ["No exclusion criteria"]


def test_non_inclusion_criteria_heading_falls_back_to_unclassified() -> None:
    # Round 3 finding 2: 'Non-inclusion Criteria:' must never be inclusion;
    # falls back to unclassified.
    text = (
        "Inclusion Criteria:\n"
        "* Age 18+\n"
        "Non-inclusion Criteria:\n"
        "* Cancer history\n"
        "* Alcohol abuse\n"
        "Exclusion Criteria:\n"
        "* Stroke\n"
    )
    parsed = split_eligibility_sections(text)

    assert parsed is not None
    assert parsed.parsed is True
    assert parsed.inclusion == ["Age 18+"]
    assert parsed.unclassified == ["Cancer history", "Alcohol abuse"]
    assert parsed.exclusion == ["Stroke"]
    assert "Cancer history" not in parsed.inclusion


def test_non_inclusion_with_space_falls_back_to_unclassified() -> None:
    # Round 3 finding 2: 'Non inclusion criteria' must never open an inclusion section.
    text = "Inclusion Criteria:\n* Age 18+\nNon inclusion criteria\n* Cancer history\n"
    parsed = split_eligibility_sections(text)

    assert parsed is not None
    assert parsed.parsed is True
    assert parsed.inclusion == ["Age 18+"]
    assert parsed.unclassified == ["Cancer history"]
    assert "Cancer history" not in parsed.inclusion


def test_midline_split_with_ordinary_casing_and_punctuation() -> None:
    # Round 3 finding 3: 'Exclusion criteria:' with lowercase 'criteria' splits mid-line.
    text = (
        "Inclusion Criteria:\n"
        "* consent given. Exclusion criteria:\n"
        "* advanced cancer\n"
        "* pregnancy\n"
    )
    parsed = split_eligibility_sections(text)

    assert parsed is not None
    assert parsed.parsed is True
    assert parsed.inclusion == ["consent given."]
    assert parsed.exclusion == ["advanced cancer", "pregnancy"]


def test_multiple_headings_on_single_line_split_in_order() -> None:
    # Round 3 finding 3: multiple headings on one line processed from left to right.
    text = "* a. Exclusion Criteria: b. Inclusion Criteria: c\n* d\n"
    parsed = split_eligibility_sections(text)

    assert parsed is not None
    assert parsed.parsed is True
    assert parsed.unclassified == ["a."]
    assert parsed.exclusion == ["b."]
    assert parsed.inclusion == ["c", "d"]


def test_inline_heading_followed_by_midline_heading() -> None:
    # Round 3 finding 3: leading inline heading + subsequent mid-line heading.
    text = "Inclusion Criteria: age 18+. Exclusion Criteria: pregnancy\n"
    parsed = split_eligibility_sections(text)

    assert parsed is not None
    assert parsed.parsed is True
    assert parsed.inclusion == ["age 18+."]
    assert parsed.exclusion == ["pregnancy"]
    assert parsed.unclassified == []


def test_combined_inclusion_and_exclusion_criteria_is_unclassified() -> None:
    # Round 3 finding 4: combined heading maps to unclassified.
    text = (
        "Inclusion and Exclusion Criteria:\n"
        "* minimal access spinal surgery\n"
        "* older than 18 years\n"
    )
    parsed = split_eligibility_sections(text)

    assert parsed is not None
    assert parsed.parsed is False
    assert parsed.inclusion == []
    assert parsed.exclusion == []
    assert parsed.unclassified == [
        "minimal access spinal surgery",
        "older than 18 years",
    ]
    assert [s.kind for s in parsed.sections] == ["unclassified"]


def test_combined_slash_criteria_is_unclassified() -> None:
    # Round 3 finding 4: 'INCLUSION/EXCLUSION CRITERIA - SAMPLE 3:' maps to unclassified.
    text = "INCLUSION/EXCLUSION CRITERIA - SAMPLE 3:\n* Sample item\n"
    parsed = split_eligibility_sections(text)

    assert parsed is not None
    assert parsed.parsed is False
    assert parsed.inclusion == []
    assert parsed.exclusion == []
    assert parsed.unclassified == ["Sample item"]


def test_midline_bare_keywords_without_criteria_word_do_not_split() -> None:
    # Round 3 finding 5: bare 'Inclusion:' or 'Exclusion:' mid-line without the word
    # 'criteria' must not split.
    text = "Exclusion Criteria:\n* Prior therapy; Inclusion: allowed if washed out\n* B\n"
    parsed = split_eligibility_sections(text)

    assert parsed is not None
    assert parsed.parsed is True
    assert parsed.inclusion == []
    assert parsed.exclusion == [
        "Prior therapy; Inclusion: allowed if washed out",
        "B",
    ]


def test_cosmetic_bold_inline_heading_has_no_orphan_markers() -> None:
    # Round 3 finding 10: '**Exclusion Criteria:** text' has clean item without dangling '**'.
    text = "**Exclusion Criteria:** text\n"
    parsed = split_eligibility_sections(text)

    assert parsed is not None
    assert parsed.parsed is True
    assert parsed.exclusion == ["text"]
    assert parsed.sections[0].heading_raw == "**Exclusion Criteria:**"


def test_numbered_and_dash_headings_recognized() -> None:
    # Round 3 finding 10: numbered and en-dash headings are recognized.
    text = "1. Inclusion Criteria :\n* Item 1\n2. Exclusion Criteria:\n* Item 2\n"
    parsed = split_eligibility_sections(text)

    assert parsed is not None
    assert parsed.parsed is True
    assert parsed.inclusion == ["Item 1"]
    assert parsed.exclusion == ["Item 2"]
    assert [s.heading_raw for s in parsed.sections] == [
        "Inclusion Criteria :",
        "Exclusion Criteria:",
    ]

    text2 = "– Exclusion Criteria\n* Item 1\n"
    parsed2 = split_eligibility_sections(text2)
    assert parsed2 is not None
    assert parsed2.parsed is True
    assert parsed2.exclusion == ["Item 1"]
    assert parsed2.sections[0].heading_raw == "Exclusion Criteria"


@pytest.mark.parametrize(
    "heading",
    [
        "Non- inclusion Criteria:",
        "Non-  Inclusion Criteria",
        "NON -inclusion criteria:",
        "Inclusion Criteria for Nurses (no exclusion criteria):",
        "Inclusion Criteria and Exclusion Criteria:",
        "Inclusion-Exclusion Criteria:",
        "Inclusion, Exclusion Criteria:",
        "Inclusion and Key Exclusion Criteria:",
        "Post- inclusion criteria:",
        "Pre inclusion and exclusion criteria:",
        "Post- inclusion and exclusion criteria:",
        "Pre- inclusion/exclusion criteria:",
    ],
)
def test_heading_that_cannot_name_one_list_is_unclassified(heading: str) -> None:
    parsed = split_eligibility_sections(f"{heading}\n* first item\n* second item")

    assert parsed is not None
    assert parsed.parsed is False
    assert [(s.kind, s.heading_raw) for s in parsed.sections] == [("unclassified", heading)]
    assert parsed.unclassified == ["first item", "second item"]


def test_temporal_compound_is_not_a_mention_of_the_other_list() -> None:
    parsed = split_eligibility_sections("Exclusion Criteria (post-inclusion):\n* first item")

    assert parsed is not None
    assert parsed.exclusion == ["first item"]


@pytest.mark.parametrize(
    "line",
    [
        "Medical staff: No exclusion criteria",
        "Medical staff: Any exclusion criteria",
        "Any exclusion criteria",
        "* No exclusion criteria",
        "* Does not meet any exclusion criteria",
        "* Absence of exclusion criteria",
        "* No other exclusion criteria",
        "* Meeting exclusion criteria:",
        "* Diabetes (see exclusion criteria:",
        "* Mentions exclusion criteria elsewhere in prose",
    ],
)
def test_statement_about_criteria_stays_in_the_open_inclusion_section(line: str) -> None:
    text = f"Inclusion Criteria:\n{line}\n* later item"
    parsed = split_eligibility_sections(text)

    assert parsed is not None
    assert [s.kind for s in parsed.sections] == ["inclusion"]
    assert parsed.inclusion[-1] == "later item"
    assert reconstruct(parsed).split() == _tokens_after_marker_strip(text)


@pytest.mark.parametrize(
    ("line", "kept_in_inclusion", "heading", "first_unclassified"),
    [
        ("* 6 months Exclusion Criteria", [], "6 months Exclusion Criteria", "next"),
        ("* 2 mg per day Exclusion Criteria", [], "2 mg per day Exclusion Criteria", "next"),
        (
            "* No missed doses in this period Exclusion Criteria",
            [],
            "No missed doses in this period Exclusion Criteria",
            "next",
        ),
        (
            "General Exclusion Criteria arbitrary trailing prose",
            [],
            "General Exclusion Criteria",
            "arbitrary trailing prose",
        ),
        ("* Has consent. Exclusion Criteria", ["Has consent."], "Exclusion Criteria", "next"),
        ("1. Has consent. Exclusion Criteria", ["Has consent."], "Exclusion Criteria", "next"),
        (
            "* Insured (social) Non inclusion criteria",
            ["Insured (social)"],
            "Non inclusion criteria",
            "next",
        ),
        (
            "Inclusion Criteria: age 18. Exclusion criteria",
            ["age 18."],
            "Exclusion criteria",
            "next",
        ),
        ("General Study Exclusion Criteria", [], "General Study Exclusion Criteria", "next"),
        ("* Study Exclusion Criteria：", [], "Study Exclusion Criteria：", "next"),
        ("* **Study Exclusion Criteria**", [], "**Study Exclusion Criteria**", "next"),
        ("6 months Exclusion Criteria: pregnant", [], "6 months Exclusion Criteria:", "pregnant"),
        ("General Exclusion Criteria 2. History", [], "General Exclusion Criteria", "2. History"),
        ("Criteria for Exclusion", [], "Criteria for Exclusion", "next"),
        ("Criteria for non inclusion：", [], "Criteria for non inclusion：", "next"),
        (
            "Criteria for exclusion were as follows:",
            [],
            "Criteria for exclusion were as follows:",
            "next",
        ),
        ("* Exclusion Criteria of Child:", [], "Exclusion Criteria of Child:", "next"),
    ],
)
def test_unrecognized_exclusion_heading_closes_the_inclusion_section(
    line: str, kept_in_inclusion: list[str], heading: str, first_unclassified: str
) -> None:
    text = f"Inclusion Criteria:\n{line}\n* next"
    parsed = split_eligibility_sections(text)

    assert parsed is not None
    assert parsed.parsed is True
    assert parsed.inclusion == kept_in_inclusion
    assert parsed.exclusion == []
    assert parsed.sections[-1].kind == "unclassified"
    assert parsed.sections[-1].heading_raw == heading
    assert parsed.unclassified[0] == first_unclassified
    assert parsed.unclassified[-1] == "next"
    assert reconstruct(parsed).split() == _tokens_after_marker_strip(text)


def test_unrecognized_heading_does_not_reopen_an_exclusion_section() -> None:
    parsed = split_eligibility_sections("Exclusion Criteria:\n* a Non inclusion criteria\n* b")

    assert parsed is not None
    assert parsed.exclusion == ["a Non inclusion criteria", "b"]


def test_closing_heading_in_text_before_a_midline_heading() -> None:
    """Keep exclusion text out of inclusion even before a later recognized heading."""
    text = "Inclusion Criteria:\n6 months Exclusion Criteria: pregnant. Inclusion Criteria: child"
    parsed = split_eligibility_sections(text)

    assert parsed is not None
    assert parsed.inclusion == ["child"]
    assert parsed.unclassified == ["pregnant."]
    assert reconstruct(parsed).split() == _tokens_after_marker_strip(text)


def test_safe_close_can_move_an_inclusion_statement_to_unclassified() -> None:
    """Pin the disclosed over-eager close without assigning the opposite list."""
    text = (
        "Inclusion Criteria:\nPatients eligible according to inclusion and exclusion criteria\n"
        "* later inclusion item"
    )
    parsed = split_eligibility_sections(text)

    assert parsed is not None
    assert parsed.inclusion == []
    assert parsed.exclusion == []
    assert parsed.unclassified == ["later inclusion item"]
    assert reconstruct(parsed).split() == _tokens_after_marker_strip(text)


@pytest.mark.parametrize(
    "text",
    [
        "Exclusion Criteria：\n* pregnant",
        "* Exclusion Criteria：\n* pregnant",
        "Exclusion Criteria： pregnant",
        "Inclusion Criteria:\n* adult. Exclusion Criteria： pregnant",
    ],
)
def test_fullwidth_colon_is_an_exact_heading_colon(text: str) -> None:
    parsed = split_eligibility_sections(text)

    assert parsed is not None
    assert parsed.exclusion == ["pregnant"]


def test_repeated_bold_markers_after_inline_heading_are_not_item_text() -> None:
    parsed = split_eligibility_sections("Exclusion Criteria: ** **pregnant")

    assert parsed is not None
    assert parsed.exclusion == ["pregnant"]
