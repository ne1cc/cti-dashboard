# ADR 0056: AACT Snapshots as the Deep Status-History Source (Own Snapshots Retained for Freshness)

- **Status:** Accepted, conditional (Issue #56 mandate) — adoption becomes binding only after a first real AACT load validates retention, per-era status values, and the own-snapshot comparison. All prototype evidence to date is synthetic fixtures; no AACT file was downloaded or parsed.
- **Deciders:** Repository Owner / Product & Architecture Review (via Issue #56, Epic #43)
- **Date:** 2026-11-01
- **Technical Story:** Issue #56 (Part of Epic #43 — Generalize to the whole registry: retire per-disease profiles)

---

## Context

The platform's longitudinal story has one structural weakness: history begins with this project's own collection. The registry API serves only current records, so status transitions are observable only across successive snapshots the pipeline itself captures. Retention keeps `snapshot_runs_to_keep: 3` weekly snapshots (`config/project_config.yml`) — roughly 14 days of depth. Every status-transition metric (`int_trial_status_history`, recruiting entry/exit flags) is therefore blind beyond a fortnight until the project accrues months of runs.

AACT — the Aggregate Analysis of ClinicalTrials.gov database, a public resource of the Clinical Trials Transformation Initiative (CTTI, co-founded by Duke University and the U.S. FDA) — has published dated static copies of the *entire* registry since 2017. If those copies support coarse historical status reconstruction, transition metrics gain 12+ months of depth without a year of advance data collection, which Epic #43's registry-wide scope needs.

The issue raised a specific retention claim to verify: *AACT publishes daily snapshots, keeping every daily snapshot for the current month plus the first-of-month snapshot for earlier months.*

### Verification Performed (2026-11-01)

**No AACT file was downloaded, parsed, or loaded for this ADR.** The prototype (`src/analysis/aact_status_history.py`) and its tests run entirely on synthetic fixtures; the checks below are observations of web pages only. Every claim that depends on real AACT data is therefore unverified and marked as such.

1. **Live site** ([https://aact.ctti-clinicaltrials.org](https://aact.ctti-clinicaltrials.org), fetched 2026-11-01): confirmed AACT is "refreshed daily" from the source registry and offers "Historical snapshots — download the latest database or past versions to compare", plus a live cloud Postgres. The site was redesigned into an account-gated portal: downloads and database credentials require creating an account, the static file listing (`/pipe_files`) returns 404, and the snapshots page redirects to sign-in. **Per-file retention on the live 2026 site could not be enumerated without an account.**
2. **Published listing, as archived** (official AACT snapshots page, Wayback Machine capture 2025-04-18): the page split downloads into "Current Month's Daily Static Copies" and a "Monthly Archive of Static Copies" back to 2017-01. A tally of the 766 registry archive entries in that listing shows retention that is **both deeper and less regular than the claimed policy**: complete daily series exist for 2023-09 through 2025-03 (scattered gaps); daily copies also exist for 2023-01 through 2023-04 (25–31 copies each), while 2023-05, 2023-06 and 2023-08 hold a single copy; three months have no copy at all (2021-07, 2021-09, 2022-08); and 17 months' only listed copy is dated later than the first of the month — sometimes far later (e.g. 2017-01 through 2018-01, 2021-10, 2022-01, 2022-09, 2023-07). Files rename from `*_clinical_trials*` to `*_clinical_trials_ctgov*` starting 2024-06-24.
3. **Pipe-file policy text, as archived** (official `/pipe_files` page, Wayback Machine capture 2025-02-17; the live page returns 404): describes pipe-delimited flat-file archives and states the retention policy directly: *"Each daily set of flat files is made available for download until the end of the month. On the first of each month, a permanent copy of the flat files is created."*

**Verdict on the claim:** the issue's sentence matches the archived `/pipe_files` policy text as a description of intent, but the actual 2025-04 listing deviates in both directions — deeper than claimed (an ~18-month daily window at capture, plus a dense Jan–Apr 2023 stretch) and less regular than "first-of-month" (missing months, late-dated copies). The practical consequence is that snapshot spacing is irregular, not a clean monthly grid: intervals between retained copies range from days to roughly two months. The archived observations are point-in-time; the live site's actual retention must be re-enumerated under an account before integration.

### Licensing & Terms (sources read, with dates)

- **CTTI disclaimer** — found only in archived AACT pages (footer of the 2025-04-18 capture, marked "Copyright © CTTI 2016"); it was not present on the live landing or sign-in pages fetched 2026-11-01. Its wording is a *request*, not a licence condition: *"CTTI encourages the use of all materials listed on this site in the pursuit of improving the clinical trials enterprise… available to the public for free. We do ask that you acknowledge the source whenever using or referencing CTTI materials."* The accompanying citation is labeled **suggested**, e.g.: *Aggregate Analysis of ClinicalTrials.gov (AACT) Database. Clinical Trials Transformation Initiative (CTTI). Available at: https://aact.ctti-clinicaltrials.org/ (Accessed: \<date\>).* The project will honor the request and use the suggested citation in published data products, but the source does not state this as a binding requirement.
- **No redistribution or derived-data terms were found** in any source read. The disclaimer covers CTTI "materials" and is not a data licence; the integration plan publishes derived data, so redistribution rights remain an open item to settle (or to take up with CTTI) before publication.
- **Portal access terms were not read**: the live portal serves them only behind account creation, and no account was created. They govern the adopted option (downloads and cloud access) and must be reviewed before integration.
- AACT data is derived from ClinicalTrials.gov (NLM/NIH/FDA).
- No personal contact data is involved: the slim history keeps only protocol-level status and date fields (`nct_id`, `overall_status`, start/completion dates, hash) from the AACT `studies` table. The ADR 0065 zero-contact boundary is preserved — the extract schema contains no contact PII by construction.

---

## Decision

**Both sources, by role:**

1. **Adopt AACT as the deep-history backbone for status-transition metrics.** Monthly (first-of-month) AACT snapshots load into a slim `fct_trial_status_history` — one row per `nct_id` per snapshot date with `overall_status`, key registry dates (start, primary completion, completion), and a field-level record hash for change detection. The code path is exercised by prototype tests over a synthetic 13-month span; **whether real AACT copies deliver ≥12 months of usable coverage is unconfirmed until a first real load** (see Limitations).
2. **Keep this project's own weekly snapshots unchanged** as the freshness and capture-age layer: they feed current-state metrics, remain under project retention control, and act as the tiebreaker on dates both sources observe.
3. **On overlap, own observations win** for same-day status; AACT fills every date own snapshots do not cover. Discrepancies at (near-)matching dates are reported, not silently resolved — `compare_with_own()` classifies each own observation as `match`, `status_mismatch`, or `own_only` against the nearest AACT snapshot within tolerance.
4. **Prototype scope honored:** the loader (`src/analysis/aact_status_history.py`) consumes CSV extracts of the AACT `ctgov.studies` table (one `\copy` command per snapshot date, documented in the module docstring) and never touches the production warehouse. It normalizes `overall_status` (trimmed, upper-cased) at read time because AACT spans registry API eras with capitalization differences; real per-era values still need confirmation against real extracts. No AACT dumps or extracts are committed; full-dump restores (pg_restore) and cloud-DB access are out of prototype scope.

### Expected discrepancy sources (why mismatches are normal)

- **Cadence:** own weekly snapshots vs AACT monthly boundaries — tolerance-based nearest-date matching bridges this but cannot be exact.
- **Registry reporting lag:** overall status is sponsor-reported; AACT copies and our API captures can disagree for days around a transition even when both are correct.
- **Status-verification:** the registry may revise status retroactively; older AACT copies preserve what was published at that time — which is precisely the historical value.

---

## Limitations

- **No real AACT load has been performed.** Every prototype result cited here — transition flags, hashes, comparison outcomes, the 13-month span — comes from synthetic fixtures. The adoption conditions (retention under an account, per-era status values, ≥12-month coverage from real copies, and the comparison against own snapshots on real data) are all outstanding.
- **Irregular snapshot spacing, not clean monthly granularity:** gaps, missing months, and late-dated copies (see Verification) mean a transition is dated to the *next retained snapshot, whatever its date*; intervals range from days to ~2 months. Metrics must state "status as of the snapshot that revealed it", not a daily truth. The dense daily window (Sep 2023–Mar 2025, plus Jan–Apr 2023, at the 2025-04 capture) rolls forward and must be re-confirmed per download.
- **Account-gated access:** the live AACT portal requires an account (credentials for the cloud Postgres, login for static downloads). Integration work must provision credentials via `.env` (added to `.env.example` at integration time, never committed).
- **Backfill still requires per-date data; the CSV path does not avoid a restore.** Static copies are PostgreSQL dumps (~GB-scale, full registry) and dumps before 2021-11-15 are schema-incompatible (published AACT note). The documented `\copy` extract runs against a connected database, so each historical snapshot date still requires that date's dump restored locally first — or that month's daily pipe-delimited flat-file set, which may avoid Postgres but is not yet parsed by the prototype's reader (it expects `studies_YYYYMMDD.csv`). Backfill cost is therefore per-date restores or pipe-file processing, not a single extract.
- **Retention drift risk:** the archived listing evidence is from 2025-04; the live 2026 site's per-file inventory is not publicly enumerable. Re-verify at account creation before committing to a specific backfill plan.
- **Hash semantics differ:** own snapshots hash the full registry JSON (`source_json_hash`); the AACT slim history hashes only the kept fields. Hashes are comparable only within a source, never across.

---

## Consequences & Follow-up

- Transition metrics over ≥12 months are *expected* to be feasible upon integration without a waiting period, but that expectation rests on synthetic-fixture tests only; the first real AACT load must confirm it before transition metrics ship.
- Integration issues (tracked under Epic #43): provision AACT account + `.env.example` keys; review the portal access terms (not read); settle redistribution/derived-data rights with CTTI (none found in the sources read); promote the slim history into dbt as an AACT-sourced staging model alongside `int_trial_status_history`; define overlap precedence in the warehouse; run `compare_with_own()` against real own snapshots and report discrepancies; update `README` history caveats and `docs/metric_definitions.md` once shipped; cite AACT with the CTTI suggested citation in any published data product.
- Until integration lands, the shipped pipeline behavior is unchanged; this ADR records the decision and the evidence gathered so far — dated observations of archived web pages plus synthetic-fixture prototype tests, with no real AACT data loaded yet.
