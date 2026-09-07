"""Docs must describe the data tree that exists — or be declared history.

This repo's last two production incidents were both "the docs described shape A
while the code did shape B". The guard is cheap, and it is the only thing that
keeps the next migration from re-creating the same class of defect.

Two kinds of claim are checked. A *path* claim is compared against the tree the
profile migration left behind. A *count* claim is compared against the commands
that produce it, because a number nobody can regenerate is a lie with a shelf
life. Both are read against the live documentation only: `_is_archive` exempts
dated incident records — `docs/DEPLOY_FLY.md` and `docs/development_log.md`, named
in `NARRATIVE_FILES` — plus **any tracked markdown whose own filename contains a
`YYYY-MM-DD` date** (so `docs/platform_updates_2026-09-04.md` is dated wholesale),
which name dead paths and past counts on purpose: deleting that evidence is worse
than the stale path. That filename rule is a real exemption, so a new live document
named after a date opts itself out of both guards silently —
`test_the_archive_exemption_is_exactly_the_dated_narratives` pins the resulting set
and fails if it ever changes. The retired pipeline chain is checked everywhere,
because every sentence that states it describes what a command does right now.
"""

import collections
import json
import re
import subprocess
from pathlib import Path
from typing import NamedTuple

import pytest

ROOT = Path(__file__).resolve().parents[1]

# Dated narratives of trees that no longer exist. Rewriting them destroys the
# evidence, so they are exempt from the path list and from the count list (see
# `_is_archive`); the pipeline-chain guard still reads every one of them. Only
# files `git ls-files "*.md"` can return belong here — `docs/superpowers/` is
# gitignored and holds no tracked markdown, so it used to sit in this list
# exempting nothing. `test_the_archive_exemption_is_exactly_the_dated_narratives`
# pins what the exemption resolves to in practice.
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

# Fenced blocks: commands, logs and tree listings. Not claims about the suite.
FENCE_RE = re.compile(r"```.*?```", re.DOTALL)

# Lines that start a new markdown block: heading, list bullet, table row.
BLOCK_START_RE = re.compile(r"^\s*(?:#{1,6}\s|[-*+]\s|\|)")

# A count of the dbt or pytest layer written into prose, in the word orders these
# docs actually use: "115 dbt tests", "dbt — 73 data tests", "dbt, 73 tests",
# "| dbt data tests | **73** |", "| pytest | **N** |", "a 162-test pytest suite".
# §14's `| pytest | **N** |` row is this repository's single home for that count,
# and it only entered the guard's reach when the cell shape stopped demanding the
# literal word "tests" after "pytest".
#
# This list *is* the guard's reach: a count phrased any other way ("the dbt suite
# has 999 tests", "| dbt | 999 |") is invisible to it, and every document that
# describes what the guard catches points here rather than claiming universality.
#
# The windows are deliberately narrow. `[^\d.]{0,4}` admits a punctuation mark and
# a space ("dbt, 73") but not a word, so `make dbt-test`: 114/114 tests PASS — a
# dated build-log line in docs/development_log.md — is not read as a claim of 114
# dbt tests. The two table-cell shapes require a pipe or colon *and* a **bold**
# number, because "29 tested dbt models, 419 recruiting trials" is a model count
# of 29 and a trial count of 419, not a model count of 419.
#
# (pattern, count group, kind group — kind group None when the shape can only be pytest)
COUNT_SHAPES: list[tuple[re.Pattern[str], int, int | None]] = [
    (re.compile(r"(\d+)\s+dbt\s+(?:data\s+)?(tests?|models?)\b", re.I), 1, 2),
    (re.compile(r"\bdbt\b[^\d.]{0,4}(\d+)\s+(?:data\s+)?(tests?|models?)\b", re.I), 1, 2),
    (re.compile(r"\bdbt\s+(?:data\s+)?(tests?|models?)\b[ |:]{1,4}\*{2}(\d+)", re.I), 2, 1),
    (re.compile(r"(\d+)\s+pytest\s+tests?\b", re.I), 1, None),
    (re.compile(r"\bpytest\b[^\d.]{0,4}(\d+)\s+tests?\b", re.I), 1, None),
    (re.compile(r"\bpytest\b(?:\s+tests?)?[ |:]{1,4}\*{2}(\d+)", re.I), 1, None),
    (re.compile(r"\b(\d+)[\s-]+test\s+pytest\s+suite\b", re.I), 1, None),
]


