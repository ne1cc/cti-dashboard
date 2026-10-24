# Deploy the Streamlit dashboard (Fly.io)

**Live:** this project's own deployment runs at
[cti-dashboard.fly.dev](https://cti-dashboard.fly.dev/).

The dashboard reads a local DuckDB warehouse built by the pipeline. Unlike
Streamlit Community Cloud (see `docs/DEPLOY_STREAMLIT.md`), this Fly.io
setup keeps the pipeline running: the container bootstraps the warehouse on
first boot and then refreshes it every 7 days on its own, matching this
project's weekly-snapshot design (`config/project_config.yml`'s
`scope.refresh_cadence: weekly`). A refresh briefly stops the dashboard,
because DuckDB will not let a writer run while a reader holds the file — see
"What the first week in production caught" for the failure that forced this
shape.

## What's already in the repo

- `Dockerfile` — builds the app image (Python 3.12, `uv`, dbt deps).
- `entrypoint.sh` — a supervisor, not just a launcher: it runs `make pipeline`
  once if no warehouse exists yet, then owns the Streamlit child process and,
  on schedule, stops that process, reruns the pipeline, and starts it again.
- `fly.toml` — Fly app config: one always-on machine, one persistent
  volume (`cti_data`) mounted at `/app/data`, health-checked against
  Streamlit's `/_stcore/health` endpoint, and `kill_timeout = '270s'` so a
  deploy that lands during a refresh lets the pipeline finish rather than
  killing it mid-write. 270s is 2x the longest measured two-profile run
  (130s, in this image on 2026-09-07 at commit `78c2050`), rounded up to 30s,
  and `tests/test_fly_config.py` pins that relation. The earlier 90s was set
  from a 59s *one-profile* cold run and would have force-killed a
  two-profile refresh mid-write.
- `.github/workflows/ci.yml` — unified CI/CD workflow: gates production
  deployment on the `lint-and-test` job passing on pushes to `main`.
- `.github/scripts/deploy_fly.sh` — automated deployment script with pre-rollout
  release image capture, post-deployment smoke check against `/_stcore/health`,
  and automated rollback on rollout or smoke failure.

## Automated deployment (GitHub Actions)

Production deployments are automated and gated by CI in `.github/workflows/ci.yml`.
The uncoordinated standalone Fly deployment workflow has been removed
so broken code can never deploy to production if tests fail.

### Unified Pipeline & CI Gating
The `deploy` job runs strictly downstream of `lint-and-test` (`needs: lint-and-test`).
If code fails linting, code formatting, type checking, or unit test coverage gates,
the deployment job does not execute.

- **Trigger:** Gated strictly to pushes on `main` (`github.event_name == 'push' && github.ref == 'refs/heads/main'`).
  Pull requests and manual `workflow_dispatch` invocations run the full verification
  suite without triggering a deploy.
- **GitHub Environment:** The deploy job runs in the GitHub environment `production`,
  restricting `FLY_API_TOKEN` secret exposure to the deploy step. This allows
  repository administrators to configure protection rules (such as required reviewers)
  without impacting CI test execution.
- **Concurrency:** Uses concurrency group `deploy-production` with `cancel-in-progress: false`.
  This serializes production rollouts so overlapping runs cannot clobber each other
  or deploy out of order, while ensuring an active in-flight rollout is never terminated
  mid-run.

### Deployment Script & Health Verification
Rollouts are executed by `.github/scripts/deploy_fly.sh`:
1. **Pre-rollout image capture:** Inspects current releases using
   `flyctl releases --image --json -a cti-dashboard` to capture the Docker image
   reference of the active release before deploying new code.
2. **Rollout execution:** Runs `flyctl deploy --remote-only --app cti-dashboard`.
3. **Smoke check:** Polls `/_stcore/health` with bounded retries (12 attempts,
   10-second intervals, 5-second request timeout; matching the ~2-minute initial boot budget).
   HTTP 200 signals a healthy deployment.

### Automated Rollback
If either the deployment command or the post-deployment smoke check fails:
- The script automatically initiates a rollback by redeploying the previously captured
  image reference: `flyctl deploy --image "$PREV_IMAGE" --app cti-dashboard`.
