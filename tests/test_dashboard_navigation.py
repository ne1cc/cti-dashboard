"""The router registers every page and keeps the URLs file navigation published."""

import sys
from pathlib import Path

DASHBOARD = Path(__file__).resolve().parents[1] / "dashboard"
sys.path.insert(0, str(DASHBOARD))

from components.navigation import SECTIONS  # noqa: E402

SPECS = [spec for specs in SECTIONS.values() for spec in specs]


def test_every_page_file_is_registered_exactly_once():
    registered = [spec.path for spec in SPECS]
    on_disk = {"overview.py", *(f"pages/{p.name}" for p in (DASHBOARD / "pages").glob("*.py"))}
    assert len(registered) == len(set(registered))
    assert set(registered) == on_disk


def test_registered_paths_exist():
    for spec in SPECS:
        assert (DASHBOARD / spec.path).is_file(), spec.path


def test_published_page_urls_are_preserved():
    # File-based navigation served pages/N_Name.py at /Name.
    for spec in SPECS:
        if spec.path == "overview.py":
            assert spec.url_path is None, "overview is the default page at /"
        else:
            assert spec.url_path == Path(spec.path).stem.split("_", 1)[1]
