"""Build the ``acs_population`` seed from Census ACS 5-year table-based summary files.

The Census API now requires a key, so this reads the keyless bulk files instead:
``acsdt5y<year>-b01001.dat`` (sex by age), ``acsdt5y<year>-b03002.dat`` (Hispanic or
Latino origin by race) and ``Geos<year>5YR.txt`` (geography names), all under
https://www2.census.gov/programs-surveys/acs/summary_file/<year>/table-based-SF/.
Download them once into a scratch directory and run::

    python -m src.ingest.acs_population --year 2024 --data-dir <dir> \
        --county-states 10,44,50,33 --zctas 19711,02903 \
        --out dbt_clinical_trials/seeds/acs_population.csv

Everything is local file parsing: no network call happens here, so tests run offline
against a small committed fixture.
"""

import argparse
import csv
import math
from collections.abc import Iterable, Iterator
from pathlib import Path

GEO_PREFIXES = {"0400000US": "state", "0500000US": "county", "860Z200US": "zcta"}

# B01001 variable numbers (E = estimate, M = margin of error) per sex and age band.
# Male rows are 003-025, female rows are 027-049 (same layout, offset 24).
_MALE_BANDS = {
    "0-17": range(3, 7),
    "18-44": range(7, 15),
    "45-64": range(15, 20),
    "65+": range(20, 26),
}
_FEMALE_OFFSET = 24
_SEX_TOTAL = {"male": 2, "female": 26}

# B03002: Hispanic or Latino origin by race. 008 (some other race) and 009 (two or
# more races) are folded into one non-Hispanic bucket to keep the seed small.
_RACE_ETHNICITY = {
    "Hispanic or Latino": [12],
    "Non-Hispanic White": [3],
    "Non-Hispanic Black or African American": [4],
    "Non-Hispanic American Indian or Alaska Native": [5],
    "Non-Hispanic Asian": [6],
    "Non-Hispanic Native Hawaiian or Pacific Islander": [7],
    "Non-Hispanic other or multiple races": [8, 9],
}

COLUMNS = [
    "geo_level",
    "geo_id",
    "geo_name",
    "state_fips",
    "sex",
    "age_band",
    "race_ethnicity",
    "population",
    "margin_of_error",
    "table_id",
    "vintage",
]


def _wanted_geo(geo_id: str, county_states: set[str], zctas: set[str]) -> tuple[str, str] | None:
    """Return (level, id) when the GEO_ID is in the requested subset, else None."""
    for prefix, level in GEO_PREFIXES.items():
        if geo_id.startswith(prefix):
            code = geo_id[len(prefix) :]
            if level == "state":
                return level, code
            if level == "county" and code[:2] in county_states:
                return level, code
            if level == "zcta" and code in zctas:
                return level, code
    return None


def _iter_table(path: Path) -> Iterator[tuple[str, dict[str, str]]]:
    """Yield (GEO_ID, row) for every line of a pipe-delimited table file."""
    with path.open(encoding="utf-8", newline="") as f:
        yield from ((row["GEO_ID"], row) for row in csv.DictReader(f, delimiter="|"))


def _num(value: str) -> int | None:
    """ACS uses large negative sentinels (e.g. -555555555) for 'not available'."""
    if value == "":
        return None
    n = int(float(value))
    return None if n < 0 else n


def _combine(row: dict[str, str], table: str, nums: Iterable[int]) -> tuple[int | None, int | None]:
    """Sum estimates; margin of error is the root-sum-of-squares (Census guidance)."""
    est, moe_sq = 0, 0.0
    for n in nums:
        e, m = _num(row[f"{table}_E{n:03d}"]), _num(row[f"{table}_M{n:03d}"])
        if e is None:
            return None, None
        est += e
        moe_sq += (m or 0) ** 2
    return est, round(math.sqrt(moe_sq))


def build_rows(
    data_dir: Path,
    year: int,
    county_states: set[str],
    zctas: set[str],
) -> list[dict[str, object]]:
    """Long-form population rows for all states plus the requested counties/ZCTAs."""
    geos = data_dir / f"Geos{year}5YR.txt"
    vintage = f"ACS {year - 4}-{year} 5-year"
    names: dict[str, str] = {}
    with geos.open(encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f, delimiter="|"):
            if _wanted_geo(r["GEO_ID"], county_states, zctas):
                names[r["GEO_ID"]] = r["NAME"]

    out: list[dict[str, object]] = []

    def emit(
        geo_id: str, sex: str, age: str, race: str, table: str, est: int | None, moe: int | None
    ) -> None:
        level, code = _wanted_geo(geo_id, county_states, zctas) or ("", "")
        out.append(
            {
                "geo_level": level,
                "geo_id": code,
                "geo_name": names.get(geo_id, ""),
                "state_fips": code[:2] if level != "zcta" else "",
                "sex": sex,
                "age_band": age,
                "race_ethnicity": race,
                "population": est,
                "margin_of_error": moe,
                "table_id": table,
                "vintage": vintage,
            }
        )

    for geo_id, row in _iter_table(data_dir / f"acsdt5y{year}-b01001.dat"):
        if not _wanted_geo(geo_id, county_states, zctas):
            continue
        emit(geo_id, "all", "all", "all", "B01001", *_combine(row, "B01001", [1]))
        for sex, total_n in _SEX_TOTAL.items():
            offset = 0 if sex == "male" else _FEMALE_OFFSET
            emit(geo_id, sex, "all", "all", "B01001", *_combine(row, "B01001", [total_n]))
            for band, nums in _MALE_BANDS.items():
                emit(
                    geo_id,
                    sex,
                    band,
                    "all",
                    "B01001",
                    *_combine(row, "B01001", [n + offset for n in nums]),
                )
        for band, nums in _MALE_BANDS.items():
            both = [n + o for n in nums for o in (0, _FEMALE_OFFSET)]
            emit(geo_id, "all", band, "all", "B01001", *_combine(row, "B01001", both))

    for geo_id, row in _iter_table(data_dir / f"acsdt5y{year}-b03002.dat"):
        if not _wanted_geo(geo_id, county_states, zctas):
            continue
        for label, parts in _RACE_ETHNICITY.items():
            emit(geo_id, "all", "all", label, "B03002", *_combine(row, "B03002", parts))

    return sorted(out, key=lambda r: (str(r["geo_level"]), str(r["geo_id"]), str(r["table_id"])))


def write_csv(rows: list[dict[str, object]], out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def _split(value: str) -> set[str]:
    return {x.strip() for x in value.split(",") if x.strip()}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument(
        "--year", type=int, required=True, help="Final year of the 5-year window, e.g. 2024."
    )
    p.add_argument(
        "--data-dir", type=Path, required=True, help="Directory holding the downloaded ACS files."
    )
    p.add_argument(
        "--county-states", default="", help="Comma-separated state FIPS whose counties are kept."
    )
    p.add_argument("--zctas", default="", help="Comma-separated ZCTA5 codes to keep.")
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args(argv)
    rows = build_rows(args.data_dir, args.year, _split(args.county_states), _split(args.zctas))
    write_csv(rows, args.out)
    print(f"wrote {len(rows)} rows to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
