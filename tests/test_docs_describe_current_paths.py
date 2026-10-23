"""Docs must describe the data tree that exists — or be declared history.

This repo's last two production incidents were both "the docs described shape A
while the code did shape B". The guard is cheap, and it is the only thing that
keeps the next migration from re-creating the same class of defect.

Path claims are checked against the profile-scoped tree. Dated incident
records and date-named documents are exempt from the retired-path guard;
the retired pipeline chain is checked everywhere. Exact model/test counts
are not a documentation contract.
"""

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Historical trees are exempt from retired-path checks; the pipeline-chain
# guard still reads them. The archive test pins the resolved exemption set.
NARRATIVE_FILES = frozenset(
    {
        Path("docs/DEPLOY_FLY.md"),
        Path("docs/development_log.md"),
    }
)

# Needles are matched as plain substrings of live documents, so each is written in
# the shortest form that no live path shares. The two bronze directory needles have
# no trailing slash — the pre-migration tree held those directories directly under
# `data/bronze/`, while every live path nests them under a profile id
# (`data/bronze/adrd/manifests/`), so the shorter form cannot fire on it. Checked
# against the tracked markdown on 2026-09-07: the only hits for either shorter
# needle are inside exempt archives, and `data/quarantine` now appears in no live
# document at all — every profile's `paths.quarantine` points inside its own bronze
# tree (`config/profiles/adrd.yml:81` → `data/bronze/adrd/manifests/quarantine`).
RETIRED_PATHS = [
    "data/bronze/manifests",
    "data/bronze/api_responses",
    "data/bronze/_schema_baseline.json",
    "data/bronze_full_catalog",
    "data/silver_full_catalog",
    "data/gold_full_catalog",
    "data/quarantine",
    "config/full_catalog_config.yml",
]

# `pipeline:` became `orchestrate prune-data dbt-run dbt-test quality-report` in
# Task 5. The arrow form is a live-behaviour sentence, so no file is exempt here:
# docs/DEPLOY_FLY.md's "first boot" bullet uses it to tell the reader what the
# container really runs, and PROJECT_DOCUMENTATION.md uses it inside a bash
# fence — which is why this guard reads the whole file, fences included.
RETIRED_CHAINS = ["ingest → transform → dbt-run"]

DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _tracked_markdown() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "-z", "*.md"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return [Path(p) for p in out.split("\0") if p]


def _is_narrative(path: Path) -> bool:
    return any(path == p or path.is_relative_to(p) for p in NARRATIVE_FILES)


def _carries_date_in_name(path: Path) -> bool:
    """A file whose own name is a date is dated wholesale.

    `docs/platform_updates_2026-09-04.md` states the counts that were true on
    2026-09-04; the date is in the filename every reader and every link sees, so
    every figure inside it inherits it.
    """
    return bool(DATE_RE.search(path.name))


def _is_archive(path: Path) -> bool:
    """Files that record a past state rather than the current one.

    Exempt only from retired-path checks: dated incident records and date-named
    update files retain their historical paths. The pipeline-chain guard still
    checks every document.
    """
    return _is_narrative(path) or _carries_date_in_name(path)


def _read(path: Path) -> str:
    """Decoded-with-replacement text. Several of these files are not valid UTF-8.

    `docs/DEPLOY_STREAMLIT.md` carries 0x97 at byte 1104 (measured 2026-09-05),
    the same file `ruff format` complains about, so
    `read_text(encoding="utf-8")` raises `UnicodeDecodeError` and the guard
    errors instead of reporting.
    """
    return (ROOT / path).read_bytes().decode("utf-8", errors="replace")


def _prose(path: Path) -> str:
    """Whitespace-collapsed text of a tracked markdown file, fences included.

    Collapsing matters because `docs/architecture.md` wraps the retired pipeline
    chain across a line break, so a search that sees one physical line at a time
    lets it through while the reader sees one sentence.
    """
    return " ".join(_read(path).split())


def test_no_live_doc_names_a_retired_path() -> None:
    offenders: dict[str, list[str]] = {}
    for path in _tracked_markdown():
        if _is_archive(path):
            continue
        text = _prose(path)
        hits = [needle for needle in RETIRED_PATHS if needle in text]
        if hits:
            offenders[str(path)] = hits
    assert not offenders, f"retired paths still documented: {offenders}"


def test_no_doc_names_the_old_pipeline_chain() -> None:
    offenders = [
        str(path)
        for path in _tracked_markdown()
        if any(needle in _prose(path) for needle in RETIRED_CHAINS)
    ]
    assert not offenders, f"retired pipeline chain still documented: {offenders}"


def test_the_archive_exemption_is_exactly_the_dated_narratives() -> None:
    """The retired-path exemption's size is pinned.

    `_is_archive` skips the two `NARRATIVE_FILES` incident records *and* any tracked
    markdown whose own filename contains a `YYYY-MM-DD` date. That second half is a
    convention, not a declaration: naming a live document
    `docs/runbook_2026-09-08.md` would remove it, and every claim inside it, from
    retired-path checks. This asserts what the rule resolves to today, so a new exempt file
    is a decision someone had to write down here rather than a silent omission.
    """
    exempt = {str(path) for path in _tracked_markdown() if _is_archive(path)}
    assert exempt == {
        "docs/DEPLOY_FLY.md",  # dated incident record
        "docs/development_log.md",  # the archive the live docs point at
        "docs/platform_updates_2026-09-04.md",  # exempt by its filename date
    }
