"""Corpus tests for eligibility section splitting over real registry blobs.

The corpus in ``tests/fixtures/criteria_corpus/`` is verbatim
ClinicalTrials.gov API v2 ``eligibilityCriteria`` text with per-sample
provenance (NCT ID, source URL, retrieval date). It is real data: never
fabricate, edit, or pad it. See the ``provenance`` block in the fixture.
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
        f"(floor {PARSE_RATE_FLOOR:.0%}); unparsed NCT IDs: {misses}"
    )
    assert rate >= PARSE_RATE_FLOOR, f"Parse rate {rate:.1%} below floor {PARSE_RATE_FLOOR:.0%}"