- It performs a follow-up health check against `/_stcore/health` to confirm recovery.
- Regardless of whether rollback restores service, the script **always exits with a non-zero exit code**,
  ensuring GitHub Actions marks the workflow failed and surfaces the failure immediately.
- If no previous image exists (e.g. first deployment), rollback is skipped and failure is
  reported directly.

## One-time setup (you run these — they provision billed Fly resources)

```bash
# Install the Fly CLI if you haven't: https://fly.io/docs/flyctl/install/
fly auth login

# Create the app from fly.toml (app = "cti-dashboard"; rename that line
# first if the name is already taken on Fly).
fly launch --no-deploy

# Create the persistent volume fly.toml expects. 1 GB is the size the deployed
# volume reports (`fly volumes list --app cti-dashboard --json`, 2026-09-06:
# vol_vwnxk31ln0w618mv, cti_data, size_gb 1, region iad, snapshot_retention 5,
# auto_backup_enabled true).
#
# Measured footprint of /app/data in this image on 2026-09-07 (commit 78c2050,
# linux/arm64, fresh named volume, both refreshable profiles — 78.8 MB of ADRD
# bronze and 253.1 MB of NSCLC bronze per ingestion run, so one retained bronze
# run per profile is 331.9 MB (the whole bronze directory walks 332.0 MB; the
# 0.1 MB is per-directory rounding), 34.9 MB of silver per retained weekly
# snapshot, DuckDB 44.6 MB at one snapshot and 63.2 MB at three):
#
#   steady state, 3 retained snapshots  499.7 MB  -> 2.0x headroom
#     (walked 332.0 bronze + 104.6 silver + 63.2 DuckDB; per-directory 1-dp MB
#      sum to 499.8, so the byte total 499,714,216 is authoritative — reproduced
#      to 14 bytes across two independent rounds)
#   worst instant of a refresh, same horizon  866.5 MB  -> 1.15x headroom
#     (walked 663.9 bronze + 139.4 silver + 63.2 DuckDB, taken after the new
#      fetch and before `prune-data` trims it; the same fetch-time construction
#      walked at one and two retained snapshots measured 778.1 MB (411,344,692
#      steady / 778,098,816 peak) and 824.2 MB)
#   `make prune-data` at the shipped horizon then took the volume from 866.5 MB
#   back to 499.7 MB (4 runs removed across both profiles, 2026-09-07).
#
# Why the horizon is 3 and not the 6 it shipped with. The plan's rule of record
# is "headroom >= 2.5 keeps 6", but at 1 GB that is unsatisfiable for any horizon
# (2.5x needs a steady state <= 400 MB, and a single retained snapshot already
# walks 411.3 MB). The criterion actually applied is the deepest horizon whose
# worst refresh instant leaves more than a chosen 100 MB free of the 1,000 MB
# ceiling — 3, at 866.5 MB walked (133.5 free). Beyond the walked points the peak
# is a prediction of one two-point fit through the walked peaks peak(1)=778.1 and
# peak(3)=866.5 (44.2 MB per extra snapshot): peak(6) ~= 999 MB (ESTIMATE), at or
# over the ceiling. The hazard there is the fetch/transform writes, not the DuckDB
# build — `make pipeline` runs `prune-data` before `dbt-run`, so the transient
# second bronze run is reclaimed before the warehouse is written. `fly volumes
# extend cti_data --size 2` restores the rule's premise; at 2 GB the same fit puts
# steady(6) ~= 632 MB, ~= 3.2x headroom (ESTIMATE — no 2 GB volume has been
# walked, re-measure after extending). Horizon 3 also gives up the guaranteed
# monthly trend series; see "What to expect" for that mechanism and number.
fly volumes create cti_data --region iad --size 1

