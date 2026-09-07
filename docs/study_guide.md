# Study Guide — How to Understand This Project

A self-paced path from "it runs" to "I could rebuild and defend every piece of it."
Work top to bottom; each level assumes the previous one. Commands are copy-pasteable
from the repository root.

---

## Level 0 — The 15-minute mental model

Read these, in order, before touching code:

1. `README.md` sections 1–7 (what and why)
2. `PROJECT_DOCUMENTATION.md` §3 (architecture diagram) and §5 (layer grains)
3. `docs/clinical_interpretation_guardrails.md` (the language rules that shape everything)

The whole system in one sentence: **download public registry pages verbatim (bronze),
flatten them into typed tables (silver), model them into tested analytics tables
(gold), and rank market segments for human feasibility review (dashboard).**

Three ideas explain most design decisions:

| Idea | Consequence in this repo |
|---|---|
| Raw data is immutable | bronze is append-only; everything downstream is rebuildable |
| The API has no history | history = our own retained weekly snapshots (`run_id`) |
| Never overstate | proxy flags, interpretation notes, empty contacts model, guarded captions |

## Level 1 — Run it and poke it

```bash
make setup && make pipeline     # full build (or reuse the existing data/)
make dashboard                  # browse all 8 pages, read every caption
```

Then open the warehouse directly — this is the single most instructive exercise:

```bash
uv run python -c "
import duckdb
c = duckdb.connect('data/warehouse/clinical_trials.duckdb', read_only=True)
print(c.execute('select table_schema, table_name from information_schema.tables order by 1,2').df().to_string())
"
```

Questions to answer yourself (all answerable with SQL against `main_marts`):

- How many trials are recruiting in your state, and in which phases?
- Which facility appears on the most recruiting trials?
- What are the top 5 segments in the priority queue, and what does each
  `priority_explanation` say?

## Level 2 — Trace one trial through every layer

Pick any NCT ID from the Trial Explorer page (e.g. the newest one). Then follow it:

```bash
NCT=NCT07721467   # substitute your pick

# 1. Bronze: the raw JSON as the registry served it
grep -rl "$NCT" data/bronze/*/api_responses/
# open that file and find the study record (the directory level is the profile)

# 2. Silver: the flattened rows
uv run python -c "
import pandas as pd, glob
f = sorted(glob.glob('data/silver/trials/run_id=*.parquet'))[-1]
df = pd.read_parquet(f)
print(df[df.nct_id=='$NCT'].T)
"

# 3. Gold: the modeled record
uv run python -c "
import duckdb
c = duckdb.connect('data/warehouse/clinical_trials.duckdb', read_only=True)
print(c.execute(\"select * from main_marts.dim_trial where nct_id='$NCT'\").df().T)
print(c.execute(\"select facility_name, city, state_normalized from main_marts.fct_trial_site where nct_id='$NCT'\").df())
"
```

You have now seen the entire medallion pattern with your own data. Note what changed
at each hop: nothing (bronze), shape (silver), meaning (gold).

## Level 3 — Read the code in this order

The order matters; each file is more understandable after the previous one.

| # | File | What to learn from it |
|---|---|---|
| 1 | `config/project_config.yml` | every knob the pipeline has; nothing is hidden in code |
| 2 | `src/config.py` | typed config loading, `.env` overrides, `lru_cache` singleton |
| 3 | `src/ingest/ctg_client.py` + `retry_policy.py` | polite HTTP: timeouts, backoff, retry-able status codes |
| 4 | `src/ingest/pagination.py` + `extract_studies.py` | pageToken loop, run_id minting, incremental reuse, manifest writing |
| 5 | `src/ingest/snapshot_manifest.py` | what makes a run auditable (hashes, counts, status) |
| 6 | `src/transform/flatten_studies.py` | one JSON study → seven entity row-sets; where every silver column is born |
| 7 | `src/transform/normalize_locations.py` + `normalize_conditions.py` | best-effort normalization and why it's labeled that way |
| 8 | `dbt_clinical_trials/models/staging/stg_trials.sql` + `_sources.yml` | how dbt reads Parquet via `read_parquet`, staging conventions |
| 9 | `models/intermediate/int_current_trial_status.sql` + `int_trial_status_history.sql` | snapshot logic: latest record per trial, history construction |
| 10 | `models/marts/dim_trial.sql`, `fct_trial_snapshot.sql`, `fct_trial_site.sql` | dimensional modeling: surrogate keys, grains, current-record flag |
| 11 | `models/marts/mart_feasibility_priority_queue.sql` | the centerpiece — read every CTE; map each to a score component in §10 of `PROJECT_DOCUMENTATION.md` |
| 12 | `models/marts/_marts.yml` + `tests/*.sql` | how invariants become executable tests |
| 13 | `src/quality/reconciliation.py` + `schema_drift.py` | cross-layer trust: counts must agree, schema changes must be noticed |
| 14 | `dashboard/components/data.py` + `guardrails.py` | cached read-only access; mechanically enforced disclaimers |
| 15 | `tests/test_metrics.py` | the three-way config sync test and score edge cases |

