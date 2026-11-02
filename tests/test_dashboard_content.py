"""Content guardrails and dynamic formatting tests for generic dashboard chrome.

Enforces that generic UI modules (components and pages) remain strictly neutral:
free from hard-coded disease literals (ADRD / Alzheimer's / dementia), prescriptive
playbook advice, uncited clinical claims, and speculative ROI calculator strings.
Also verifies that condition group labels are dynamically resolved from the active
profile's taxonomy.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from dashboard.components.guidance import format_condition_group
from src.transform.normalize_conditions import ConditionTaxonomy, TaxonomyGroup

ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_DIR = ROOT / "dashboard"

# Banned strings in generic dashboard chrome (case-insensitive substring check)
PROHIBITED_GENERIC_STRINGS = [
    # Disease-specific hard-coding in generic chrome
    "adrd",
    "alzheimer",
    "dementia",
    # Prescriptive playbook phrases
    "playbook",
    "step 1:",
    "step 2:",
    "step 3:",
    "do not automatically eliminate",
    # Uncited clinical claims
    "screen failure",
    "study partner burden",
    "pet scan",
    "adas-cog",
    # Speculative ROI calculator phrases
    "roi" + "_scenarios",
    "roi" + "_assumptions",
    "scenario explorer",
    "burn rate",
    "cost per feasibility review",
]


def _collect_dashboard_python_files() -> list[Path]:
    """Collect all Python source files in the dashboard directory."""
    files = []
    for path in DASHBOARD_DIR.rglob("*.py"):
        # Exclude temporary or cache files
        if "__pycache__" not in path.parts:
            files.append(path)
    return files


def test_generic_dashboard_chrome_is_neutral():
    """Assert generic UI code contains no hard-coded disease terms, playbooks, or ROI copy."""
    dashboard_files = _collect_dashboard_python_files()
    assert dashboard_files, "Found no dashboard Python files to scan"

    violations: list[str] = []

    for file_path in dashboard_files:
        rel_path = file_path.relative_to(ROOT)
        content = file_path.read_text(encoding="utf-8")
        tree = ast.parse(content, filename=str(file_path))

        # Check all string literals (docstrings, constant strings, messages)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                string_val = node.value.lower()
                for prohibited in PROHIBITED_GENERIC_STRINGS:
                    if prohibited in string_val:
                        violations.append(
                            f"{rel_path}:{getattr(node, 'lineno', '?')} found prohibited "
                            f"literal '{prohibited}': {node.value[:60]!r}"
                        )

    assert not violations, (
        f"Found {len(violations)} prohibited content violation(s) in generic dashboard chrome:\n"
        + "\n".join(violations)
    )


def test_dynamic_condition_group_formatting_adrd():
    """Verify ADRD condition group formatting dynamically resolves labels from taxonomy."""
    assert format_condition_group("alzheimers_disease", "adrd") == "Alzheimer's Disease"
    assert (
        format_condition_group("mild_cognitive_impairment", "adrd")
        == "Mild Cognitive Impairment (MCI)"
    )
    assert (
        format_condition_group("frontotemporal_dementia", "adrd") == "Frontotemporal Dementia (FTD)"
    )
    assert format_condition_group("lewy_body_dementia", "adrd") == "Lewy Body Dementia (LBD)"
    assert format_condition_group("vascular_dementia", "adrd") == "Vascular Dementia (VaD)"
    assert (
        format_condition_group("parkinsons_disease_dementia", "adrd")
        == "Parkinson's Disease Dementia (PDD)"
    )
    assert format_condition_group("dementia_unspecified", "adrd") == "Dementia (Unspecified)"
    assert (
        format_condition_group("cognitive_impairment_other", "adrd")
        == "Cognitive Symptoms (Non-Dementia)"
    )
    assert format_condition_group("non_dementia_other", "adrd") == "Other Conditions"


def test_dynamic_condition_group_formatting_oncology_nsclc():
    """Verify NSCLC condition group formatting dynamically resolves labels from taxonomy."""
    assert (
        format_condition_group("nsclc_adenocarcinoma", "oncology_nsclc") == "NSCLC Adenocarcinoma"
    )
    assert (
        format_condition_group("nsclc_squamous", "oncology_nsclc")
        == "NSCLC Squamous Cell Carcinoma"
    )
    assert (
        format_condition_group("nsclc_large_cell", "oncology_nsclc") == "NSCLC Large Cell Carcinoma"
    )
    assert (
        format_condition_group("nsclc_biomarker_targeted", "oncology_nsclc")
        == "Biomarker-Targeted NSCLC"
    )
    assert (
        format_condition_group("nsclc_metastatic_advanced", "oncology_nsclc")
        == "Metastatic / Advanced NSCLC"
    )
    assert format_condition_group("nsclc_unspecified", "oncology_nsclc") == "NSCLC (Unspecified)"
    assert (
        format_condition_group("other_thoracic_oncology", "oncology_nsclc")
        == "Other Thoracic Oncology"
    )


def test_dynamic_condition_group_formatting_heart_failure():
    """Verify Heart Failure condition group formatting dynamically resolves acronyms."""
    assert format_condition_group("heart_failure_reduced_ef", "heart_failure") == (
        "Heart Failure with Reduced Ejection Fraction (HFrEF)"
    )
    assert format_condition_group("heart_failure_preserved_ef", "heart_failure") == (
        "Heart Failure with Preserved Ejection Fraction (HFpEF)"
    )
    assert format_condition_group("other_heart_conditions", "heart_failure") == (
        "Other Heart Conditions"
    )


def test_dynamic_condition_group_formatting_fallback_and_mutation(monkeypatch: pytest.MonkeyPatch):
    """Verify fallback behavior and dynamic resolution when taxonomy label is mutated."""
    from src.config import (
        ApiConfig,
        HttpConfig,
        IngestionConfig,
        PathsConfig,
        ProjectConfig,
        RetentionConfig,
    )
    from src.profiles import IndicationProfile, SharedPaths

    # 1. Unknown condition group falls back cleanly to title case
    assert format_condition_group("unknown_rare_disease", "adrd") == "Unknown Rare Disease"
    assert format_condition_group("unknown_rare_disease", None) == "Unknown Rare Disease"

    # 2. Non-string inputs handle gracefully
    assert format_condition_group(None) == ""
    assert format_condition_group(123) == "123"

    # 3. Dynamic mutation: mutating a taxonomy group label updates rendered output
    # without touching any dashboard code
    mock_tax = ConditionTaxonomy(
        groups=(
            TaxonomyGroup(
                condition_group="synthetic_group",
                dementia_relevance=False,
                exact=(),
                substrings=(),
                display_name="Initial Label",
            ),
        ),
        default_group="default_synthetic",
        default_dementia_relevance=False,
        default_display_name="Default Synthetic Label",
    )

    mock_profile = IndicationProfile(
        profile_id="synthetic_profile",
        display_name="Synthetic Profile",
        ingest_only=False,
        config=ProjectConfig(
            api=ApiConfig(base_url="http://example.com", http=HttpConfig()),
            paths=PathsConfig(
                bronze_api_responses=Path("/tmp/bronze"),
                bronze_manifests=Path("/tmp/manifests"),
                quarantine=Path("/tmp/quarantine"),
                silver=Path("/tmp/silver"),
                gold=Path("/tmp/gold"),
                duckdb=Path("/tmp/duckdb.duckdb"),
            ),
            ingestion=IngestionConfig(),
            retention=RetentionConfig(),
        ),
        taxonomy=mock_tax,
        shared=SharedPaths(silver=Path("/tmp"), gold=Path("/tmp"), duckdb=Path("/tmp")),
    )

    class MockRegistry:
        def get(self, profile_id: str):
            if profile_id == "synthetic_profile":
                return mock_profile
            from src.profiles import get_registry

            return get_registry().get(profile_id)

    monkeypatch.setattr("src.profiles.get_registry", lambda: MockRegistry())

    assert format_condition_group("synthetic_group", "synthetic_profile") == "Initial Label"

    # Mutate the taxonomy group display_name
    mutated_tax = ConditionTaxonomy(
        groups=(
            TaxonomyGroup(
                condition_group="synthetic_group",
                dementia_relevance=False,
                exact=(),
                substrings=(),
                display_name="Mutated Custom Label",
            ),
        ),
        default_group="default_synthetic",
        default_dementia_relevance=False,
        default_display_name="Default Synthetic Label",
    )
    mutated_profile = IndicationProfile(
        profile_id="synthetic_profile",
        display_name="Synthetic Profile",
        ingest_only=False,
        config=mock_profile.config,
        taxonomy=mutated_tax,
        shared=mock_profile.shared,
    )

    class MutatedRegistry:
        def get(self, profile_id: str):
            if profile_id == "synthetic_profile":
                return mutated_profile
            from src.profiles import get_registry

            return get_registry().get(profile_id)

    monkeypatch.setattr("src.profiles.get_registry", lambda: MutatedRegistry())

    # Output immediately reflects mutated label without dashboard code changes
    assert format_condition_group("synthetic_group", "synthetic_profile") == "Mutated Custom Label"
