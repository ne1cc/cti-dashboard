"""Census ACS 5-year population extract for the `dim_population` denominators.

Intent: turn two ACS 5-year tables (B01001 sex by age, B03002 Hispanic origin by
race) into one long-format CSV of population counts by state, county and ZCTA,
committed as the `acs_population_extract` dbt seed.

Source: the Census Bureau's table-based Summary File bulk files, which need no
API key (the keyed `api.census.gov` endpoint was deliberately not used so the
ingest stays reproducible without credentials). Parsing takes iterables of
lines, so tests run on a committed fixture with no network.

Limits:
- Estimates only; margins of error (the `_M` columns) are not carried.
- Full files are 80-200 MB each, so a refresh streams roughly 370 MB.
- shortcut: counties and ZCTAs are an explicit subset chosen on the command
  line, because a seed holding all ~3,200 counties and ~33,000 ZCTAs is not
  "small"; widen the selection (or move to a warehouse load) when a consumer
  needs more geographies.
"""

from __future__ import annotations

import argparse
import csv
from collections.abc import Iterable, Iterator
from pathlib import Path

import requests

VINTAGE = "ACS 2020-2024 5-year"
BASE_URL = "https://www2.census.gov/programs-surveys/acs/summary_file/2024/table-based-SF"
DATA_URL = BASE_URL + "/data/5YRData/acsdt5y2024-{table}.dat"
GEOS_URL = BASE_URL + "/documentation/Geos20245YR.txt"

SUMLEVEL_TO_GEO_LEVEL = {"040": "state", "050": "county", "860": "zcta"}
# ZCTA GEO_IDs carry a geographic-component code of 'Z2' where states/counties carry '00'.
PLAIN_GEO_PREFIXES = ("0400000US", "0500000US", "860Z200US")

# B01001 line numbers: male = 003..025, female = 027..049 (same 23 age rows each).
AGE_BANDS = {
    "0-17": range(3, 7),
    "18-44": range(7, 15),
    "45-64": range(15, 20),
    "65+": range(20, 26),
}
SEX_OFFSET = {"male": 0, "female": 24}

# B03002 lines kept: 003..009 are the non-Hispanic race rows, 012 is Hispanic or Latino (any race).
RACE_ETHNICITY = {
    3: "Non-Hispanic White",
    4: "Non-Hispanic Black",
    5: "Non-Hispanic American Indian and Alaska Native",
    6: "Non-Hispanic Asian",
    7: "Non-Hispanic Native Hawaiian and Other Pacific Islander",
    8: "Non-Hispanic Some other race",
    9: "Non-Hispanic Two or more races",
    12: "Hispanic or Latino",
}

COLUMNS = [
    "geo_level",
    "geo_id",
    "geo_name",
    "state_fips",
    "dimension",
    "age_band",
    "sex",
    "race_ethnicity",
    "population",
    "table_id",
    "vintage",
]

# {geo_id: {line_number: estimate}}
Estimates = dict[str, dict[int, int]]


def _split_geo_id(geo_id: str) -> tuple[str, str]:
    """Return (sumlevel, fips-style id) from a GEO_ID such as '0500000US25025'."""
    return geo_id[:3], geo_id.split("US", 1)[1]


def _select(geo_id: str, county_states: set[str], zctas: set[str]) -> bool:
    """True when the geography belongs in the extract: all states plus the chosen counties/ZCTAs.

    Plain geographies only (prefix in PLAIN_GEO_PREFIXES): the component and variant codes
    in between mark slices of a state (e.g. 'A0'/'H0' metro/non-metro) that reuse its FIPS
    and would double-count it.
    """
    sumlevel, ident = _split_geo_id(geo_id)
    if not geo_id.startswith(PLAIN_GEO_PREFIXES):
        return False
    if sumlevel == "040":
        return True
    if sumlevel == "050":
        return ident[:2] in county_states
    return sumlevel == "860" and ident in zctas


def read_table(
    lines: Iterable[str], table_id: str, county_states: set[str], zctas: set[str]
) -> Estimates:
    """Stream one ACS table file into {geo_id: {line: estimate}} for the selected geographies.

    Contract:
    - Returns only `_E` (estimate) columns; non-numeric or negative values (Census
      uses sentinels such as -555555555) are left out, so callers see a gap, not a count.
    - Raises ValueError if the header does not carry the expected `{table_id}_E001` column.
    """
    rows = iter(lines)
    header = next(rows).rstrip("\r\n").split("|")
    if f"{table_id}_E001" not in header:
        raise ValueError(f"{table_id}: header lacks {table_id}_E001; wrong table file?")
    wanted = {i: int(name.rsplit("E", 1)[1]) for i, name in enumerate(header) if "_E" in name}
    out: Estimates = {}
    for raw in rows:
        cells = raw.rstrip("\r\n").split("|")
        if not _select(cells[0], county_states, zctas):
            continue
        out[cells[0]] = {line: int(cells[i]) for i, line in wanted.items() if cells[i].isdigit()}
    return out


