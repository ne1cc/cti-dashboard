"""Build the small pinned burden seeds used as condition-specific denominators.

Two sources, each reduced to a committed seed CSV (see ``docs/burden_sources.md``):

* ``places``: CDC PLACES county estimates (crude prevalence, BRFSS-based) for the
  ``DIABETES`` and ``COGNITION`` measures, pulled from the data.cdc.gov Socrata API.
* ``scp``: NCI/CDC State Cancer Profiles state-level lung and bronchus incidence
  (SEER + NPCR registries), pulled from the site's CSV export.
* ``scp-demographics``: the same report's national (US) row filtered by sex and by
  non-Hispanic race / Hispanic origin, the burden shares behind the
  participation-to-prevalence ratio (issue #75).

The parsers (``parse_places``, ``parse_scp``) are pure and tested on committed
fixtures; only ``fetch_*`` touch the network. Example::

    python -m src.ingest.burden_extracts places --states DE,MD,RI,VT \
        --out dbt_clinical_trials/seeds/burden_places_county.csv
    python -m src.ingest.burden_extracts scp \
        --out dbt_clinical_trials/seeds/burden_scp_lung_state.csv
    python -m src.ingest.burden_extracts scp-demographics \
        --out dbt_clinical_trials/seeds/burden_demographic_share.csv
"""

import argparse
import csv
import re
from pathlib import Path
from typing import Any

import requests

PLACES_RELEASE = "PLACES 2025 release"
PLACES_DATASET_ID = "swc5-untb"
PLACES_URL = f"https://data.cdc.gov/resource/{PLACES_DATASET_ID}.json"
PLACES_MEASURES = ("DIABETES", "COGNITION")

SCP_PERIOD = "2019-2023"
SCP_URL = (
    "https://statecancerprofiles.cancer.gov/incidencerates/index.php?"
    "stateFIPS=00&areatype=state&cancer=047&race=00&sex=0&age=001&stage=999&year=0"
    "&type=incd&sortVariableName=rate&sortOrder=default&output=1"
)

# Burden-share groups (dimension, label, SCP race codes, SCP sex code). The SCP race
# options are mutually exclusive (non-Hispanic races, or Hispanic of any race), so
# "Not Hispanic or Latino" is the sum of the four non-Hispanic race reports.
SCP_DEMOGRAPHIC_GROUPS: tuple[tuple[str, str, tuple[str, ...], str], ...] = (
    ("sex", "Female", ("00",), "2"),
    ("sex", "Male", ("00",), "1"),
    ("ethnicity", "Hispanic or Latino", ("05",), "0"),
    ("ethnicity", "Not Hispanic or Latino", ("07", "28", "38", "48"), "0"),
)
SCP_DEMOGRAPHIC_COLUMNS = [
    "burden_source_id",
    "dimension",
    "group_label",
    "annual_cases",
    "scp_race_codes",
    "scp_sex_code",
    "period",
    "geography",
]

PLACES_COLUMNS = [
    "state_code",
    "county_fips",
    "county_name",
    "measure_id",
    "measure",
    "prevalence_pct",
    "ci_low_pct",
    "ci_high_pct",
    "adult_population",
    "data_year",
    "release",
    "dataset_id",
]
SCP_COLUMNS = [
    "state_fips",
    "state_name",
    "annual_cases",
    "age_adjusted_rate_per_100k",
    "ci_low",
    "ci_high",
    "registry_program",
    "period",
    "site",
]

_TIMEOUT = 60