Companion while reading: `docs/data_dictionary.md` (column meanings) and
`docs/metric_definitions.md` (formulas).

## Level 4 — Break it, then fix it (exercises)

Doing these will teach more than any reading. Each is safe and reversible.

1. **Grain violation.** In a scratch SQL session, try inserting a duplicate
   `nct_id` into a copy of `dim_trial`, then run `make dbt-test` mentally against
   it — find which test would catch it (`_marts.yml`).
2. **Weight drift.** Change one weight in `config/score_weights.yml` *without*
   touching the seed CSV. Run `make test`. Find the failing test and understand
   why the project refuses to let the three weight locations diverge. Revert.
3. **New metric.** Add `enrollment_count_median` per segment to
   `mart_trial_activity.sql`, add a `not_null` test, run
   `make dbt-run dbt-test`. (Median is one line in DuckDB: `median(...)`.)
4. **New therapeutic area.** Run
   `make ingest CONDITION="Parkinson Disease"` and follow the run through
   transform. What breaks in the condition taxonomy, and why is that expected?
5. **Second snapshot.** Run `make full-refresh` a day later, rebuild, and watch
   `growth_uses_registry_proxy_flag` and `has_multi_snapshot_history` change.
   This is the single best demonstration of the snapshot-history design.
6. **Dashboard change.** Add a phase filter to the Sponsor Landscape page using
   `components/filters.py` as a pattern. Run `make test` — the smoke test will
   exercise your page.

## Level 5 — Explain it out loud

You understand the project when you can answer these without notes
(interview-calibrated; talking points also in `README.md` §25):

1. Why keep raw JSON forever instead of just the parsed tables?
2. The API only returns current records — so where does "status history" come from,
   and what is honestly impossible on day one?
3. Why is `nct_id` a reliable key but `facility_name` is not, and how does the
   schema encode that difference?
4. Walk through the feasibility score: components, normalization, weights, bands.
   Why is a *ranking* more defensible than a *prediction* here?
5. What stops this project from claiming "site X is failing to recruit"? Name the
   mechanical controls, not just the intent.
6. Why `error` vs `warn` severities in dbt tests — what class of problem is each for?
7. What would change to move this to BigQuery + Airflow? What wouldn't change?

## External resources (matched to this codebase)

| Topic | Resource | Why it maps here |
|---|---|---|
| The source | [ClinicalTrials.gov API v2 docs](https://clinicaltrials.gov/data-api/api) | the exact endpoint and parameters used in `project_config.yml` |
| Registry background | [ClinicalTrials.gov glossary](https://clinicaltrials.gov/study-basics/glossary) | what RECRUITING, phases, enrollment types actually mean |
| dbt | [dbt Developer Hub — quickstarts & best practices](https://docs.getdbt.com/) | staging/intermediate/marts convention this repo follows |
| Dimensional modeling | Kimball & Ross, *The Data Warehouse Toolkit* (ch. 1–3) | dims, facts, bridges, surrogate keys, grain discipline |
| DuckDB | [DuckDB documentation](https://duckdb.org/docs/) | `read_parquet`, QUALIFY, window frames, concurrency model (why the segfault rule exists) |
| Medallion pattern | search: "medallion architecture bronze silver gold" | the layering rationale, vendor-neutral |
| Streamlit | [Streamlit docs](https://docs.streamlit.io/) — caching + `st.column_config` | `cache_resource`/`cache_data` and the LinkColumn used in Trial Explorer |
| Python packaging | [uv documentation](https://docs.astral.sh/uv/) | how the environment and groups are managed |
| HHI | any econ reference on Herfindahl–Hirschman Index | the sponsor-concentration component |

## Where each question is answered

| "I want to understand…" | Read |
|---|---|
| the whole system at once | `PROJECT_DOCUMENTATION.md` |
| a specific column | `docs/data_dictionary.md` |
| a specific metric formula | `docs/metric_definitions.md` |
| why a number can't be trusted further | `docs/assumptions_and_limitations.md` |
| what the tests guarantee | `docs/data_quality_framework.md` |
| what we refuse to claim | `docs/clinical_interpretation_guardrails.md` |
| how it was actually built, mistakes included | `docs/development_log.md` |
| how to present it to a stakeholder | `docs/executive_memo_template.md` |

---

*Everything here inherits the project's interpretation rule: outputs are
public-registry planning signals, not recruitment forecasts.*