def read_names(lines: Iterable[str], wanted: Iterable[str]) -> dict[str, str]:
    """Map GEO_ID -> NAME from the pipe-delimited Geos file, for `wanted` ids only."""
    keep = set(wanted)
    rows = iter(lines)
    header = next(rows).rstrip("\r\n").split("|")
    gid, name = header.index("GEO_ID"), header.index("NAME")
    cells = (r.rstrip("\r\n").split("|") for r in rows)
    return {c[gid]: c[name] for c in cells if len(c) > name and c[gid] in keep}


def build_rows(
    sex_age: Estimates, race: Estimates, names: dict[str, str]
) -> Iterator[dict[str, str | int]]:
    """Yield long-format rows (total, age x sex, race/ethnicity) per geography.

    Contract:
    - Geographies missing from either table or from `names` are skipped.
    - An age band is emitted only when every B01001 line in it has an estimate,
      so a band is never a partial sum.
    - Rows are ordered by geo_id for a stable, diffable seed.
    """
    for geo_id in sorted(sex_age.keys() & race.keys() & names.keys()):
        sumlevel, ident = _split_geo_id(geo_id)
        base = {
            "geo_level": SUMLEVEL_TO_GEO_LEVEL[sumlevel],
            "geo_id": ident,
            "geo_name": names[geo_id],
            "state_fips": ident[:2] if sumlevel in {"040", "050"} else "",
            "vintage": VINTAGE,
        }
        b01, b03 = sex_age[geo_id], race[geo_id]
        empty = {"age_band": "", "sex": "", "race_ethnicity": ""}
        if 1 in b01:
            yield {
                **base,
                **empty,
                "dimension": "total",
                "population": b01[1],
                "table_id": "B01001",
            }
        for sex, offset in SEX_OFFSET.items():
            for band, lines_in_band in AGE_BANDS.items():
                parts = [b01.get(n + offset) for n in lines_in_band]
                if None in parts:
                    continue
                yield {
                    **base,
                    **empty,
                    "dimension": "age_sex",
                    "age_band": band,
                    "sex": sex,
                    "population": sum(p for p in parts if p is not None),
                    "table_id": "B01001",
                }
        for line, label in RACE_ETHNICITY.items():
            if line in b03:
                yield {
                    **base,
                    **empty,
                    "dimension": "race_ethnicity",
                    "race_ethnicity": label,
                    "population": b03[line],
                    "table_id": "B03002",
                }


def _stream(url: str) -> Iterator[str]:
    """Yield decoded lines of a remote text file without holding it in memory."""
    with requests.get(url, stream=True, timeout=120) as resp:
        resp.raise_for_status()
        resp.encoding = "utf-8"
        for line in resp.iter_lines(decode_unicode=True):
            yield line if isinstance(line, str) else line.decode("utf-8")


def write_csv(rows: Iterable[dict[str, str | int]], out: Path) -> int:
    """Write rows with the fixed seed column order; return the row count."""
    n = 0
    with out.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
            n += 1
    return n


def main(argv: list[str] | None = None) -> int:
    """CLI: download the two tables and the geo names, then write the extract CSV."""
    p = argparse.ArgumentParser(description="Build the ACS population seed extract.")
    p.add_argument("--out", type=Path, required=True)
    p.add_argument(
        "--county-states", nargs="*", default=[], help="state FIPS whose counties to keep"
    )
    p.add_argument("--zctas", nargs="*", default=[], help="5-digit ZCTAs to keep")
    args = p.parse_args(argv)
    states, zctas = set(args.county_states), set(args.zctas)
    sex_age = read_table(_stream(DATA_URL.format(table="b01001")), "B01001", states, zctas)
    race = read_table(_stream(DATA_URL.format(table="b03002")), "B03002", states, zctas)
    names = read_names(_stream(GEOS_URL), sex_age.keys() & race.keys())
    print(f"wrote {write_csv(build_rows(sex_age, race, names), args.out)} rows to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
