"""Offline parser tests for the burden seeds (Issue #71) plus seed-level sanity checks."""

import csv
from pathlib import Path

import pytest

from src.ingest.burden_extracts import parse_places_county, parse_seer_state_profile

SEEDS = Path(__file__).resolve().parents[1] / "dbt_clinical_trials" / "seeds"

# Verbatim layout of a State Cancer Profiles export (shortened header, three data rows).
SEER_SNIPPET = """Incidence Rate Report by State

"Lung & Bronchus (All Stages^), 2019-2023"

State,FIPS,Rate,Low,High,Rank,RankLow,RankHigh,Average Annual Count,Recent Trend
"US (SEER+NPCR)(1)",00000,51.8 ,51.8, 51.9,N/A , N/A , N/A,225094,falling
"Kentucky(7)",21000,83.6 ,82.5, 84.7,1 , 1 , 1,5032,falling
"District of Columbia(2)",11001,46.1 ,43.8, 48.4,39 , 36 , 44,314,falling

"(1) Source: footnote that must not be parsed as data"
"""

PLACES_SNIPPET = """year,stateabbr,locationname,locationid,measureid,measure,data_value,\
low_confidence_limit,high_confidence_limit,totalpop18plus
2023,MA,Suffolk,25025,DIABETES,Diagnosed diabetes among adults,10.1,9.0,11.2,600000
2023,TX,Loving,48301,DIABETES,Diagnosed diabetes among adults,,,,30
2023,MA,Barnstable,25001,DIABETES,Diagnosed diabetes among adults,,,,90
"""


def test_seer_parser_drops_national_row_and_footnotes() -> None:
    rows = parse_seer_state_profile(SEER_SNIPPET)
    assert [r["state_name"] for r in rows] == ["Kentucky", "District of Columbia"]
    ky = rows[0]
    assert ky["fips_code"] == "21"  # state FIPS, not the file's 5-digit state+county code
    assert (ky["cancer_site"], ky["period"]) == ("Lung & Bronchus", "2019-2023")
    assert (ky["age_adjusted_rate_per_100k"], ky["average_annual_count"]) == ("83.6", "5032")


def test_seer_parser_fails_loudly_on_layout_change() -> None:
    with pytest.raises(ValueError, match="unexpected State Cancer Profiles layout"):
        parse_seer_state_profile("<html>moved</html>")


def test_places_parser_filters_states_and_keeps_suppressed_rows_blank() -> None:
    rows = parse_places_county(PLACES_SNIPPET, {"MA"})
    assert [r["county_fips"] for r in rows] == ["25001", "25025"]
    assert rows[0]["prevalence_pct"] == ""  # suppressed by CDC, never imputed
    assert rows[1]["prevalence_pct"] == "10.1"


def test_committed_burden_seeds_are_complete_and_pinned() -> None:
    with (SEEDS / "burden_seer_lung_state.csv").open(encoding="utf-8") as fh:
        seer = list(csv.DictReader(fh))
    assert len(seer) == 52  # 50 states + DC + PR
    assert {r["period"] for r in seer} == {"2019-2023"}
    with (SEEDS / "burden_places_county.csv").open(encoding="utf-8") as fh:
        places = list(csv.DictReader(fh))
    assert {r["measure_id"] for r in places} == {"DIABETES", "COGNITION"}
    assert {r["brfss_year"] for r in places} == {"2023"}
    assert {r["state_code"] for r in places} == {"MA", "RI", "NH", "VT", "DE", "HI", "DC"}
