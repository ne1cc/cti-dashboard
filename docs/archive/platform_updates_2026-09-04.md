> [!NOTE]
> Historical archive note (2026-09-04). Engineering-count inventories were omitted during archival to prevent stale count drift.

# Platform Updates — 2026-09-04

This document explains, in plain language, what was decided today for the
platform modernization and why. The full technical design lives in
`docs/superpowers/specs/2026-09-04-platform-modernization-design.md`.

---

## The one-sentence summary

Your project already has a solid pipeline; we're adding the pieces that make it
run itself on a schedule, survive failures, prove its own correctness, and show
all of it to anyone who opens the repo — which is exactly what data engineering
interviewers look for.

## What changes, before → after

| Area | Before | After |
|---|---|---|
| Running the pipeline | You run `make pipeline` by hand | A scheduler runs it every week, automatically |
| Where data lives | Only your laptop (and the Fly volume) | Also a durable cloud bucket (Google Cloud Storage) |
| If something breaks | You'd only notice when you next look | The run fails visibly, retries, and never publishes bad data |
| Proving data quality | Tests exist but don't run in CI against real dbt models | CI builds the models and runs dbt tests on every push |
| Code style safety | Ruff lint only | Ruff + type checking + pre-commit hooks (after a one-time `pre-commit install`) |
| Schema changes | A changed column could silently break dashboards | Contracts make the build fail loudly instead |
| Warehouse | DuckDB only | DuckDB + BigQuery (free tier) from the same dbt code |

## The new tools, one paragraph each

**Dagster (orchestration).** Think of it as a control room for your pipeline.
Today your pipeline is a list of steps in a Makefile — Dagster turns each step
into a tracked *asset* (bronze raw data, silver parquet, each dbt model) and
draws them as a live diagram showing what depends on what, what ran, when, and
what passed or failed. When a hiring manager asks "how does this run in
production?", Dagster is the answer. You'll run a little web app locally
(`dagster dev`) to see it.

**GitHub Actions as the scheduler.** GitHub can run jobs on a timer (cron).
Every Monday, a workflow will trigger the Dagster job headlessly — no laptop
needed. Every run is logged publicly in the repo's Actions tab, which becomes
verifiable proof that your "weekly snapshot history" claim is real.

**Google Cloud Storage (the "lake").** A cheap, durable place to put files in
the cloud (free tier is plenty — your data is ~20 MB per week). After each
successful run, the raw API responses (bronze), the parquet files (silver), and
the finished DuckDB warehouse (gold) are uploaded there. Cloud files survive
even if your laptop dies or the Fly machine is rebuilt — the lake, not the Fly
volume, becomes the master copy.

**Publish-on-green.** A simple rule with a big payoff: data is only uploaded to
the cloud *after every quality check passes*. A broken run means nothing is
published, so the dashboard keeps showing last week's good data instead of this
week's broken data.

**Data contracts.** Each gold mart model declares the exact columns and types it
promises to output. If a future change breaks that promise, the build fails at
that moment with a clear error — instead of quietly sending bad data downstream.
Data contracts are one of the most-discussed ideas in data engineering right now.

**mypy (type checking) + pre-commit.** mypy reads your Python code and flags
places where a variable might not be the type you think it is — it catches bugs
before running anything. After a one-time `pre-commit install`, pre-commit runs
quick checks automatically right before each git commit, so style and type
problems never make it into the repo.

**dbt build in CI, on fixture data.** CI will carry a tiny handmade dataset
(~10 fake trials covering every status and phase). Every push to GitHub builds
the real dbt models against it and runs the dbt tests. This proves the models
themselves work — not just the Python around them.

**BigQuery (stretch, but included).** Google's cloud data warehouse, free tier.
Because dbt models are portable, the same SQL that builds marts in DuckDB can
build them in BigQuery. "Same models, two warehouses" is a genuinely rare
portfolio line for a new grad.

## What stays the same

- All `make` commands keep working — `make ingest`, `make dashboard`, etc.
- The Streamlit dashboard, dbt models, and all existing tests are preserved, not rewritten.
- No API keys needed for the core pipeline; cloud auth uses one free-tier bucket
  and one service-account key you create once and store as a GitHub secret.
- Nothing here requires spending money beyond the existing small Fly machine
  (and GCS/BigQuery stay inside their free tiers at this data size).

## What this asks of you (one-time setup, later — not today)

1. Create a Google Cloud project (free) and a storage bucket.
2. Create a service account key and add it as a GitHub Actions secret.
3. Keep running `fly deploy` as you already do after the Fly-side changes land.

Nothing gets provisioned automatically — you'll run those steps yourself when
we reach that milestone, with exact commands in the docs.

## How you'll see it working

- **Locally:** `dagster dev` opens a browser UI showing your bronze→silver→gold
  pipeline as a clickable diagram with checkmarks.
- **In the repo:** the Actions tab shows a green weekly run with logs.
- **In the bucket:** dated folders per run (`ingestion_run_id=...`), an immutable
  snapshot folder per week, and a `gold/latest/` file the dashboard serves.
- **In CI:** every push runs lint → types → unit tests → real dbt build + tests.

## Small dictionary

- **Bronze / silver / gold** — raw data / cleaned data / final analytical tables.
- **Asset** — one tracked piece of data the pipeline produces.
- **Asset check** — a test that runs against an asset right after it's produced.
- **Manifest** — the receipt your ingestion writes each run (query, counts, status).
- **Cron** — a time-based schedule ("every Monday at 13:00 UTC").
- **Fixture** — small handmade data used for testing without hitting the real API.
- **Free tier** — the always-free usage allowance on Google Cloud.

## Timeline (rough, evenings)

1. **Week 1** — Dagster assets wrapping your pipeline; local UI shows the real graph.
2. **Week 2** — CI depth: types, coverage, pre-commit, dbt-on-fixture, contracts.
3. **Week 3** — Weekly GitHub Action + cloud lake + Fly dashboard pulls from it.
4. **Week 4** — BigQuery portability + updated README/docs/resume bullets.