# Deploy manually if needed.
fly deploy
```

## Adding indication profiles

`make pipeline` refreshes every non-ingest-only profile found in
`config/profiles/`. The 1 GB volume measurements above cover only ADRD and
NSCLC. Adding condition profiles increases raw bronze, retained silver snapshots,
warehouse size, and refresh time; the existing measurements do not establish
capacity for a larger profile set. Before deploying added profiles, run complete
profile snapshots in a measured environment, record peak and steady data size
and pipeline duration, then set a volume size and retention horizon with safe
headroom. A first deployment of new profiles also rebuilds the shared warehouse.

Local measurement on 2026-10-04 for complete snapshots of ten added profiles:
50,795 studies produced 1.4 GB of bronze and 161 MB of silver. The warehouse
models other than trial similarity occupied 272 MB. Building
`mart_trial_similarity` over all twelve profiles used 28 GB of temporary DuckDB
storage after about 14 minutes without completing; the run was stopped and its
temporary files were removed. This run does not establish a final warehouse
size or refresh time. The 1 GB Fly volume cannot hold even the ten new raw
snapshots, before retaining the existing profiles, silver data, or the
warehouse. Do not deploy the expanded profile set until storage has been sized
from a completed full build and the similarity mart's runtime and peak storage
have been addressed.

### 2026-10-07 outage: the precondition above was not met, and production went down

Release v26 (2026-10-05) deployed the twelve-profile set onto the 2 GB volume.
The set's own bronze exceeded the volume's steady budget, the volume reached
100%, and the machine entered a crash loop: `entrypoint.sh`'s `log()` helper
failed its first append with ENOSPC and `set -euo pipefail` killed the
container within seconds of boot, before anything could be logged. With
`auto_start_machines` off, the stopped machine stayed dark until 2026-10-15.
Remediation in PR `fix/production-volume-and-profile-scope`:

- `fly.toml` sets `CTI_REFRESHABLE_PROFILES = 'adrd,oncology_nsclc'`; the
  deployed refresh is the measured-passing two-profile configuration again.
  `src/profiles.py` `refreshable()` enforces the variable and fails loudly on
  unknown names. Remove the variable only after the paragraph above is
  satisfied: the similarity mart bounded, and storage sized from a completed
  local full build.
  Update 2026-10-15: the first half is now done — `mart_trial_similarity`
  discovers pairs through `int_similarity_candidates` (shared condition-group
  tokens, capped by `var('similarity_max_token_df')`) instead of an unbounded
  pairwise self-join, and `tests/test_similarity_candidates.py` pins the new
  output to the old logic restricted to candidates. Trials sharing no
  surviving token now get zero neighbors instead of 25 minimum-score rows.
  The sizing half still stands: no production re-expansion until a completed
  twelve-profile local build has recorded pairs, temp peak, warehouse size,
  and refresh duration.
- The volume was extended 2 GB → 5 GB (`fly volumes extend cti_data --size 5`).
  The stale ten-profile bronze (~1.4 GB) is retained by `prune_all`, which
  intentionally prunes `registry.all()` at `bronze_runs_to_keep: 1`.
- `entrypoint.sh` logging is non-fatal; `auto_start_machines` is on so a
  stopped machine wakes on traffic.

## Secrets and CI environment

- ClinicalTrials.gov's API v2 is public and unauthenticated — there is nothing
  to pass via `fly secrets set` on the running Fly machine.
- GitHub Actions automated deployment requires a `FLY_API_TOKEN` configured in
  repository secrets or within the `production` GitHub Environment settings.

## What to expect

- **First boot** takes longer than a normal deploy: the container runs the
  full pipeline (orchestrate → prune-data → dbt-run → dbt-test → quality-report)
  before Streamlit starts serving. Watch progress with `fly logs`.
  Measured on the live app: 59 seconds from `starting make pipeline` to
  `pipeline succeeded` (2026-09-05T02:35:56Z → 02:36:55Z), which includes a
  real 27-page download from ClinicalTrials.gov. That was a *one-profile*
  bootstrap; the refresh is two profiles now. Its successor, measured
  2026-09-07 in this image at commit `78c2050` (Docker 29.4.3, linux/arm64,
  `shared-cpu-1x`, an empty named volume mounted at `/app/data`):
  **130 seconds** for `make pipeline` over both refreshable profiles — a real
  31-page ADRD run (3,037 records) and a real 61-page NSCLC run (6,060
  records), `prune-data` a no-op at 0 runs removed, dbt models and dbt
  tests green, 12 reconciliation checks passing across 2 profiles. Budget
  first boot at about two minutes, not one.
- **Every 7 days**, the entrypoint pauses the dashboard, reruns the pipeline,
  and resumes serving. Budget the outage as one pipeline run plus Streamlit's
  startup. The one-profile figure was 50 seconds, measured on 2026-09-05 in
  this image itself (Docker, linux/arm64, shared CPU) over a reused ingestion
  run — 3,037 trials, with all dbt models and tests passing green.
  Measured for two profiles on 2026-09-07 in the
  same image: **130 s** from an empty volume, **120 s and 118 s** for two
  forced pulls onto a volume that already held a run (each = 47 s/45 s of
  `orchestrate --full-refresh` + 73 s of `prune-data dbt-run dbt-test
  quality-report`), and **67 s** when a same-day `make pipeline` skips the
  fetch because a run for the same query hash is inside the 24-hour
  `ingestion.reuse_window_hours` (`config/project_config.yml`). A weekly
  refresh always lands outside that window, so budget the ~120-130 s figure;
  67 s is what a same-day retry of a failed refresh costs. The same
  one-profile run finished in 19 seconds on an unloaded desktop, so treat the
  container figures as the ones that matter and not a floor.
  A longer refresh means a longer serving outage, and the outage is bounded by
  `kill_timeout` (270s): `entrypoint.sh` defers SIGTERM until the in-flight
  `make pipeline` returns, and `auto_stop_machines = 'off'` means nothing else
  comes to serve meanwhile.
  It has to pause: DuckDB takes one writer *or* readers, and the dashboard
  caches its read-only connection for the life of the process. While paused,
  Fly's proxy stops routing to the machine, but nothing restarts: the machine's
  `restart` policy is `on-failure`, which keys off the entrypoint process's
  exit code, and the supervisor stays PID 1 through the whole refresh.
  Output goes to stdout/stderr and is also appended best-effort to
  `/app/data/logs/pipeline.log` on the volume. A failed file append cannot
  suppress stdout or change the pipeline exit status. Every
  cadence tick logs its inputs (`refresh check: now=… last=… elapsed=…s
  interval=…s`), so a skipped refresh is visible in stdout rather than merely
  absent. Both intervals are tunable via `CTI_REFRESH_INTERVAL_SECONDS` and
  `CTI_REFRESH_CHECK_SECONDS`.
- **This machine cannot auto-suspend to zero.** `auto_stop_machines` is set
  to `"off"` because the background refresh loop must keep running
  continuously — unlike a typical Fly demo app that scales to zero when
  idle. Expect a small recurring cost (roughly $2-5/month on Fly's
  smallest shared-CPU tier as of this writing), not a free deployment.
- **The volume is the constraint, and the horizon is set from it.** `pipeline`
  prunes before it builds, so between refreshes `/app/data` holds exactly one
  raw bronze run per profile plus `retention.snapshot_runs_to_keep` weekly
  silver snapshots — and for the minutes during a refresh, one more of each.
  Walked off the live container volume on 2026-09-07 (commit `78c2050`):
  78.8 MB of ADRD bronze and 253.1 MB of NSCLC bronze per run (the plan's
  "~2x ADRD" for NSCLC was an unmeasured estimate; the measured ratio is 3.2x,
  and ADRD's 78.8 MB lands on the figure the 2026-09-05 desktop walk reported for
  that profile), so **one retained bronze run per profile = 331.9 MB** (the whole
  bronze directory walks 332.0 MB — the 0.1 MB is per-directory rounding);
  34.9 MB of silver per additional retained snapshot across both profiles; DuckDB
  walked at 44.6 MB for one snapshot and 63.2 MB at three — no steady walk
  isolated the 2-snapshot value, so between them it follows the fit's ~9.3
  MB/snapshot; and `data/gold` at 0.0 MB at every walk with no `data/quarantine`
  directory present at all — the mart bytes live in the warehouse file, the term
  that grew. The *walked* instants against the ceiling the deployed volume reports
  (`size_gb: 1` = 1e9 bytes) are: steady 411.3 MB (1 snapshot) and 499.7 MB
  (3 snapshots; byte totals 411,344,692 and 499,714,216 — per-directory figures
  are 1-dp MB, so the terms may sum 0.1 MB off the authoritative byte total), and
  worst refresh instant 778.1 MB (1 snapshot) and 866.5 MB (3, fetched before
  `prune-data` trims it). Headroom at the shipped steady state is 1e9 ÷ 499.7e6 =
  **2.0**; at its worst instant, 1000 ÷ 866.5 = **1.15**. That prune took the
  volume back from 866.5 to 499.7 MB (4 runs removed across both profiles).

  **The rule of record, and why it did not decide the number.** (a) The plan's
  rule is `headroom ≥ 2.5` on the steady state keeps `snapshot_runs_to_keep` at 6.
  (b) At 1 GB that is unsatisfiable for *every* horizon: 2.5× demands a steady
  state ≤ 400 MB, but the single retained bronze run is already 331.9 MB and the
  shallowest legal state — one retained snapshot — walks 411.3 MB, so even `k = 1`
  falls short, and `k < 1` is refused by `prune_profile`'s horizon guard (0 deletes
  every run the profile retains; a negative is refused there too, rather than by
  `runs_to_prune`'s helper check). (c)
  The criterion actually applied is the deepest horizon whose worst refresh
  instant leaves more than a **chosen** 100 MB free of the 1,000 MB ceiling: that
  floor and the 2.0× acceptability are judgment on measured terms, not
  measurements. 3 (peak 866.5, walked, 133.5 free) is where they land. (d)
  `fly volumes extend cti_data --size 2` is the user's action that restores (a)'s
  premise; until then the shipped horizon is 3 (`config/project_config.yml` and
  all three `config/profiles/*.yml`).

  Beyond the walked points the peak is a *prediction* of one two-point model: each
  extra retained snapshot adds 34.9 MB of silver plus the ~9.3 MB of DuckDB growth
  between the two walked steady states — ≈44.2 MB/step, fit through the walked
  peaks `peak(1) = 778.1` and `peak(3) = 866.5`, so `peak(k) ≈ 866.5 + 44.2·(k−3)`:
  `peak(4) ≈ 910.7` (only ~89 MB free, past the 100 MB floor) and `peak(6) ≈ 999.1`
  (ESTIMATE) — at or over the ceiling. Where the model can be checked against a
  walked point between its anchors it is: `peak(2)` predicts 822.3 against the
  824.2 MB walked, 1.9 MB low. The exposure at that peak is the
  **fetch/transform** writes, not the DuckDB build: `make pipeline` runs
  `prune-data` before `dbt-run` (Makefile), so the transient second bronze run is
  reclaimed before the warehouse is written. Decompose the same model at `k = 6`
  and the shape is clear: the DuckDB file predicts ~91 MB (63.2 + 3·9.3) and the
  retained silver ~209 MB (6·34.9) — a ~632 MB steady state, two thirds of the
  ~999 MB peak — while the transient second bronze run is ~332 MB. This is
  arithmetic that rules 6 out; the term the rule was written to bound — retained
  silver — is the *smaller* one (34.9 MB/week against the 331.9 MB single bronze
  run), so lowering the horizon buys volume, not an order of magnitude.

  What depth 3 gives up is trend history, and it is a counted cost: `entrypoint.sh`
  refreshes weekly, so 3 snapshots span 14 days, and a 14-day span sits inside a
  single calendar month on **197 of 365 weekly start dates** (measured 2026-09-07)
  — on those weeks the Geography Trends page's `recruiting_growth_3m` is null,
  because the model reports no 3-month delta without a second month (see
  `docs/metric_definitions.md`); that null is the honest reading of "not enough
  history" the 0.0 used to disguise. At 2 GB, restoring horizon 6 makes the series
  always present; the 6-snapshot steady state there is ≈632 MB (ESTIMATE, the same
  fit: `499.7 + 44.2·3`), ≈3.2x headroom — re-measure after extending, no 2 GB
  volume has been walked.

  Whether Fly's own volume snapshots (`snapshot_retention: 5`,
  `auto_backup_enabled: true`, reported 2026-09-06) consume the 1 GB is not
  settled here and nothing above depends on it: the arithmetic counts only
  bytes visible inside the container at `/app/data`. If they do count, the
  extension stops being a recommendation.

## Local freshness metadata and disk preflight (#88, partial)

This local implementation does not activate production monitoring. External
uptime/freshness alerts, the public metadata/API contract in #58, secret setup,
and real staging/production acceptance remain open. No HTTP route or server is
added here. A healthy Streamlit process alone does not prove fresh data.

Defaults are `CTI_FRESHNESS_MAX_AGE_DAYS=10` and
`CTI_REFRESH_MIN_FREE_BYTES=500000000` (positive integer bytes). Capture age is
stale only when **greater than** the configured number of days; exactly ten
days is acceptable at the default. Nonpositive or malformed thresholds fail
the check. The floor is an interim conservative operating choice based on
previously documented transient growth of roughly 367 MB plus margin, not a
new capacity measurement. Verify actual free bytes and approve the floor
before activation: it can block an already near-full deployment. This does
not authorize adding profiles or increasing the volume.

The supervisor checks available bytes on the configured warehouse filesystem
before initialization writes and before pausing a serving dashboard. A disk
rejection reports `free_bytes` and `min_free_bytes` on stdout, skips the
pipeline, and leaves the existing warehouse and dashboard serving. If no
warehouse exists, it reports that bootstrap is impossible under the floor;
there is no prior data to serve. Scheduled retries retain the existing weekly
epoch cadence and hourly due checks. The legacy `.last_pipeline_run` epoch
advances only after the JSON success marker is published.

The marker is `.freshness.json` under the configured warehouse's grandparent
(normally the data directory). It uses a temporary file on that filesystem
and atomic replace. Schema version 1 records:

- `attempt_at_utc`, `attempt_status` (`running`, `success`, `failed`) and an
  optional verification error;
- `last_success.pipeline_completed_at_utc`, separately from each profile's
  original ingestion-start `captured_at_utc` and accepted `run_id`;
- each profile's warehouse row counts for `dim_trial`, `fct_trial_snapshot`
  (all retained snapshots), and `mart_trial_similarity`, plus reconciliation
  totals (`checks_total`, `checks_failed`);
- `source_api_version: "v2"` and `source_release_at_utc: null`. The source's
  dataset release timestamp is unknown; capture time is not a release version.

Only the registry's configured `refreshable()` set is required. A success
requires a complete successful manifest and an accepted current warehouse run
for every required profile, along with passing existing reconciliation checks.
The supervisor invokes the writer only after the full pipeline, including dbt
tests and the quality-report gate, succeeds. A failed attempt retains the
previous `last_success`; an interrupted attempt remains `running` and fails
freshness verification. Reusing an old capture may complete the pipeline
successfully but cannot reset capture age. Missing, malformed, future-dated,
incomplete, or unsuccessful metadata never means fresh.

Every dashboard page checks the selected profile and shows its capture time
and last verified pipeline completion. It warns on stale/unknown metadata or
an unsuccessful latest attempt. Decisions use the current UTC clock on each
page render. A stale profile fails the aggregate CLI check even when another
profile is fresh.

Run these commands **only against an explicit disposable local root** for
verification. The fixture runner uses saved synthetic records and performs
a local dbt build; it never refreshes live registry data. Use a new empty
destination (the fixture runner refuses nonempty roots).

```bash
export CTI_PROJECT_ROOT="$(mktemp -d)/freshness-fixture"
export CTI_REFRESHABLE_PROFILES=adrd,oncology_nsclc
export CTI_FRESHNESS_MAX_AGE_DAYS=10
export CTI_REFRESH_MIN_FREE_BYTES=500000000
uv run python -m tests.fixture_warehouse --root "$CTI_PROJECT_ROOT"
uv run python -m src.quality.freshness --preflight
# Only after a completed pipeline/dbt fixture build and all its tests:
uv run python -m src.quality.freshness --record-success
uv run python -m src.quality.freshness --check
```

`--check` prints JSON and exits 1 on unacceptable metadata or low disk, 0
otherwise. Fixed historical fixture captures are expected to be stale; a
recent pipeline completion must not hide that. Without a flag, the CLI prints
metadata/status for inspection without enforcing freshness as an exit code.
`--preflight` is read-only. The supervisor uses `--record-attempt` before the
pipeline and `--record-failure` on failure; these preserve the last success.
If success verification records an error, the subsequent `--record-failure`
preserves it. Status and `--check` expose that reason in `metadata.error` and
`errors`, including when no prior success exists; the dashboard warning uses
the same `errors`. A new `--record-attempt` clears the previous attempt's error.
Those writer flags change only the local marker and never run the pipeline.
Do not use `--record-success` to label an unverified warehouse successful.

If a check fails, inspect stdout for the attempt, required profile, and actual
free/floor bytes; inspect the accepted manifests and quality reconciliation
before retrying. Use only approved retention cleanup or capacity changes.
Do not delete the serving warehouse, manually advance the epoch, or rewrite
capture times to silence a warning. A failed atomic marker write preserves
the old marker and cannot advance the epoch; stdout is needed when the disk
cannot persist failure metadata.

Low-space rejection is **not atomic rollback**. Unexpected mid-refresh
ENOSPC/OOM or a partial multi-model dbt build can still leave partial state.
Full atomic publication needs a separate approved design. An outside monitor
still needs a parent-approved metadata/API interpretation, workflow/issue
permissions, app-scoped credentials, activation approval, a verified disk
floor, and a real controlled alert within a check interval. Fixture/stub
evidence here does not satisfy that outside acceptance; #88 stays open.

## What the first week in production caught

The original design — refresh in the background while the dashboard keeps
serving — did not work, and nothing in the deployment looked broken while it
was failing. Two independent defects, both fixed on this branch:

**1. The refresh could never get the DuckDB write lock.** Nine consecutive
scheduled attempts (10:19Z through 18:21Z on 2026-09-05) died at `dbt-seed`:

```
_duckdb.IOException: IO Error: Could not set lock on file
"/app/data/warehouse/clinical_trials.duckdb": Conflicting lock is held in
/usr/local/bin/python3.12 (PID 672)
```

PID 672 was the dashboard: `dashboard/components/data.py` caches a read-only
DuckDB connection with `@st.cache_resource` for the lifetime of the Streamlit
process, and DuckDB permits one read-write connection *or* read-only ones,
never both. Serving had to stop for the writer to run, which is what the
pause-and-resume bullet above describes. `entrypoint.sh` in the running
container was byte-identical to the committed copy (md5
`ccc84f88a22bbe1105884dcb8487a2ae`), so this was not a stale-image artefact.

**2. Even a fully green pipeline run could refresh no data.** `make ingest`
defaults `--profile` to `default`, which resolves through the registry to the
`adrd` profile and writes `data/bronze/adrd/manifests/`. `make transform` takes
no profile at all and reads the global config's legacy
`data/bronze/manifests/`. Reproduced locally on 2026-09-05 in a clean worktree,
where it fails silently:

```
Run 20260905T203954Z_89f2b958 finished: status=success pages=31 records=3037
uv run python -m src.cli transform
No runs to transform (0 manifests inspected).
```

Fourteen seconds after that run started, transform was looking at an
empty directory, and dbt and the quality gate went on to pass against silver
the run had never touched. The live volume shows the same
divorce: `data/bronze/manifests/` holds the 02:36Z bootstrap's manifest while
`data/bronze/adrd/manifests/` holds the 19:19Z run's. Fixed by pointing the
default config's bronze paths at the adrd profile, with tests pinning that the
default config, the adrd profile, and dbt's `ingestion_manifests` source all
name the same directory.

What the log cannot settle: the loop's first attempt came at 10:19:47Z, 7.7
hours after the 02:36:55Z bootstrap, and then retried hourly. Firing at all
requires `elapsed >= 604800`, which means `.last_pipeline_run` was missing or
ancient by 10:19Z — a healthy marker would have made the loop skip silently for
six more days. But the same day's log also holds three pipeline runs (09:07Z,
19:19Z, 19:55Z) with no `[entrypoint]` prefix, started from outside the loop,
and the marker now on the volume carries a 20:02:42Z timestamp that matches no
`[entrypoint]` line at all. That day's `pipeline.log` therefore mixes
loop-driven runs with manually driven ones, so it cannot settle the 10:19Z
trigger. The per-tick `refresh check:` line added here is what makes the next
occurrence answerable.

## The pre-cutover flat bronze tree is stranded on the live volume, and only you can delete it

Item 2 names what the old layout left behind: `data/bronze/manifests/` and
`data/bronze/api_responses/`, flat under `data/bronze/` instead of nested under a
profile id. Those directories are still on the deployed volume, and nothing in this
repo will ever remove them. `dbt_clinical_trials/models/staging/_sources.yml` globs
the per-profile paths, so a flat directory is not a source; `src/utils/retention.py`
prunes only inside roots a profile's config names — its anti-stranding rule, because
a page directory no manifest names is invisible to every later prune — and a flat
tree is exactly that; `init-data-dirs` creates the profile tree and deletes nothing.

**The 499.7 MB steady and 866.5 MB peak figures above therefore exclude them**, and
the margin those figures bought is 133.5 MB. This document has no measurement of the
flat tree's size; what is measured is one ADRD bronze run at 78.8 MB (2026-09-07,
commit `78c2050`), and the flat tree holds the pre-cutover equivalent of one, so
treat that as the order of magnitude and confirm it rather than trusting it:

```bash
fly ssh console -a cti-dashboard
du -sh /app/data/bronze/manifests /app/data/bronze/api_responses   # if either exists
du -sh /app/data                                                   # the current total
ls /app/data/bronze/    # the live tree is per-profile: adrd/, oncology_nsclc/
```

The same walk may find `data/bronze/_schema_baseline.json` — the single global
baseline the per-profile drift check replaced. It is gone from the repository and
inert on the volume.

Remove them only **after** a refresh that landed both profiles, i.e. after
`pipeline succeeded` appears in `/app/data/logs/pipeline.log` and each of
`/app/data/bronze/adrd/manifests/` and `/app/data/bronze/oncology_nsclc/manifests/`
holds that run's manifest. Before that point they are the only copy of the last
pre-cutover ingestion:

```bash
rm -rf /app/data/bronze/manifests /app/data/bronze/api_responses
du -sh /app/data   # the drop from the figure recorded above is what you recovered
```

## Egress from Fly is verified

ClinicalTrials.gov's bot protection blocks `httpx`'s TLS/HTTP handshake
specifically — `curl`, stdlib `urllib`, and `requests` all succeed with
identical requests while `httpx` gets a 403 — which is why this project's
ingestion client uses `requests`. That fix now holds from a real Fly egress
IP: the container's run `20260905T191901Z_77632421` reported
`status=success pages=27 records=2618 quarantined=0` with no 403.

## Notes

- **The scheduled refresh covers both refreshable indications, and the
  dashboard serves one at a time.** `make pipeline` runs `orchestrate`, which
  walks every profile in `config/profiles/` that is not `ingest_only`: the
  2026-09-07 container run logged `Orchestrating 2 profile(s): ['adrd',
  'oncology_nsclc']` (commit `78c2050`). `full_catalog` stays opt-in —
  `ingest_only: true` at `config/profiles/full_catalog.yml:21` — so its
  registry-wide pages never reach silver by accident. The dashboard does not
  blend the two: `dashboard/app.py` renders a profile selector and every read
  in `dashboard/components/data.py` takes the selected `profile_id`, so
  trials from both profiles in the shared `dim_trial` stay distinguishable.
  What is still single-profile by default is the manual entry point:
  `make ingest` sends the Makefile's `CONDITION` (`Alzheimer Disease`, the
  `adrd` profile). Use `make orchestrate` for both.
- This app is a **portfolio demonstration**, not clinical decision
  support.
