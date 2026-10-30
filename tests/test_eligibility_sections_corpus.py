"""Corpus tests for eligibility section splitting over real registry blobs.

The corpus in ``tests/fixtures/criteria_corpus/`` is verbatim
ClinicalTrials.gov API v2 ``eligibilityCriteria`` text. Each sample records
its NCT ID, source study URL, and criteria text; the API endpoint, field
path, and retrieval date are recorded once at corpus level in the fixture's
``provenance`` block. This is real data: never fabricate, edit, or pad it.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from src.transform.eligibility_sections import (
    reconstruct,
    split_eligibility_sections,
    strip_bullet_markers,
)

CORPUS_PATH = (
    Path(__file__).resolve().parent / "fixtures" / "criteria_corpus" / "criteria_corpus.json"
)

# Regression floor: the measured rate on 2026-10-28 was 99.0% (198/200).
# The floor leaves headroom without letting a splitter regression slip by.
PARSE_RATE_FLOOR = 0.90


@pytest.fixture(scope="module")
def corpus() -> list[dict[str, Any]]:
    doc = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    samples: list[dict[str, Any]] = doc["samples"]
    return samples


def test_corpus_has_provenance_and_100_distinct_real_samples(corpus: list[dict[str, Any]]) -> None:
    doc = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    provenance = doc["provenance"]
    assert provenance["api_endpoint"].startswith("https://clinicaltrials.gov/api/v2/")
    assert provenance["field"] == "protocolSection.eligibilityModule.eligibilityCriteria"
    assert provenance["retrieved"]

    nct_ids = [sample["nct_id"] for sample in corpus]
    assert len(nct_ids) >= 100
    assert len(set(nct_ids)) == len(nct_ids), "Corpus samples must be distinct studies"
    for sample in corpus:
        assert sample["source_url"].endswith(sample["nct_id"])
        assert sample["criteria_text"].strip()


@pytest.mark.parametrize("sample_index", range(200))
def test_corpus_sample_is_lossless(corpus: list[dict[str, Any]], sample_index: int) -> None:
    sample = corpus[sample_index]
    parsed = split_eligibility_sections(sample["criteria_text"])

    assert parsed is not None, f"No sections parsed for {sample['nct_id']}"
    reconstructed = reconstruct(parsed).split()
    original = strip_bullet_markers(sample["criteria_text"]).split()
    assert reconstructed == original, f"Text dropped or invented for {sample['nct_id']}"


def test_corpus_parse_rate_meets_floor_and_report(corpus: list[dict[str, Any]]) -> None:
    parsed_count = 0
    misses: list[str] = []
    for sample in corpus:
        parsed = split_eligibility_sections(sample["criteria_text"])
        assert parsed is not None
        if parsed.parsed:
            parsed_count += 1
        else:
            misses.append(sample["nct_id"])

    rate = parsed_count / len(corpus)
    print(
        f"\nEligibility section parse rate: {parsed_count}/{len(corpus)} = {rate:.1%} "
        f"(floor {PARSE_RATE_FLOOR:.0%}; measures heading recognition, not list assignment); "
        f"unparsed NCT IDs: {misses}"
    )
    assert rate >= PARSE_RATE_FLOOR, f"Parse rate {rate:.1%} below floor {PARSE_RATE_FLOOR:.0%}"


@pytest.mark.parametrize(
    ("nct_id", "expected_sections"),
    [
        (
            "NCT02262806",
            [
                ("inclusion", "INCLUSION CRITERIA"),
                ("exclusion", "EXCLUSION CRITERIA"),
            ],
        ),
        (
            "NCT05713799",
            [
                ("inclusion", "INCLUSION CRITERIA"),
                ("exclusion", "EXCLUSION CRITERIA"),
            ],
        ),
        (
            "NCT07259369",
            [
                ("inclusion", "Inclusion Criteria:"),
                ("exclusion", "Exclusion Criteria:"),
            ],
        ),
        (
            "NCT07812337",
            [
                ("inclusion", "Inclusion Criteria:"),
                ("exclusion", "Exclusion Criteria (PCP):"),
                ("exclusion", "Participant Exclusion Criteria:"),
            ],
        ),
        (
            "NCT07779785",
            [
                ("inclusion", "Inclusion Criteria:"),
                ("inclusion", "Inclusion Criteria:"),
                ("exclusion", "Exclusion Criteria:"),
                ("exclusion", "Exclusion Criteria:"),
            ],
        ),
        (
            "NCT06902753",
            [
                ("inclusion", "Inclusion Criteria:"),
                ("unclassified", "Non-inclusion Criteria:"),
                ("exclusion", "Exclusion Criteria (post-inclusion):"),
            ],
        ),
    ],
)
def test_pinned_real_corpus_headings(
    corpus: list[dict[str, Any]],
    nct_id: str,
    expected_sections: list[tuple[str, str]],
) -> None:
    sample = next(s for s in corpus if s["nct_id"] == nct_id)
    parsed = split_eligibility_sections(sample["criteria_text"])

    assert parsed is not None
    assert parsed.parsed is True
    actual_sections = [(s.kind, s.heading_raw) for s in parsed.sections]
    assert actual_sections == expected_sections
    assert len(parsed.inclusion) > 0
    assert len(parsed.exclusion) > 0

    if nct_id == "NCT06902753":
        # Round 3 finding 2: verify non-inclusion criteria do not leak into inclusion list.
        assert len(parsed.unclassified) == 6
        assert "person with a medical history in neurology" not in " ".join(parsed.inclusion)
        assert "person with a medical history in neurology" in " ".join(parsed.unclassified)


def test_frozen_regression_suite_cited_registry_cases() -> None:
    """Regression test suite for actual registry records cited in AI review round 3.

    - NCT02565485 (Finding 1): '* No exclusion criteria' stays an item in inclusion.
    - NCT04989608 (Finding 2): 'Non inclusion criteria' falls back to unclassified, not inclusion.
    - NCT01195584 (Finding 4): 'Inclusion and Exclusion Criteria:' falls back to unclassified.
    - NCT01997346 (Finding 3): mid-line 'Exclusion criteria: N/A' recognized as exclusion section.
    """
    # Case 1: NCT02565485
    blob_2565485 = (
        "Inclusion Criteria:\n\n"
        "* Singleton pregnancy with gestational age ≥ 35 weeks\n"
        "* Admission for delivery\n"
        "* Age 18 or older\n"
        "* Desires neuraxial analgesia in labor\n"
        "* No exclusion criteria\n"
        "* Maternal pulse pressure < 45 mmHg on admission (verified by repeat blood pressure)\n"
        "* Category 1 FHT on admission/prior to epidural placement\n"
        "* Epidural placement within 6 hours of admission to Labor and Delivery\n\n"
        "Exclusion Criteria:\n\n"
        "* Multiple gestation\n"
        "* Intrauterine growth restriction\n"
        "* Hypertensive disorders\n"
        "* Gestational or pregestational diabetes mellitus\n"
        "* Substance abuse\n"
        "* Intrauterine fetal demise\n"
        "* Congenital or chromosomal fetal abnormalities\n"
        "* Category II or III FHR tracing on admission to L&D (pre-epidural)\n"
        "* Contraindication to neuraxial anesthesia\n"
        "* Maternal cardiomyopathy\n"
        "* Maternal renal insufficiency\n"
        "* Maternal hypotension prior to epidural placement"
    )
    p_2565485 = split_eligibility_sections(blob_2565485)
    assert p_2565485 is not None
    assert p_2565485.parsed is True
    assert len(p_2565485.inclusion) == 8
    assert len(p_2565485.exclusion) == 12
    assert "No exclusion criteria" in p_2565485.inclusion
    assert "Maternal pulse pressure < 45 mmHg on admission" in p_2565485.inclusion[5]

    # Case 2: NCT04989608
    blob_4989608 = (
        "Inclusion Criteria:\n\n"
        "Transient ischemic accident Group\n\n"
        "* Patient over 60 years of age with a transient ischemic attack\n"
        "* Patient having signed a free and informed consent\n\n"
        "Control group\n\n"
        "* Subject over 60 years old\n"
        "* Subject included in the Medit-AGEING research protocol\n"
        "* Subject with one or more risk factor (s) for cerebrovascular disease\n"
        "* Subject not opposing the use of their data for the Cog-Tia study\n\n"
        "Non inclusion criteria\n\n"
        "Transient ischemic accident Group\n\n"
        "* Patient with contraindications to performing a brain MRI\n"
        "* Neurological co-morbidity\n"
        "* Refusal to participate\n"
        "* Persons deprived of their liberty\n"
        "* Adult persons subject to a legal protection measure\n\n"
        "Control group\n\n"
        "- Subject refusing to the use of their data for the Cog-Tia study\n\n"
        "Exclusion Criteria:\n\n"
        "* New stroke during the study participation"
    )
    p_4989608 = split_eligibility_sections(blob_4989608)
    assert p_4989608 is not None
    assert p_4989608.parsed is True
    assert "Patient with contraindications to performing a brain MRI" not in p_4989608.inclusion
    assert "Patient with contraindications to performing a brain MRI" in p_4989608.unclassified
    assert p_4989608.exclusion == ["New stroke during the study participation"]

    # Case 3: NCT01195584
    blob_1195584 = (
        "Inclusion and Exclusion Criteria:\n\n"
        "* all patients who underwent minimal access spinal surgery\n"
        "* patient older than 18 years\n"
        "* all products have been used within intended use"
    )
    p_1195584 = split_eligibility_sections(blob_1195584)
    assert p_1195584 is not None
    assert p_1195584.parsed is False
    assert p_1195584.inclusion == []
    assert p_1195584.exclusion == []
    assert len(p_1195584.unclassified) == 3
    assert p_1195584.sections[0].kind == "unclassified"
    assert p_1195584.sections[0].heading_raw == "Inclusion and Exclusion Criteria:"

    # Case 4: NCT01997346
    blob_1997346 = (
        "The inclusion and exclusion criteria for the clinics and participants:\n\n"
        "1. Data abstraction\n\n"
        "   Inclusion criteria: N/A - all study sites included. "
        "Exclusion criteria: N/A - all study sites included. .\n\n"
        "2. Key informant interviews\n\n"
        "   Inclusion criteria:\n"
        "   * ≥18 years of age\n\n"
        "   Exclusion criteria:\n"
        "   * <18 years of age"
    )
    p_1997346 = split_eligibility_sections(blob_1997346)
    assert p_1997346 is not None
    assert p_1997346.parsed is True
    assert any(s.kind == "exclusion" and "N/A" in " ".join(s.items) for s in p_1997346.sections)