class LiveCounts(NamedTuple):
    """What the tools report right now, in the units the docs state them in."""

    dbt_models: int
    dbt_tests: int
    pytest_tests: int

    def bucket(self, kind: str) -> int:
        return {"model": self.dbt_models, "test": self.dbt_tests, "pytest": self.pytest_tests}[kind]


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

    They are exempt from the path guard and from the dated-or-live count guard,
    and from nothing else. The development log is the archive the other docs now
    *point at* for retired names, and a date-named update file is a snapshot of
    its own day: forcing live numbers into either would replace accurate history
    with numbers that belong to a different date. Cost of being wrong: a stale
    figure inside one of these files goes unguarded — which is the price of
    keeping the record at all.
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


def _units_from_text(text: str) -> list[str]:
    """Claim-sized units of markdown text: one sentence inside one block.

    This is the *only* segmentation the count guard performs, and the guard calls
    it on `_read(path)` directly — no wrapper, because a wrapper is a bypass: the
    naive whole-file blob below was measured on 2026-09-07 to leave the live tree
    at zero violations while missing the exact shape this function exists for.

    Two failures drove the design. Fenced blocks fuse into the stream, so a number
    200 lines from the word `dbt` reads as a test count —
    PROJECT_DOCUMENTATION.md's repository layout was reported as "a 162-test pytest
    suite". And a date qualifies only the claim it sits with: splitting on `". "`
    alone let PROJECT_DOCUMENTATION.md's "Automated tests | **115 dbt data tests**"
    table row borrow "(single snapshot, 2026-07-24)" from the table 197 characters
    below it, and pass. So units reset at blank lines, headings, bullets and table
    rows — which keeps a docs/DEPLOY_FLY.md bullet's own date attached to its own
    counts, and stops a neighbouring row's date from laundering a stale one.

    Taking text rather than a `Path` is what lets `test_the_count_guard_can_fail`
    feed this function the real markdown of that laundering failure and assert both
    directions; a guard that only ever reads live files cannot show the segmentation
    working, because on today's tree blob and segmented forms agree.
    """
    stripped = FENCE_RE.sub("\n", text)
    units: list[str] = []
    for line in stripped.splitlines():
        if not line.strip():
            units.append("")  # a blank line closes the current unit
            continue
        if not units or units[-1] == "" or BLOCK_START_RE.match(line):
            units.append(line)
        else:
            units[-1] = f"{units[-1]} {line}"
    return [sentence for unit in units for sentence in _sentences(" ".join(unit.split()))]


def _sentences(text: str) -> list[str]:
    """Sentences, split on the only terminator these docs use consistently.

    Deliberately naive. `". "` splits a block into claim-sized units well enough
    to keep a date and the count it qualifies together, and a false split only
    ever shortens a sentence — which can produce an extra "needs a date" report,
    never a silent pass.
    """
    return [s.strip() for s in text.split(". ") if s.strip()]


def _counts_in(unit: str) -> list[tuple[int, str]]:
    """Every (count, kind) dbt/pytest claim in one unit.

    `kind` is a `LiveCounts` bucket key: "model", "test" or "pytest".
    """
    found: list[tuple[int, str]] = []
    for pattern, count_group, kind_group in COUNT_SHAPES:
        for match in pattern.finditer(unit):
            if kind_group is None:
                kind = "pytest"
            else:
                kind = "model" if match[kind_group].lower().startswith("model") else "test"
            found.append((int(match[count_group]), kind))
    return found


def _stale_count_claims(live: LiveCounts, units_by_path: dict[Path, list[str]]) -> list[str]:
    """Units asserting a dbt/pytest count with neither a date nor a live value."""
    violations: list[str] = []
    for path, units in units_by_path.items():
        for unit in units:
            for count, kind in _counts_in(unit):
                if DATE_RE.search(unit) or count == live.bucket(kind):
                    continue
                violations.append(
                    f"{path}: [{kind}] says {count}, the tool says {live.bucket(kind)}: "
                    f"'{unit[:160]}'"
                )
    return violations


