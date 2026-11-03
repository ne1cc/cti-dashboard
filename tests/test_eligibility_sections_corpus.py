"""Corpus tests for eligibility section splitting over real registry blobs.

The corpus in ``tests/fixtures/criteria_corpus/`` is verbatim
ClinicalTrials.gov API v2 ``eligibilityCriteria`` text. Each sample records
its NCT ID, source study URL, and criteria text; the API endpoint, field
path, and retrieval date are recorded once at corpus level in the fixture's
``provenance`` block. This is real data: never fabricate, edit, or pad it.
"""

import json
import re
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

FROZEN_PATH = CORPUS_PATH.with_name("frozen_regression_records.json")

# Shape guard for a few exclusion-style headings left inside inclusion items. Hits need
# human reading: this misses other shapes and can flag statements such as 'Absence of...'.
_HEADING_SHAPE_SCAN_RE = re.compile(
    r"(?:exclusions?|non[-\s]*inclusions?)\s+criteri(?:a|on)"
    r"(?:\s*[:：]?\**\s*$|\s+\d{1,3}\\?[.)]\s|\s+(?:of|for)\b.*[:：]\s*$)"
    r"|^\W*criteria\s+for\s+(?:the\s+)?(?:exclusions?|non[-\s]*inclusions?)\b",
    re.IGNORECASE,
)
_HEADING_SHAPE_SCAN_GUARD_RE = re.compile(r"\b(?:no|not|any|without)\b", re.IGNORECASE)


def _heading_shape_hits(samples: list[dict[str, Any]]) -> list[str]:
    """Return candidate IDs for a narrow shape guard, not measured wrong-list errors."""
    hits = []
    for sample in samples:
        parsed = split_eligibility_sections(sample["criteria_text"])
        assert parsed is not None
        lines = [line for item in parsed.inclusion for line in item.splitlines()]
        if any(
            _HEADING_SHAPE_SCAN_RE.search(line) and not _HEADING_SHAPE_SCAN_GUARD_RE.search(line)
            for line in lines
        ):
            hits.append(sample["nct_id"])
    return hits


# Regression floor: the measured rate on 2026-10-28 was 99.0% (198/200).
# The floor leaves headroom without letting a splitter regression slip by.
PARSE_RATE_FLOOR = 0.90


@pytest.fixture(scope="module")
def corpus() -> list[dict[str, Any]]:
    doc = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    samples: list[dict[str, Any]] = doc["samples"]
    return samples


@pytest.fixture(scope="module")
def frozen_records() -> dict[str, dict[str, Any]]:
    doc = json.loads(FROZEN_PATH.read_text(encoding="utf-8"))
    return {sample["nct_id"]: sample for sample in doc["samples"]}


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


def test_corpus_parse_rate_meets_floor_and_report(
    corpus: list[dict[str, Any]], frozen_records: dict[str, dict[str, Any]]
) -> None:
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
    corpus_hits = _heading_shape_hits(corpus)
    frozen_hits = _heading_shape_hits(list(frozen_records.values()))
    print(
        f"\nEligibility section parse rate: {parsed_count}/{len(corpus)} = {rate:.1%} "
        f"(floor {PARSE_RATE_FLOOR:.0%}; measures heading recognition, not list assignment); "
        f"unparsed NCT IDs: {misses}; "
        f"heading-shape guard hits (not semantic errors): corpus {len(corpus_hits)}/{len(corpus)}, "
        f"frozen set {len(frozen_hits)}/{len(frozen_records)}"
    )
    assert rate >= PARSE_RATE_FLOOR, f"Parse rate {rate:.1%} below floor {PARSE_RATE_FLOOR:.0%}"
    assert corpus_hits == []
    assert frozen_hits == []


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


def test_frozen_records_are_real_and_outside_the_corpus(
    corpus: list[dict[str, Any]], frozen_records: dict[str, dict[str, Any]]
) -> None:
    doc = json.loads(FROZEN_PATH.read_text(encoding="utf-8"))
    assert doc["provenance"]["api_endpoint"].startswith("https://clinicaltrials.gov/api/v2/")
    assert len(frozen_records) == 25
    assert not set(frozen_records) & {sample["nct_id"] for sample in corpus}


