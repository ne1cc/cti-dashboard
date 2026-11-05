"""Burden-source extracts for the condition denominators (Issue #71).

Intent: reproduce, from the public downloads, the two small seeds that carry
disease-burden estimates: `burden_seer_lung_state` (SEER*Explorer-backed State
Cancer Profiles, lung and bronchus incidence by state) and `burden_places_county`
(CDC PLACES county estimates for selected measures). Parsers take text, so tests
run on inline snippets with no network.

Limits:
- SEER State Cancer Profiles has no NSCLC-only cut by state; the extract is all
  lung and bronchus histologies. The proxy is documented in docs/burden_sources.md.
- shortcut: PLACES is filtered to an explicit `--states` subset to keep the seed
  small; drop the filter (about 3,000 counties per measure) when a consumer needs
  every state.
"""

from __future__ import annotations

import argparse
import csv
import io
import re
from pathlib import Path

import requests

SEER_URL = (
    "https://statecancerprofiles.cancer.gov/incidencerates/index.php?stateFIPS=00"
    "&areatype=state&cancer=047&race=00&sex=0&age=001&stage=999&year=0&type=incd"
    "&sortVariableName=rate&sortOrder=default&output=1"
)
PLACES_URL = "https://data.cdc.gov/resource/swc5-untb.csv"
PLACES_MEASURES = ("DIABETES", "COGNITION")

SEER_COLUMNS = [
    "fips_code",
    "state_name",
    "cancer_site",
    "period",
    "age_adjusted_rate_per_100k",
    "rate_ci_low",
    "rate_ci_high",
    "average_annual_count",
]
PLACES_COLUMNS = [
    "county_fips",
    "state_code",
    "county_name",
    "measure_id",
    "measure_name",
    "prevalence_pct",
    "ci_low",
    "ci_high",
    "population_18plus",
    "brfss_year",
]


def parse_seer_state_profile(text: str) -> list[dict[str, str]]:
    """Parse a State Cancer Profiles incidence CSV into one row per state.

    Contract:
    - Returns rows for states, DC and PR; the national 'US (SEER+NPCR)' row is dropped.
    - fips_code is the 2-digit state FIPS (the file's 5-digit code is state + county '000').
    - Raises ValueError if the title lines or the data table are missing, so a changed
      page layout fails loudly instead of yielding an empty seed.
    """
    lines = text.splitlines()
    title = next((ln for ln in lines if "Lung" in ln or "Bronchus" in ln), "")
    match = re.search(r'"(?P<site>[^"(]+?) \(All Stages\^\), (?P<period>\d{4}-\d{4})"', title)
    start = next((i for i, ln in enumerate(lines) if ln.startswith("State,FIPS")), None)
    if match is None or start is None:
        raise ValueError("unexpected State Cancer Profiles layout: title or header not found")
    out: list[dict[str, str]] = []
    for cells in csv.reader(lines[start + 1 :]):
        if len(cells) < 9 or not cells[1].strip().isdigit() or cells[1].startswith("00000"):
            if out:
                break  # first non-data line after the table ends it (footnotes follow)
            continue
        out.append(
            {
                "fips_code": cells[1].strip()[:2],
                "state_name": re.sub(r"\(\d+\)$", "", cells[0]).strip(),
                "cancer_site": match["site"],
                "period": match["period"],
                "age_adjusted_rate_per_100k": cells[2].strip(),
                "rate_ci_low": cells[3].strip(),
                "rate_ci_high": cells[4].strip(),
                "average_annual_count": cells[8].strip(),
            }
        )
    if not out:
        raise ValueError("no state rows parsed from State Cancer Profiles CSV")
    return out


def parse_places_county(text: str, states: set[str]) -> list[dict[str, str]]:
    """Keep crude-prevalence rows for `states` from a PLACES county CSV (Socrata export).

    Contract:
    - Rows suppressed by CDC (blank data_value) are kept with a blank prevalence so
      downstream coverage counts see them; they are never imputed.
    - Output is sorted by (measure, county_fips) for a stable diff.
    """
    rows = [
        {
            "county_fips": r["locationid"],
            "state_code": r["stateabbr"],
            "county_name": r["locationname"],
            "measure_id": r["measureid"],
            "measure_name": r["measure"],
            "prevalence_pct": r["data_value"],
            "ci_low": r["low_confidence_limit"],
            "ci_high": r["high_confidence_limit"],
            "population_18plus": r["totalpop18plus"],
            "brfss_year": r["year"],
        }
        for r in csv.DictReader(io.StringIO(text))
        if r["stateabbr"] in states
    ]
    return sorted(rows, key=lambda r: (r["measure_id"], r["county_fips"]))


def _write(rows: list[dict[str, str]], columns: list[str], out: Path) -> None:
    with out.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {len(rows)} rows to {out}")


def main(argv: list[str] | None = None) -> int:
    """CLI: `seer --out FILE` or `places --states MA RI ... --out FILE`."""
    p = argparse.ArgumentParser(description="Build burden-source seed extracts.")
    sub = p.add_subparsers(dest="source", required=True)
    sub.add_parser("seer").add_argument("--out", type=Path, required=True)
    places = sub.add_parser("places")
    places.add_argument("--out", type=Path, required=True)
    places.add_argument("--states", nargs="+", required=True)
    args = p.parse_args(argv)
    if args.source == "seer":
        resp = requests.get(SEER_URL, timeout=60)
        resp.raise_for_status()
        _write(parse_seer_state_profile(resp.text), SEER_COLUMNS, args.out)
        return 0
    chunks = []
    for measure in PLACES_MEASURES:
        resp = requests.get(
            PLACES_URL,
            params={
                "$where": f"measureid='{measure}' AND datavaluetypeid='CrdPrv'",
                "$limit": "5000",
            },
            timeout=120,
        )
        resp.raise_for_status()
        chunks.append(resp.text)
    rows = [r for chunk in chunks for r in parse_places_county(chunk, set(args.states))]
    _write(rows, PLACES_COLUMNS, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
