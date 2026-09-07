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
# bronze and 253.1 MB of NSCLC bronze per ingestion run, 34.9 MB of silver per
# retained weekly snapshot, 44.6 MB of DuckDB for the first snapshot):
#
#   steady state, 3 retained snapshots  499.7 MB  -> 2.0x headroom
#     (332.0 MB of one bronze run per profile + 104.6 MB of silver + 63.2 MB
#      of DuckDB; walked twice and reproduced to 14 bytes)
#   worst instant of a refresh, same horizon  866.5 MB  -> 1.2x headroom
#     (663.9 MB of two unpruned bronze runs + 139.4 MB of four silver
#      snapshots + 63.2 MB of DuckDB, walked after the new fetch and before
#      `prune-data` trims it; the same construction walked one and two
#      snapshots earlier in the history measured 778.1 MB and 824.2 MB)
#   `make prune-data` at the shipped horizon then took the volume from 866.5 MB
#   back to 499.7 MB (4 runs removed across both profiles, 2026-09-07).
#
# That is why `retention.snapshot_runs_to_keep` is 3 and not the 6 it shipped
# with: at a 6-snapshot horizon the arithmetic of those same measured terms puts
# a refresh's worst instant at 993-1009 MB, i.e. at or over the ceiling, which is
# a full volume mid-write. Extend this volume to 2 GB before raising the
# horizon back to 6 (steady state then ~627-642 MB, 3.1x headroom); the depth
# is the dashboard's trend history, so it is worth paying for. See
# "What to expect" for the headroom rule.
fly volumes create cti_data --region iad --size 1

# Deploy.
fly deploy
```

## No secrets required

ClinicalTrials.gov's API v2 is public and unauthenticated — there is
nothing to pass via `fly secrets set`.

## What to expect

- **First boot** takes longer than a normal deploy: the container runs the
  full pipeline (ingest → transform → dbt-run → dbt-test → quality-report)
  before Streamlit starts serving. Watch progress with `fly logs`.
  Measured on the live app: 59 seconds from `starting make pipeline` to
  `pipeline succeeded` (2026-09-05T02:35:56Z → 02:36:55Z), which includes a
  real 27-page download from ClinicalTrials.gov. That was a *one-profile*
  bootstrap; the refresh is two profiles now. Its successor, measured
  2026-09-07 in this image at commit `78c2050` (Docker 29.4.3, linux/arm64,
  `shared-cpu-1x`, an empty named volume mounted at `/app/data`):
  **130 seconds** for `make pipeline` over both refreshable profiles — a real
  31-page ADRD run (3,037 records) and a real 61-page NSCLC run (6,060
  records), `prune-data` a no-op at 0 runs removed, 32 dbt models and 137 dbt
  tests green, 12 reconciliation checks passing across 2 profiles. Budget
  first boot at about two minutes, not one.
- **Every 7 days**, the entrypoint pauses the dashboard, reruns the pipeline,
  and resumes serving. Budget the outage as one pipeline run plus Streamlit's
  startup. The one-profile figure was 50 seconds, measured on 2026-09-05 in
  this image itself (Docker, linux/arm64, shared CPU) over a reused ingestion
  run — 3,037 trials, 32 dbt models, 115 dbt tests, counts that drift with the
  catalogue and with `dbt_clinical_trials/models/`; the same two counters read
  **32 models and 137 tests** on 2026-09-07 (`dbt parse` plus a counter over
  `target/manifest.json`, corroborated by the container's own `dbt test`,
  which reported `PASS=137`). Measured for two profiles on 2026-09-07 in the
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
  Output is appended to `/app/data/logs/pipeline.log` on the volume, and every
  cadence tick logs its inputs (`refresh check: now=… last=… elapsed=…s
  interval=…s`), so a skipped refresh is visible in the log rather than merely
  absent. Both intervals are tunable via `CTI_REFRESH_INTERVAL_SECONDS` and
  `CTI_REFRESH_CHECK_SECONDS`.
- **This machine cannot auto-suspend to zero.** `auto_stop_machines` is set
  to `"off"` because the background refresh loop must keep running
  continuously — unlike a typical Fly demo app that scales to zero when
  idle. Expect a small recurring cost (roughly $2-5/month on Fly's
  smallest shared-CPU tier as of this writing), not a free deployment.
- **The volume is the constraint, and there is a rule about it.** `pipeline`
  prunes before it builds, so between refreshes `/app/data` holds exactly one
  raw bronze run per profile plus `retention.snapshot_runs_to_keep` weekly
  silver snapshots — and for the minutes during a refresh, one more of each.
  Walked off the live container volume on 2026-09-07 (commit `78c2050`):
  78.8 MB of ADRD bronze and 253.1 MB of NSCLC bronze per run (the plan's
  "~2x ADRD" for NSCLC was an unmeasured estimate; the ratio is 3.2x, and
  ADRD's 78.8 MB lands on the same figure the 2026-09-05 desktop walk reported
  for that profile, to the 0.1 MB both walks round to), 34.9 MB of silver per
  additional retained snapshot across both profiles, DuckDB at
  44.6 → 55.8 → 63.2 MB for 1 → 2 → 3 snapshots, and
  `data/gold` at 0.0 MB at every walk with no `data/quarantine` directory
  present at all — the mart bytes live in the warehouse file, which is the
  term that grew. Against the ceiling the deployed volume reports
  (`size_gb: 1` = 1e9 bytes): steady state 499.7 MB, so
  **headroom = 1e9 ÷ 499.7e6 = 2.0**; the worst instant of a refresh, walked
  after a new fetch and before `prune-data` trims it, is 866.5 MB
  (663.9 + 139.4 + 63.2), i.e. 1.2. That same prune took the volume from
  866.5 back to 499.7 MB (4 runs removed across both profiles).
  The rule: **headroom ≥ 2.5 keeps `snapshot_runs_to_keep` at 6; anything
  less brings it down before the next deploy.** 2.0 < 2.5, so the shipped
  horizon is 3 (`config/project_config.yml` and all three
  `config/profiles/*.yml`). Two things that rule does *not* hide:
  1. at 1 GB no horizon reaches 2.5x, because one bronze run per profile is
     already 332.0 MB and a 2.5x steady state must fit 400 MB — which even a
     single retained snapshot (411.3 MB, measured) exceeds, and a horizon of
     0 is what the arithmetic asks for while `prune_profile` refuses a
     snapshot horizon shallower than bronze's. So 3 is not "2.5x achieved",
     it is the deepest horizon whose refresh peak still leaves >100 MB free
     (866.5 of 1000, walked); at 6 the same terms land at 993-1009 MB, i.e. a
     full volume mid-write.
  2. the term the rule was written to bound — retained silver — is the
     *smaller* one: 34.9 MB per week against 331.9 MB for the one bronze run
     per profile that `bronze_runs_to_keep: 1` retains. Lowering the horizon
     buys volume, not an order of magnitude. `fly volumes extend cti_data
     --size 2` is the lever that restores the rule's premise; the 6-snapshot
     steady state there is 627-642 MB, 3.1x headroom.

  Whether Fly's own volume snapshots (`snapshot_retention: 5`,
  `auto_backup_enabled: true`, reported 2026-09-06) consume the 1 GB is not
  settled here and nothing above depends on it: the arithmetic counts only
  bytes visible inside the container at `/app/data`. If they do count, the
  extension stops being a recommendation.

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