@pytest.mark.parametrize(
    ("nct_id", "expected_sections", "not_in_inclusion"),
    [
        # Round 4 finding 1: unrecognized exclusion or non-inclusion heading in open inclusion.
        (
            "NCT07444697",
            [
                ("inclusion", "Inclusion Criteria", 11),
                ("unclassified", "6 months Exclusion Criteria", 21),
            ],
            "Known malignancy",
        ),
        (
            "NCT07752485",
            [("inclusion", "Inclusion Criteria", 8), ("unclassified", "Exclusion Criteria", 8)],
            None,
        ),
        (
            "NCT01606826",
            [
                ("unclassified", None, 1),
                ("inclusion", "Inclusion Criteria:", 5),
                ("exclusion", "Exclusion Criteria:", 10),
                ("inclusion", "Inclusion criteria:", 1),
                ("unclassified", "Exclusion criteria", 7),
            ],
            "Pregnancy,",
        ),
        (
            "NCT06780761",
            [
                ("inclusion", "Inclusion Criteria:", 2),
                ("unclassified", "Criteria for exclusion from the study were as follows:", 2),
            ],
            None,
        ),
        (
            "NCT05504564",
            [
                ("inclusion", "Inclusion Criteria:", 2),
                ("exclusion", "Exclusion Criteria:", 6),
                ("inclusion", "Inclusion Criteria of Child with language disorder:", 3),
                ("unclassified", "Exclusion Criteria of Child with language disorder:", 1),
                ("inclusion", "Inclusion Criteria of Adult:", 1),
            ],
            None,
        ),
        (
            "NCT02603185",
            [("inclusion", "Inclusion Criteria:", 15), ("exclusion", "Exclusion Criteria：", 20)],
            None,
        ),
        (
            "NCT05785754",
            [
                ("inclusion", "Selected Inclusion Criteria:", 40),
                ("unclassified", "Criteria for Exclusion:", 25),
            ],
            None,
        ),
        (
            "NCT00099255",
            [
                ("inclusion", "Inclusion Criteria", 12),
                ("unclassified", "Criteria for Exclusion", 11),
            ],
            None,
        ),
        (
            "NCT07899606",
            [
                ("unclassified", None, 1),
                ("inclusion", "Inclusion Criteria", 8),
                ("unclassified", "Exclusion Criteria", 12),
            ],
            None,
        ),
        (
            "NCT03359850",
            [
                ("inclusion", "Inclusion Criteria:", 30),
                ("unclassified", "Criteria for Exclusion:", 24),
            ],
            None,
        ),
        (
            "NCT05000216",
            [
                ("unclassified", None, 5),
                ("inclusion", "Inclusion Criteria:", 9),
                ("unclassified", "Adult General Exclusion Criterion", 16),
            ],
            "Inability or unwillingness of a participant",
        ),
        (
            "NCT01202721",
            [
                ("inclusion", "Inclusion Criteria:", 3),
                ("unclassified", "General Study Exclusion Criteria", 13),
                ("exclusion", "Additional Exclusion Criteria for BB arm only", 6),
                ("exclusion", "Additional Exclusion Criteria for ARB arm only", 7),
            ],
            None,
        ),
        (
            "NCT01022515",
            [
                ("unclassified", None, 1),
                ("inclusion", "Inclusion criteria :", 6),
                ("unclassified", "Non inclusion criteria", 12),
            ],
            None,
        ),
        (
            "NCT02557464",
            [
                ("inclusion", "Inclusion Criteria:", 1),
                ("inclusion", "Inclusion criteria", 8),
                ("unclassified", "Non-inclusion criteria", 4),
                ("inclusion", "Inclusion criteria", 7),
                ("unclassified", "Non-inclusion criteria", 7),
            ],
            None,
        ),
        # Round 4 finding 2: heading text classified as the wrong kind.
        (
            "NCT02816151",
            [
                ("inclusion", "inclusion criteria", 8),
                ("unclassified", "non- inclusion criteria", 9),
            ],
            "Toxin Botulinic",
        ),
        (
            "NCT05420623",
            [
                ("inclusion", "Inclusion Criteria (Amputees patients):", 5),
                ("unclassified", "Non-inclusion Criteria (Amputees patients):", 6),
                ("inclusion", "Inclusion Criteria (Healthy volunteers):", 1),
                ("unclassified", "Non- Inclusion Criteria (Healthy volunteers):", 6),
            ],
            None,
        ),
        (
            "NCT03913390",
            [
                ("inclusion", "Inclusion criteria for the quantitative sample:", 3),
                ("unclassified", "Non- inclusion criteria:", 5),
            ],
            None,
        ),
        (
            "NCT06348719",
            [
                ("unclassified", None, 1),
                ("inclusion", "Inclusion Criteria:", 5),
                ("unclassified", "Non Inclusion Criteria:", 9),
                ("exclusion", "Exclusion criteria :", 2),
                ("inclusion", "Inclusion Criteria:", 11),
                ("unclassified", "Non Inclusion Criteria:", 4),
                ("exclusion", "Exclusion criteria :", 2),
                ("inclusion", "Inclusion Criteria:", 4),
                ("unclassified", "Non Inclusion Criteria:", 2),
                ("inclusion", "Inclusion Criteria:", 2),
                ("unclassified", "Non- inclusion Criteria:", 1),
                ("inclusion", "Inclusion Criteria:", 2),
                ("unclassified", "Non- inclusion Criteria:", 1),
            ],
            None,
        ),
        (
            "NCT02758938",
            [
                ("inclusion", "Inclusion Criteria for Peer Focus Group:", 1),
                ("inclusion", "Inclusion Criteria for Patients:", 3),
                ("exclusion", "Exclusion Criteria for Patients:", 2),
                ("unclassified", "Inclusion Criteria for Nurses (no exclusion criteria):", 1),
                ("unclassified", "Inclusion Criteria for Physician (no exclusion criteria):", 1),
            ],
            None,
        ),
        (
            "NCT03387189",
            [("inclusion", "Inclusion Criteria:", 1), ("exclusion", "Exclusion Criteria:", 1)],
            None,
        ),
    ],
)
def test_frozen_record_keeps_exclusion_text_out_of_inclusion(
    frozen_records: dict[str, dict[str, Any]],
    nct_id: str,
    expected_sections: list[tuple[str, str | None, int]],
    not_in_inclusion: str | None,
) -> None:
    text = frozen_records[nct_id]["criteria_text"]
    parsed = split_eligibility_sections(text)

    assert parsed is not None
    assert parsed.parsed is True
    assert [(s.kind, s.heading_raw, len(s.items)) for s in parsed.sections] == expected_sections
    assert reconstruct(parsed).split() == strip_bullet_markers(text).split()
    if not_in_inclusion is not None:
        assert not_in_inclusion not in " ".join(parsed.inclusion)
        assert not_in_inclusion in " ".join(parsed.unclassified)


