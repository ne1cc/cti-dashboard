"""Build the ``county_access_context`` seed (Issue #72) from three federal files.

One row per county: Census Gazetteer centroid, CDC/ATSDR Social Vulnerability Index
(SVI) overall percentile rank, and USDA ERS Rural-Urban Commuting Area (RUCA) group.
Download once into a scratch directory, then run::

    python -m src.ingest.access_context --states 10,24,44,50 \
        --gazetteer <dir>/2024_Gaz_counties_national.txt \
        --svi <dir>/SVI_2022_US_county.csv \
        --ruca <dir>/2020-rural-urban-commuting-area-codes-census-tracts.csv \
        --out dbt_clinical_trials/seeds/county_access_context.csv

Sources and releases are recorded in docs/access_methodology.md. Everything is local
file parsing (no network), so tests run offline against tiny committed excerpts.

RUCA is a census-tract classification. A county has no single RUCA code, so each
tract's primary code is mapped to a group (metropolitan 1-3, micropolitan 4-6, small
town 7-9, rural 10, not coded 99) and the county takes the group holding the most
residents. ``ruca_group_pop_share`` says how dominant that group is, so a mixed county
is visible to consumers.
"""

import argparse
import csv
from collections import defaultdict
from pathlib import Path

COLUMNS = [
    "county_fips",
    "county_name",
    "state_fips",
    "latitude",
    "longitude",
    "svi_rpl_themes",
    "svi_release",
    "ruca_group",
    "ruca_group_pop_share",
    "ruca_release",
]
SVI_RELEASE = "CDC/ATSDR SVI 2022"
RUCA_RELEASE = "USDA ERS RUCA 2020"
# Group order breaks population ties toward the more urban group, deterministically.
_RUCA_GROUPS = ("metropolitan", "micropolitan", "small_town", "rural", "not_coded")


def ruca_group(primary_code: str) -> str:
    code = int(primary_code)
    if 1 <= code <= 3:
        return "metropolitan"
    if 4 <= code <= 6:
        return "micropolitan"
    if 7 <= code <= 9:
        return "small_town"
    if code == 10:
        return "rural"
    return "not_coded"  # 99 = zero-population / not coded


def _read(path: Path, **kwargs: object) -> list[dict[str, str]]:
    with path.open(newline="", **kwargs) as f:  # type: ignore[call-overload]
        return [
            {k.strip(): (v or "").strip() for k, v in r.items() if k} for r in csv.DictReader(f)
        ]


def _read_gazetteer(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        # Census Gazetteer files are tab-separated and pad header names and lines.
        return [
            {k.strip(): (v or "").strip() for k, v in r.items() if k}
            for r in csv.DictReader(f, delimiter="\t")
        ]


def build_rows(gazetteer: Path, svi: Path, ruca: Path, states: set[str]) -> list[dict[str, object]]:
    """Context rows for every Gazetteer county in ``states`` (state FIPS codes)."""
    svi_rpl = {
        r["FIPS"]: float(r["RPL_THEMES"])
        for r in _read(svi, encoding="utf-8-sig")
        if r["RPL_THEMES"] != "" and float(r["RPL_THEMES"]) >= 0  # -999 = not computed
    }
    pop_by_group: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in _read(ruca, encoding="latin-1"):
        pop_by_group[r["CountyFIPS20"]][ruca_group(r["PrimaryRUCA"])] += int(r["Population"] or 0)

    out: list[dict[str, object]] = []
    for r in _read_gazetteer(gazetteer):
        fips = r["GEOID"]
        if fips[:2] not in states:
            continue
        groups = pop_by_group.get(fips, {})
        total = sum(groups.values())
        # Max population wins; _RUCA_GROUPS order settles ties.
        top = min(groups, key=lambda g: (-groups[g], _RUCA_GROUPS.index(g))) if groups else None
        out.append(
            {
                "county_fips": fips,
                "county_name": r["NAME"],
                "state_fips": fips[:2],
                "latitude": float(r["INTPTLAT"]),
                "longitude": float(r["INTPTLONG"]),
                "svi_rpl_themes": svi_rpl.get(fips),
                "svi_release": SVI_RELEASE,
                "ruca_group": top,
                "ruca_group_pop_share": round(groups[top] / total, 4) if top and total else None,
                "ruca_release": RUCA_RELEASE,
            }
        )
    return sorted(out, key=lambda r: str(r["county_fips"]))


def write_csv(rows: list[dict[str, object]], out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument(
        "--states", required=True, help="Comma-separated state FIPS whose counties are kept."
    )
    p.add_argument("--gazetteer", type=Path, required=True, help="Census Gazetteer counties file.")
    p.add_argument("--svi", type=Path, required=True, help="CDC/ATSDR SVI county CSV.")
    p.add_argument("--ruca", type=Path, required=True, help="USDA ERS RUCA census-tract CSV.")
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args(argv)
    states = {s.strip() for s in args.states.split(",") if s.strip()}
    rows = build_rows(args.gazetteer, args.svi, args.ruca, states)
    write_csv(rows, args.out)
    print(f"wrote {len(rows)} rows to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
