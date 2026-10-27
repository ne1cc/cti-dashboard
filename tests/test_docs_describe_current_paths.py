"""Keep local documentation links and concrete repository file references usable."""

import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest

ROOT = Path(__file__).resolve().parents[1]
LINKS = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
INLINE_FILES = re.compile(r"`([^`\n]+)`")
# These files are created by setup, builds or local runs, rather than tracked source.
GENERATED_FILES = {".env", "dbt_clinical_trials/profiles.yml", ".streamlit/secrets.toml"}
GENERATED_DIRS = (
    "data/",
    "logs/",
    "reports/",
    "dbt_clinical_trials/target/",
    "dbt_clinical_trials/logs/",
    "/app/data/",
)


def broken_references(doc: Path, text: str, *, inline_files: bool = True) -> list[str]:
    targets = [(target.strip().strip("<>"), doc.parent) for target in LINKS.findall(text)]
    if inline_files:
        for token in INLINE_FILES.findall(text):
            token = re.sub(r":\d+$", "", token)
            if "/" in token and re.fullmatch(r"[\w./-]+\.[a-zA-Z0-9]+", token):
                targets.append((token, ROOT))
    missing = []
    for target, base in targets:
        url = urlsplit(target)
        path = unquote(url.path)
        if url.scheme or url.netloc or not path:
            continue
        if path in GENERATED_FILES or path.startswith(GENERATED_DIRS):
            continue
        if not (base / path).exists():
            missing.append(target)
    return missing


def test_local_documentation_references_exist() -> None:
    docs = [
        ROOT / "README.md",
        ROOT / "PROJECT_DOCUMENTATION.md",
        *sorted((ROOT / "docs").glob("*.md")),
    ]
    archives = sorted((ROOT / "docs/archive").glob("*.md"))
    offenders = {}
    for doc in [*docs, *archives]:
        missing = broken_references(
            doc, doc.read_text(encoding="utf-8", errors="replace"), inline_files=doc in docs
        )
        if missing:
            offenders[str(doc.relative_to(ROOT))] = missing
    assert not offenders, f"Broken documentation references: {offenders}"


@pytest.mark.parametrize("text", ["[missing](missing.md#section)", "`src/missing.py`"])
def test_missing_local_reference_is_detected(tmp_path: Path, text: str) -> None:
    assert broken_references(tmp_path / "guide.md", text)


def test_links_use_the_document_directory(tmp_path: Path) -> None:
    (tmp_path / "real file.md").touch()
    text = (
        "[local](<real%20file.md#section>) "
        "[external](https://example.com/missing.md) [anchor](#here)"
    )
    assert broken_references(tmp_path / "guide.md", text) == []
    assert broken_references(tmp_path / "nested/guide.md", "[wrong base](real%20file.md)")