@pytest.mark.parametrize(
    ("nct_id", "exclusion_text", "unclassified_count"),
    [
        ("NCT01911104", "Resting blood pressure", 40),
        ("NCT02093390", "The subject has a history of drug abuse", 11),
        ("NCT06235385", "History of prior valve surgery", 4),
        ("NCT03190577", "Patients under legal supervision", 1),
        ("NCT00111098", "Prior recipient or scheduled to receive a kidney transplant", 1),
    ],
)
def test_followup_frozen_records_close_inclusion_before_exclusion_prose(
    frozen_records: dict[str, dict[str, Any]],
    nct_id: str,
    exclusion_text: str,
    unclassified_count: int,
) -> None:
    """Pin all five reviewed leaks with verbatim records, without runtime fetching."""
    text = frozen_records[nct_id]["criteria_text"]
    parsed = split_eligibility_sections(text)

    assert parsed is not None and parsed.parsed
    assert exclusion_text not in " ".join(parsed.inclusion)
    assert exclusion_text in " ".join(parsed.unclassified)
    assert len(parsed.sections[-1].items) == unclassified_count
    # Inline headings can expose a leading '-' item marker, also stripped by design.
    assert [t for t in reconstruct(parsed).split() if t != "-"] == [
        t for t in strip_bullet_markers(text).split() if t != "-"
    ]
