# Competition Audit and Drill-Through

## Overview

Open **Data coverage and caveats** on any competition-bearing page to inspect the
selected metric's definition, exact filters, profile/run IDs, contributing NCT IDs,
exclusive exclusions, overlapping warnings and original recorded locations. Sidebar
scope and a selected segment/sponsor/facility refinement are recorded separately.
Distinct-study totals deduplicate across overlapping segments. Geography uses reported
U.S. country/state; unresolved membership stays in the eligible denominator without
claiming it belongs to a selected state. Location status does not gate confirmed
overall `RECRUITING` counts. Missing facility text does not erase valid state geography.

The Overview's **Listed facilities** card counts best-effort normalized
name + city + state identities in the selected profile's latest captured snapshot.
Earlier retained snapshots do not add to the count; a latest capture with no
attributable facilities yields zero. **States with listed sites** spans distinct
states/territories across retained usable locations, while trial cards use each
trial's latest-observed record. The combined location audit retains historical
study observations and summarizes distinct studies. See
[metric definitions](metric_definitions.md) for grains and identity limits.

## Timestamps, Age Warnings, and Audit JSON

The panel separates pipeline age, posted-update day age and verification month age,
with raw dates and precision retained. Its configurable project-defined warning
(default >180 days) retains records. Enrollment categories distinguish estimated targets, reported actual,
missing counts and unknown types; study totals are never apportioned to sites.

Download the JSON audit for selected run IDs/dates, `competition-audit-v2`, active
rule hashes, evaluation UTC, threshold, decisions, contributors, enrollment and raw
page/ordinal/hash lineage, qualifying growth/proxy events and predecessor evidence.
The audit retains wider eligible/count-input denominators; unrelated derived formulas,
windows and warehouse values are disclosed rather than recalculated from the union.
See [metric definitions](metric_definitions.md) and
[data dictionary](data_dictionary.md) for exact grains, formulas and identity limits.

## Rebuilding and Provenance

Existing silver must be rebuilt from retained profile bronze, followed by dbt rebuild,
to expose new provenance columns. For each transformable profile, run:

```bash
uv run python -m src.cli transform --profile <id> --force
make dbt-run dbt-test
```

No registry refresh is needed for this rebuild. Historical page receipt metadata remains unknown.
Bronze pruning can remove referenced pages, and active hashes do not establish historical configuration identity.

## Important Limitations

Registry-derived signals support preliminary feasibility review. They do not measure site-level recruitment performance or establish scientific validity. Counts reflect captured public records and the displayed inclusion rules.