def parse_places(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep crude-prevalence county rows for the supported measures."""
    rows = []
    for r in records:
        if r["measureid"] not in PLACES_MEASURES or r["datavaluetypeid"] != "CrdPrv":
            continue
        rows.append(
            {
                "state_code": r["stateabbr"],
                "county_fips": r["locationid"],
                "county_name": r["locationname"],
                "measure_id": r["measureid"],
                "measure": r["measure"],
                "prevalence_pct": r["data_value"],
                "ci_low_pct": r["low_confidence_limit"],
                "ci_high_pct": r["high_confidence_limit"],
                "adult_population": r["totalpop18plus"],
                "data_year": r["year"],
                "release": PLACES_RELEASE,
                "dataset_id": PLACES_DATASET_ID,
            }
        )
    return sorted(rows, key=lambda x: (x["state_code"], x["county_fips"], x["measure_id"]))


def fetch_places(states: list[str]) -> list[dict[str, Any]]:
    quoted = ",".join(f"'{s}'" for s in states)
    resp = requests.get(
        PLACES_URL,
        params={
            "$where": (
                "measureid in(" + ",".join(f"'{m}'" for m in PLACES_MEASURES) + ") "
                f"and datavaluetypeid='CrdPrv' and stateabbr in({quoted})"
            ),
            "$limit": "50000",
        },
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    return parse_places(resp.json())


_FOOTNOTE = re.compile(r"\(\d+\)\s*$")
# Footnotes in the CSV's own notes: (1) SEER+NPCR, (2) NPCR, (7) SEER.
_PROGRAM = {"1": "SEER+NPCR", "2": "NPCR", "7": "SEER"}


def _clean(value: str) -> str:
    return value.strip()


def parse_scp(text: str) -> list[dict[str, Any]]:
    """Parse the State Cancer Profiles incidence export (US and footer rows dropped).

    Suppressed values appear as non-numeric text and stay empty rather than being guessed.
    """
    lines = text.splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith("State,FIPS"))
    rows = []
    for rec in csv.reader(lines[start + 1 :]):
        if len(rec) < 9 or not re.fullmatch(r"\d{5}", rec[1].strip()):
            break  # notes section begins after the data rows
        fips = rec[1].strip()[:2]
        if fips == "00":
            continue  # US aggregate row
        footnote = re.search(r"\((\d+)\)\s*$", rec[0])

        def num(v: str) -> str:
            v = _clean(v)
            return v if re.fullmatch(r"\d+(\.\d+)?", v) else ""

        rows.append(
            {
                "state_fips": fips,
                "state_name": _FOOTNOTE.sub("", rec[0]).strip(),
                "annual_cases": num(rec[8]),
                "age_adjusted_rate_per_100k": num(rec[2]),
                "ci_low": num(rec[3]),
                "ci_high": num(rec[4]),
                "registry_program": _PROGRAM.get(footnote.group(1), "") if footnote else "",
                "period": SCP_PERIOD,
                "site": "Lung & Bronchus, all stages",
            }
        )
    return sorted(rows, key=lambda x: x["state_fips"])


def fetch_scp() -> list[dict[str, Any]]:
    resp = requests.get(SCP_URL, timeout=_TIMEOUT)
    resp.raise_for_status()
    return parse_scp(resp.content.decode("latin-1"))


def parse_scp_us_count(text: str) -> int:
    """Average annual case count from the US aggregate row of an SCP incidence export.

    Raises ValueError when the row is missing or its count is suppressed, so a
    burden share is never built from a partial total.
    """
    for rec in csv.reader(text.splitlines()):
        if len(rec) >= 9 and rec[1].strip() == "00000":
            count = _clean(rec[8])
            if re.fullmatch(r"\d+", count):
                return int(count)
            raise ValueError(f"US row count is not numeric: {count!r}")
    raise ValueError("no US aggregate row in the SCP export")


def _scp_url(race: str, sex: str) -> str:
    return SCP_URL.replace("race=00", f"race={race}").replace("sex=0", f"sex={sex}")


def fetch_scp_demographics() -> list[dict[str, Any]]:
    rows = []
    for dimension, label, races, sex in SCP_DEMOGRAPHIC_GROUPS:
        total = 0
        for race in races:
            resp = requests.get(_scp_url(race, sex), timeout=_TIMEOUT)
            resp.raise_for_status()
            total += parse_scp_us_count(resp.content.decode("latin-1"))
        rows.append(
            {
                "burden_source_id": "scp_lung_state",
                "dimension": dimension,
                "group_label": label,
                "annual_cases": total,
                "scp_race_codes": "+".join(races),
                "scp_sex_code": sex,
                "period": SCP_PERIOD,
                "geography": "US (SEER+NPCR)",
            }
        )
    return rows


def write_csv(rows: list[dict[str, Any]], columns: list[str], out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=columns, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = p.add_subparsers(dest="source", required=True)
    places = sub.add_parser("places")
    places.add_argument("--states", required=True, help="Comma-separated postal codes to keep.")
    places.add_argument("--out", type=Path, required=True)
    scp = sub.add_parser("scp")
    scp.add_argument("--out", type=Path, required=True)
    scp_demo = sub.add_parser("scp-demographics")
    scp_demo.add_argument("--out", type=Path, required=True)
    args = p.parse_args(argv)
    if args.source == "places":
        rows, cols = (
            fetch_places([s.strip() for s in args.states.split(",") if s.strip()]),
            PLACES_COLUMNS,
        )
    elif args.source == "scp-demographics":
        rows, cols = fetch_scp_demographics(), SCP_DEMOGRAPHIC_COLUMNS
    else:
        rows, cols = fetch_scp(), SCP_COLUMNS
    write_csv(rows, cols, args.out)
    print(f"wrote {len(rows)} rows to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