@pytest.fixture(scope="session")
def live_counts(dbt_manifest: Path) -> LiveCounts:
    """The numbers the docs are allowed to claim, straight from the tools.

    `dbt_manifest` is taken purely as a producer: it forces `dbt parse` to have
    run, because `.github/workflows/ci.yml` has no dbt step of its own but does
    run tests that use that session fixture — without it the manifest read below
    is a `FileNotFoundError` in CI.
    """
    collected = subprocess.run(
        ["uv", "run", "pytest", "--collect-only"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    pytest_count = next(
        int(line.split()[0]) for line in collected.splitlines() if "tests collected" in line
    )
    # The summary line ends with its duration — observed 2026-09-05 as
    # "162 tests collected in 4.40s" and 2026-09-07 as "284 tests collected in
    # 1.42s" — so match the phrase, not the end of the line. `pyproject.toml`
    # already sets `addopts = "-q"`, which still prints it; a second `-q` in this
    # argv would suppress it and make the `next()` above raise StopIteration.

    manifest = json.loads(Path(dbt_manifest).read_text())
    # resource_type == "test" only: `nodes` also holds models, seeds and analyses
    # (32 / 4 / 4 measured 2026-09-07), so subtracting models from the total
    # overcounts tests by 8 and the guard never agrees with the prose it checks.
    counts = collections.Counter(n["resource_type"] for n in manifest["nodes"].values())
    return LiveCounts(
        dbt_models=counts["model"],
        dbt_tests=counts["test"],
        pytest_tests=pytest_count,
    )


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


def test_documented_test_counts_match_the_tools(live_counts: LiveCounts) -> None:
    """Counts in prose are only honest while a command regenerates them, so this
    compares the prose to the command rather than trusting either."""
    positioning = _prose(Path("docs/competitive_positioning.md"))
    assert (
        f"{live_counts.dbt_tests} dbt tests and a {live_counts.pytest_tests}-test pytest suite"
        in positioning
    )


def test_every_documented_count_is_dated_or_live(live_counts: LiveCounts) -> None:
    """A number with no date and no command behind it is the next falsified claim.

    Scope is the live documentation only: `_is_archive` files are skipped, because
    their figures were true on a date they carry in their name or in their text,
    and a live count pasted over them belongs to a different day. The date-in-text
    rule still does all the work outside them — docs/DEPLOY_FLY.md's two *dated*
    count measurements (115 dbt tests on 2026-09-05, 137 on 2026-09-07 at commit
    `78c2050`) are left exactly as written even though the count has since moved:
    rewriting a dated sentence falsifies a measurement, which is worse than the
    staleness this file exists to remove. What is not allowed anywhere is an
    undated count the tools no longer agree with.
    """
    units_by_path = {
        path: _units_from_text(_read(path)) for path in _tracked_markdown() if not _is_archive(path)
    }
    violations = _stale_count_claims(live_counts, units_by_path)
    assert not violations, "counts with no date:\n" + "\n".join(violations)


def test_the_count_guard_can_fail(live_counts: LiveCounts) -> None:
    """A guard that cannot fail is how the docs went stale in the first place.

    Synthetic paths, so no doc has to be edited to prove the matcher has teeth.
    Every entry in `COUNT_SHAPES` now has a case here: measured 2026-09-07 against
    the shipped list, four of the seven — `dbt — N data tests`, `N pytest tests`,
    `pytest — N tests` and the `| pytest | **N** |` cell — matched none of its
    cases, so deleting them left this test green. A regex that guards no document
    and no test is dead code. The last two cases matter most: they are the shapes
    that passed the naive version of this guard while stating 115, 162 and 73, and
    the block segmentation that stops a stale count borrowing a neighbour's date.
    """
    live = live_counts
    stale = live.dbt_tests + 7

    accepted = {Path("scratch.md"): [f"Quality: {live.dbt_tests} dbt tests (measured 2026-09-07)"]}
    assert not _stale_count_claims(live, accepted), "the guard rejects a live, dated claim"

    undated = {Path("scratch.md"): [f"Quality: {stale} dbt tests, all green"]}
    assert _stale_count_claims(live, undated), "the guard misses an undated dbt test count"

    cell = {Path("scratch.md"): [f"| dbt data tests | **{stale}** | grains, keys |"]}
    assert _stale_count_claims(live, cell), "the guard misses a bold table cell"

    suite = {Path("scratch.md"): [f"Quality: a {live.pytest_tests + 3}-test pytest suite"]}
    assert _stale_count_claims(live, suite), "the guard misses a pytest suite count"

    # Lives in README.md and docs/data_quality_framework.md, so it has real work to
    # do — but until now nothing proved it was wired up.
    pytest_prose = {Path("scratch.md"): [f"Coverage: {live.pytest_tests + 5} pytest tests"]}
    assert _stale_count_claims(live, pytest_prose), "the guard misses an `N pytest tests` claim"

    # The two prose orders no live document matches today: `dbt — N data tests`,
    # which the header comment names, and its pytest mirror, which it does not.
    # Kept because a document may use them tomorrow; these cases are their only
    # witness, so removing either regex now fails loudly instead of silently.
    noun_first = {Path("scratch.md"): [f"Quality: dbt — {stale} data tests, all green"]}
    assert _stale_count_claims(live, noun_first), "the guard misses a `dbt — N data tests` claim"

    colon_first = {Path("scratch.md"): [f"pytest — {live.pytest_tests + 5} tests cover the CLI"]}
    assert _stale_count_claims(live, colon_first), "the guard misses a `pytest — N tests` claim"

    # The `| pytest | **N** |` cell, in the label-column form PROJECT_DOCUMENTATION.md
    # §14 uses. Before this case existed the shape had no witness in either direction:
    # the matcher needed the literal word "tests" after "pytest", so a stale (and
    # undated) row of that table passed while its `dbt data tests` neighbour fired.
    row = {Path("scratch.md"): [f"| pytest | **{live.pytest_tests + 11}** | CLI, transform |"]}
    assert _stale_count_claims(live, row), "the guard misses a `| pytest | **N** |` table cell"

    # A date in a *neighbouring* block does not qualify the claim above it.
    borrowed = {
        Path("scratch.md"): [
            f"| Automated tests | **{stale} dbt data tests** — all green |",
            "**Live warehouse figures (single snapshot, 2026-07-24):**",
        ]
    }
    assert _stale_count_claims(live, borrowed), "a neighbouring row's date launders a stale count"

    # ...and the block segmentation that makes the case above fail is load-bearing, so
    # it gets its own witness. Real markdown, fed through the same code the guard runs:
    # segmented, the stale row is its own unit and is reported; flattened into one
    # blob — what this degenerates to if the segmentation is dropped — the
    # next block's `(single snapshot, 2026-07-24)` qualifies it and the guard is
    # silently vacuous. Measured 2026-09-07: the blob form found zero violations on
    # the whole live tree, which is exactly how this went unnoticed.
    laundered = (
        "| Property | Value |\n"
        f"| Automated tests | **{stale} dbt data tests** | 2,618 records |\n"
        "\n"
        "**Live warehouse figures (single snapshot, 2026-07-24):**\n"
    )
    segmented = _units_from_text(laundered)
    assert any(DATE_RE.search(u) for u in segmented), "the dated block vanished from the units"
    assert _stale_count_claims(live, {Path("scratch.md"): segmented}), (
        "`_units_from_text` no longer segments by block: a stale count borrowed the next "
        "block's date and passed"
    )
    assert not _stale_count_claims(live, {Path("scratch.md"): [" ".join(laundered.split())]}), (
        "the naive whole-file blob now reports the stale count too, so this case no "
        "longer proves the segmentation earns its existence"
    )


def test_the_archive_exemption_is_exactly_the_dated_narratives() -> None:
    """The exemption is a hole in two guards, so its size is pinned.

    `_is_archive` skips the two `NARRATIVE_FILES` incident records *and* any tracked
    markdown whose own filename contains a `YYYY-MM-DD` date. That second half is a
    convention, not a declaration: naming a live document
    `docs/runbook_2026-09-08.md` would remove it, and every claim inside it, from
    both guards. This asserts what the rule resolves to today, so a new exempt file
    is a decision someone had to write down here rather than a silent omission.
    """
    exempt = {str(path) for path in _tracked_markdown() if _is_archive(path)}
    assert exempt == {
        "docs/DEPLOY_FLY.md",  # dated incident record
        "docs/development_log.md",  # the archive the live docs point at
        "docs/platform_updates_2026-09-04.md",  # exempt by its filename date
    }
