"""Build national descriptive evidence using existing audit rules; no app writes.

Run from the repository root after ingest/transform/dbt validation. The warehouse
is opened read-only. Reuse the recorded --snapshot-date and --evaluation-time to
reproduce a package while its source records and original rules remain available.
"""

from __future__ import annotations

import argparse
import csv
import json
import lzma
import subprocess
import sys
from pathlib import Path

import duckdb
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "dashboard"))

from components.audit import compute_audit, export_audit  # noqa: E402
from components.audit_panel import active_rules  # noqa: E402

from src.transform.normalize_conditions import load_taxonomy  # noqa: E402
from src.utils.hashing import sha256_json  # noqa: E402

SUPPLEMENT = ["DC", "PR", "GU", "VI", "AS", "MP"]
FILTERS = {"conditions": ["alzheimers_disease"], "phases": ["PHASE3"], "sponsors": []}
OUTPUT = ROOT / "docs/evidence"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-date", help="UTC date; default latest competition date")
    parser.add_argument("--evaluation-time", help="Timezone-aware timestamp; default now UTC")
    args = parser.parse_args()
    evaluation = pd.Timestamp(args.evaluation_time or pd.Timestamp.now(tz="UTC"))
    if evaluation.tzinfo is None:
        raise ValueError("Evaluation time requires a timezone")
    evaluation = evaluation.tz_convert("UTC")
    geography = yaml.safe_load((ROOT / "config/geography_rules.yml").read_text())
    states = sorted(set(geography["valid_state_abbreviations"]) - set(SUPPLEMENT))
    assert len(states) == 50
    names = {}
    for name, abbreviation in geography["state_name_to_abbreviation"].items():
        names.setdefault(abbreviation, name.title())
    names.update(DC="District of Columbia", VI="U.S. Virgin Islands")
    con = duckdb.connect(str(ROOT / "data/warehouse/clinical_trials.duckdb"), read_only=True)
    date = (
        args.snapshot_date
        or con.execute(
            "select max(snapshot_date) from main_marts.mart_recruiting_competition "
            "where indication_profile_id = 'adrd'"
        ).fetchone()[0]
    )
    if date is None:
        raise ValueError("No competition dataset available; do not publish blank/zero results")
    # Exactly the existing competition loader's selection, frozen once for all rows.
    observations = con.execute(
        "select indication_profile_id, ingestion_run_id as snapshot_id, nct_id, snapshot_date "
        "from main_intermediate.int_trial_status_history "
        "where indication_profile_id = 'adrd' and snapshot_date = ?",
        [date],
    ).df()
    if observations.empty:
        raise ValueError("No selected observations; results unavailable")
    con.register("selected_observations", observations)
    studies = con.execute(
        "select a.* from main_marts.mart_study_snapshot_audit a "
        "join selected_observations o using(indication_profile_id, snapshot_id, nct_id)"
    ).df()
    locations = con.execute(
        "select l.* from main_marts.mart_location_snapshot_audit l "
        "join selected_observations o using(indication_profile_id, snapshot_id, nct_id)"
    ).df()
    conditions = con.execute(
        "select indication_profile_id, ingestion_run_id as snapshot_id, nct_id, condition_group "
        "from main_intermediate.int_trial_condition_mapping where indication_profile_id='adrd'"
    ).df()
    assert len(studies) == len(observations), "Every selected observation needs an audit row"
    # Independent SQL starts at staging, not at helper decisions or exported counts.
    cohort = con.execute(
        "select t.nct_id, t.snapshot_id, t.overall_status, "
        "coalesce(t.overall_status = 'RECRUITING', false) as recruiting, "
        "coalesce(date_diff('day', try_cast(t.last_update_post_date_raw as date), "
        "o.snapshot_date) > 180, false) as older "
        "from main_staging.stg_trials t join selected_observations o "
        "using(indication_profile_id, snapshot_id, nct_id) "
        "where t.phase_normalized='PHASE3' and exists (select 1 "
        "from main_staging.stg_trial_conditions c where c.nct_id=t.nct_id "
        "and c.ingestion_run_id=t.ingestion_run_id "
        "and c.indication_profile_id=t.indication_profile_id "
        "and c.condition_group='alzheimers_disease')"
    ).df()
    con.register("selected_cohort", cohort)
    recruiting_denominator = int(cohort.recruiting.sum())
    rules = active_rules("adrd")
    audits, rows = {}, []
    for code, regions in [("national_50_states", states), *[(s, [s]) for s in states + SUPPLEMENT]]:
        audit = compute_audit(
            studies,
            locations,
            conditions,
            profile_id="adrd",
            observations=observations,
            filters={**FILTERS, "states": regions},
            metric="competition",
            count_status="recruiting",
            evaluation_time=evaluation,
            rule_config=rules,
        )
        expected = con.execute(
            "select c.*, count(l.location_ordinal) as recorded_locations, "
            "count(*) filter(where l.usable_geography_flag) as usable_locations, "
            "count(*) filter(where l.usable_geography_flag and "
            "l.state_normalized in (select unnest(?))) as matching_locations, "
            "count(*) filter(where l.location_ordinal is not null and "
            "(coalesce(trim(l.country),'')='' or (l.us_location_flag and "
            "not coalesce(l.usable_geography_flag,false)))) as unresolved_locations, "
            "count(*) filter(where l.location_ordinal is not null and "
            "(coalesce(trim(l.country),'')='' or (l.us_location_flag and "
            "coalesce(trim(l.state_raw),'')=''))) as missing_locations "
            "from selected_cohort c left join main_staging.stg_trial_locations l "
            "on l.nct_id=c.nct_id and l.snapshot_id=c.snapshot_id "
            "and l.indication_profile_id='adrd' group by all",
            [regions],
        ).df()
        contributor_mask = expected.recruiting & (expected.matching_locations > 0)
        contributors = sorted(expected.loc[contributor_mask, "nct_id"])
        undetermined = (expected.matching_locations == 0) & (
            (expected.recorded_locations == 0) | (expected.unresolved_locations > 0)
        )
        reason_counts = {}
        for record in expected.itertuples():
            reason = None
            if not record.recruiting:
                reason = "not_confirmed_recruiting"
            elif record.usable_locations == 0:
                reason = (
                    "missing_geography"
                    if record.recorded_locations == 0 or record.missing_locations > 0
                    else "unsupported_geography"
                )
            elif record.matching_locations == 0:
                reason = (
                    "no_confirmed_selected_region_match"
                    if (record.unresolved_locations > 0)
                    else "outside_selected_region"
                )
            if reason:
                reason_counts[reason] = reason_counts.get(reason, 0) + 1
        summary = audit["summary"]
        assert audit["contributors"] == contributors, code
        assert summary["eligible_studies"] == len(cohort), code
        assert summary["recruiting_eligible_studies"] == recruiting_denominator, code
        assert summary["region_undetermined_studies"] == int(undetermined.sum()), code
        assert summary["exclusions"] == reason_counts, code
        assert len(contributors) + sum(reason_counts.values()) == len(cohort), code
        share = len(contributors) / recruiting_denominator if recruiting_denominator else None
        assert summary["recruiting_coverage"] == share, code
        older = int(expected.loc[contributor_mask, "older"].sum())
        decisions = audit["studies"]
        assert older == int(
            decisions.loc[decisions.included_flag, "older_posted_update_flag"].sum()
        )
        unresolved_recruiting = int((undetermined & expected.recruiting).sum())
        assert unresolved_recruiting == int(
            (
                decisions.confirmed_recruiting_flag & decisions.region_membership.eq("undetermined")
            ).sum()
        )
        exported = json.loads(export_audit(audit))
        if audits:
            # Geography filters change decisions, not the shared original locations.
            # Reuse an equal array in memory while keeping each serialized audit intact.
            shared_locations = audits["national_50_states"]["locations"]
            assert exported["locations"] == shared_locations
            exported["locations"] = shared_locations
        audits[code] = exported
        rows.append(
            {
                "jurisdiction": code,
                "name": names.get(code, "50-state distinct union"),
                "jurisdiction_type": "national"
                if len(regions) == 50
                else "additional_jurisdiction"
                if code in SUPPLEMENT
                else "state",
                "contributing_studies": len(contributors),
                "eligible_recruiting_cohort": recruiting_denominator,
                "cohort_representation_pct": None if share is None else round(100 * share, 2),
                "undetermined_recruiting_studies": unresolved_recruiting,
                "older_posted_update_contributors": older,
            }
        )
    assert set(audits["national_50_states"]["contributors"]) == set().union(
        *(set(audits[state]["contributors"]) for state in states)
    )
    national = audits["national_50_states"]
    members = pd.DataFrame(
        national["studies"], columns=list(dict.fromkeys([*studies.columns, "included_flag"]))
    )
    national_ids = set(national["contributors"])
    region_counts = {
        nct: sum(nct in audits[state]["contributors"] for state in states) for nct in national_ids
    }
    sample_categories = {
        "national_contributor": members[members.nct_id.isin(national_ids)],
        "multi_state_contributor": members[
            members.nct_id.isin([nct for nct, count in region_counts.items() if count > 1])
        ],
        "recruiting_without_50_state_match": members[
            members.confirmed_recruiting_flag & ~members.included_flag
        ],
        "non_recruiting_exclusion": members[~members.confirmed_recruiting_flag],
        "missing_geography": members[members.geography_category.eq("missing")],
        "unsupported_geography": members[members.geography_category.eq("unsupported")],
        "missing_facility": members[members.missing_facility_location_count > 0],
        "older_posted_update": members[members.older_posted_update_flag],
        "month_precision_verification": members[members.verification_date_precision.eq("month")],
    }
    taxonomy = load_taxonomy(ROOT / "config/condition_taxonomy.yml")
    sample, absent = [], []
    for category, candidates in sample_categories.items():
        if candidates.empty:
            absent.append(category)
            continue
        record = candidates.sort_values("nct_id").iloc[0].to_dict()
        raw = json.loads(
            (ROOT / "data/bronze/adrd/api_responses" / record["raw_page_reference"]).read_text()
        )["studies"][record["raw_study_ordinal"]]
        protocol = raw["protocolSection"]
        status = protocol["statusModule"]
        raw_locations = protocol.get("contactsLocationsModule", {}).get("locations", [])
        assert protocol["identificationModule"]["nctId"] == record["nct_id"]
        assert sha256_json(raw) == record["source_json_hash"]
        assert status["overallStatus"] == record["overall_status"]
        assert protocol["designModule"]["phases"] == ["PHASE3"]
        assert any(
            taxonomy.map_condition(condition).condition_group == "alzheimers_disease"
            for condition in protocol.get("conditionsModule", {}).get("conditions", [])
        )
        assert (
            status.get("lastUpdatePostDateStruct", {}).get("date")
            == record["last_update_post_date_raw"]
        )
        assert status.get("statusVerifiedDate") == record["status_verified_date_raw"]
        assert len(raw_locations) == record["recorded_location_count"]
        for location in national["locations"]:
            if (
                location["nct_id"] == record["nct_id"]
                and location["snapshot_id"] == record["snapshot_id"]
            ):
                original = raw_locations[location["location_ordinal"]]
                for source, target in [
                    ("facility", "facility_name"),
                    ("city", "city"),
                    ("state", "state_raw"),
                    ("country", "country"),
                    ("status", "location_status"),
                ]:
                    assert original.get(source) == location[target]
        sample.append(
            {
                "category": category,
                "nct_id": record["nct_id"],
                "raw_page_reference": record["raw_page_reference"],
                "raw_study_ordinal": record["raw_study_ordinal"],
                "source_json_hash": record["source_json_hash"],
                "result": "passed",
            }
        )
    manifests = [
        json.loads((ROOT / f"data/bronze/adrd/manifests/manifest_{sid}.json").read_text())
        for sid in national["metadata"]["snapshot_ids"]
    ]
    assert all(manifest["status"] == "success" for manifest in manifests)
    package = {
        "package_version": "national-descriptive-case-study-v1",
        "evaluation_time": evaluation.isoformat(),
        "snapshot_date": str(date)[:10],
        "source_code_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "dataset_selection": "Existing competition observation selection, frozen once; "
        "latest daily study observations on the selected complete competition-mart date.",
        "state_codes": states,
        "additional_jurisdiction_codes": SUPPLEMENT,
        "national": national,
        "states": {s: audits[s] for s in states},
        "additional_jurisdictions": {s: audits[s] for s in SUPPLEMENT},
        "results": rows,
        "source_manifests": manifests,
        "verification": {
            "numerically_reconciled_audits": len(audits),
            "raw_record_sample": sample,
            "unobserved_sample_categories": absent,
            "sampling_policy": "Alphabetically first NCT ID per available category, "
            "within the condition/phase cohort. Categories may reuse a record. "
            "Checks identity, content hash, status, exact phase, condition mapping, "
            "source dates, location count "
            "and original facility/city/state/country/status fields. "
            "Sampling is not full source validation.",
        },
    }
    OUTPUT.mkdir(exist_ok=True)
    with lzma.open(
        OUTPUT / "feasibility_case_study_audit.jsonl.xz",
        "wt",
        encoding="utf-8",
        filters=[{"id": lzma.FILTER_LZMA2, "dict_size": 64 * 1024 * 1024, "preset": 0}],
    ) as output:
        manifest = {
            key: value
            for key, value in package.items()
            if key not in {"national", "states", "additional_jurisdictions"}
        }
        output.write(
            json.dumps({"record_type": "manifest", **manifest}, allow_nan=False, sort_keys=True)
        )
        output.write("\n")
        for code, exported in audits.items():
            output.write(
                json.dumps(
                    {"record_type": "audit", "jurisdiction": code, "audit": exported},
                    allow_nan=False,
                    sort_keys=True,
                )
            )
            output.write("\n")
    with (OUTPUT / "feasibility_case_study_results.csv").open("w", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    print(
        json.dumps(
            {
                "national": rows[0],
                "snapshot_date": str(date)[:10],
                "evaluation_time": evaluation.isoformat(),
                "audits_reconciled": len(audits),
                "sample_checks": len(sample),
                "unobserved_sample_categories": absent,
            },
            indent=2,
        )
    )
    con.close()


if __name__ == "__main__":
    main()
