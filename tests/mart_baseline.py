"""Dump and compare the fixture warehouse's mart outputs (profile-retirement baseline).

Regenerate the committed baseline (see tests/fixtures/mart_baseline/README.md):
    python -m tests.mart_baseline --root <empty dir>
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path
from typing import Any

import duckdb

from tests.fixture_warehouse import build_fixture_warehouse

BASELINE_DIR = Path(__file__).parent / "fixtures" / "mart_baseline"
MARTS_SCHEMA = "main_marts"
REL_TOL = 1e-6
# Calendar spine: no profile grain, 1 MB, and nothing here changes it.
SKIP_TABLES = {"dim_date"}


def dump_marts(root: Path) -> dict[str, dict[str, Any]]:
    """Every table in the marts schema as {columns, rows}, rows sorted for stable diffs."""
    con = duckdb.connect(str(root / "data/warehouse/clinical_trials.duckdb"), read_only=True)
    try:
        names = [
            r[0]
            for r in con.execute(
                "select table_name from information_schema.tables "
                "where table_schema = ? order by 1",
                [MARTS_SCHEMA],
            ).fetchall()
            if r[0] not in SKIP_TABLES
        ]
        out: dict[str, dict[str, Any]] = {}
        for name in names:
            cur = con.execute(f"select * from {MARTS_SCHEMA}.{name}")  # noqa: S608
            columns = [d[0] for d in cur.description]
            rows = [[_cell(v) for v in row] for row in cur.fetchall()]
            rows.sort(key=lambda r: json.dumps(r, sort_keys=True))
            out[name] = {"columns": columns, "rows": rows}
        return out
    finally:
        con.close()


def _cell(value: Any) -> Any:
    if value is None or isinstance(value, bool | int | float | str):
        return value
    return str(value)


def cells_match(expected: Any, actual: Any) -> bool:
    """Floats (derived rates) within 1e-6 relative; everything else (ids, counts) exact."""
    if isinstance(expected, float) and isinstance(actual, float):
        return math.isclose(expected, actual, rel_tol=REL_TOL, abs_tol=0.0)
    return bool(expected == actual) and type(expected) is type(actual)


def load_baseline() -> dict[str, dict[str, Any]]:
    return {p.stem: json.loads(p.read_text()) for p in sorted(BASELINE_DIR.glob("*.json"))}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tests.mart_baseline")
    parser.add_argument("--root", type=Path, required=True, help="Empty scratch fixture root")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    if root.exists() and any(root.iterdir()):
        sys.stderr.write(f"Refusing to overwrite nonempty directory: {root}\n")
        return 1
    os.environ["CTI_PROJECT_ROOT"] = str(root)
    build_fixture_warehouse(root)
    BASELINE_DIR.mkdir(parents=True, exist_ok=True)
    for name, table in dump_marts(root).items():
        # One row per line so a regenerated baseline diffs row by row.
        lines = ",\n".join(json.dumps(r) for r in table["rows"])
        (BASELINE_DIR / f"{name}.json").write_text(
            f'{{"columns": {json.dumps(table["columns"])},\n"rows": [\n{lines}\n]}}\n'
        )
    print(f"Baseline written to {BASELINE_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
