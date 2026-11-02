# ADR 0056: AACT Snapshots as the Deep Status-History Source (Own Snapshots Retained for Freshness)

- **Status:** Accepted (Issue #56 Mandate — adoption binding on account provisioning and pipeline integration)
- **Deciders:** Repository Owner / Product & Architecture Review (via Issue #56, Epic #43)
- **Date:** 2026-11-01
- **Technical Story:** Issue #56 (Part of Epic #43 — Generalize to the whole registry: retire per-disease profiles)

---

## Context

The platform's longitudinal story has one structural weakness: history begins with this project's own collection. The registry API serves only current records, so status transitions are observable only across successive snapshots the pipeline itself captures. Retention keeps `snapshot_runs_to_keep: 3` weekly snapshots (`config/project_config.yml`) — roughly 14 days of depth. Every status-transition metric (`int_trial_status_history`, recruiting entry/exit flags) is therefore blind beyond a fortnight until the project accrues months of runs.

AACT — the Aggregate Analysis of ClinicalTrials.gov database, a public resource of the Clinical Trials Transformation Initiative (CTTI, co-founded by Duke University and the U.S. FDA) — has published dated static copies of the *entire* registry since 2017. If those copies support coarse historical status reconstruction, transition metrics gain 12+ months of depth immediately, which Epic #43's registry-wide scope needs without a year of advance data collection.

The issue raised a specific retention claim to verify: *AACT publishes daily snapshots, keeping every daily snapshot for the current month plus the first-of-month snapshot for earlier months.*

### Verification Performed (2026-11-01)

1. **Live site** ([https://aact.ctti-clinicaltrials.org](https://aact.ctti-clinicaltrials.org)): confirmed AACT is "refreshed daily" from the source registry and offers "Historical snapshots — download the latest database or past versions to compare", plus a live cloud Postgres. The site was redesigned into an account-gated portal: downloads and database credentials require creating an account, and the formerly public static file listing (`/pipe_files`) now returns 404. **Per-file retention on the live 2026 site could not be enumerated without an account.**
2. **Published policy, as archived** (official AACT snapshots page, Wayback Machine capture 2025-04-18): the page split downloads into "Current Month's Daily Static Copies" and a "Monthly Archive of Static Copies" back to 2017-01. Direct enumeration of that listing shows **more** than the claimed policy: complete daily series exist for 2023-09 through 2025-03 (scattered gaps only), and first-of-month-only copies before that, back to 2017.

**Verdict on the claim:** confirmed as a *minimum* and corrected in practice — daily snapshots were retained well beyond "the current month" (an ~18-month daily window at the last public listing), so true monthly granularity applies only to pre-September-2023 months. AACT history comfortably covers the acceptance criterion of 12+ months. The archived observation is point-in-time; the live site's actual retention must be re-enumerated under an account before integration.

### Licensing & Terms (read from the source)

- CTTI publishes AACT for free public use: *"CTTI encourages the use of all materials listed on this site in the pursuit of improving the clinical trials enterprise… available to the public for free. We do ask that you acknowledge the source whenever using or referencing CTTI materials"* (CTTI disclaimer, AACT site). Required acknowledgment is a citation, e.g.: *Aggregate Analysis of ClinicalTrials.gov (AACT) Database. Clinical Trials Transformation Initiative (CTTI). Available at: https://aact.ctti-clinicaltrials.org/ (Accessed: \<date\>).* Data deliverables built on AACT extracts must carry this attribution.
- AACT data is derived from ClinicalTrials.gov (NLM/NIH/FDA). The live portal additionally requires an account and acceptance of its access terms before downloads or cloud queries.
- No personal contact data is involved: the slim history keeps only protocol-level status and date fields (`nct_id`, `overall_status`, start/completion dates, hash) from the AACT `studies` table. The ADR 0065 zero-contact boundary is preserved — the extract schema contains no contact PII by construction.

---

## Decision

**Both sources, by role:**

1. **Adopt AACT as the deep-history backbone for status-transition metrics.** Monthly (first-of-month) AACT snapshots load into a slim `fct_trial_status_history` — one row per `nct_id` per snapshot date with `overall_status`, key registry dates (start, primary completion, completion), and a field-level record hash for change detection. Monthly extracts spanning ≥13 months yield the required 12+ months of transition coverage (demonstrated in `tests/test_aact_status_history.py` over a 13-month span).
2. **Keep this project's own weekly snapshots unchanged** as the freshness and capture-age layer: they feed current-state metrics, remain under project retention control, and act as the tiebreaker on dates both sources observe.
3. **On overlap, own observations win** for same-day status; AACT fills every date own snapshots do not cover. Discrepancies at (near-)matching dates are reported, not silently resolved — `compare_with_own()` classifies each own observation as `match`, `status_mismatch`, or `own_only` against the nearest AACT snapshot within tolerance.
4. **Prototype scope honored:** the loader (`src/analysis/aact_status_history.py`) consumes CSV extracts of the AACT `ctgov.studies` table (one `\copy` command per snapshot date, documented in the module docstring) and never touches the production warehouse. No AACT dumps or extracts are committed; full-dump restores (pg_restore) and cloud-DB access are out of prototype scope.

### Expected discrepancy sources (why mismatches are normal)

- **Cadence:** own weekly snapshots vs AACT monthly boundaries — tolerance-based nearest-date matching bridges this but cannot be exact.
- **Registry reporting lag:** overall status is sponsor-reported; AACT copies and our API captures can disagree for days around a transition even when both are correct.
- **Status-verification:** the registry may revise status retroactively; older AACT copies preserve what was published at that time — which is precisely the historical value.

---

## Limitations

- **Monthly granularity for pre-2023-09 months:** transition *day* is unknown; a transition observed between monthly boundaries is dated to the first-of-month snapshot that reveals it. Metrics must state "status as of the monthly snapshot", not a daily truth. The daily-copy window (Sep 2023–Mar 2025, as observed) softens this for recent periods, but that window rolls forward and must be re-confirmed per download.
- **Account-gated access:** the live AACT portal requires an account (credentials for the cloud Postgres, login for static downloads). Integration work must provision credentials via `.env` (added to `.env.example` at integration time, never committed).
- **Restore cost:** static copies are PostgreSQL dumps (~GB-scale, full registry); loading them locally requires Postgres, and dumps before 2021-11-15 are schema-incompatible (published AACT note). The prototype's CSV-extract path avoids this for the slim history.
- **Retention drift risk:** the verified daily-window listing is from 2025-04; the live 2026 site's per-file inventory is not publicly enumerable. Re-verify at account creation before committing to a specific backfill plan.
- **Hash semantics differ:** own snapshots hash the full registry JSON (`source_json_hash`); the AACT slim history hashes only the kept fields. Hashes are comparable only within a source, never across.

---

## Consequences & Follow-up

- Transition metrics over ≥12 months are feasible immediately upon integration — no waiting period (verified by prototype tests spanning 13 months).
- Integration issues (tracked under Epic #43): provision AACT account + `.env.example` keys; promote the slim history into dbt as an AACT-sourced staging model alongside `int_trial_status_history`; define overlap precedence in the warehouse; update `README` history caveats and `docs/metric_definitions.md` once shipped; cite AACT per the CTTI required format in any published data product.
- Until integration lands, the shipped pipeline behavior is unchanged; this ADR records the decision and the verified evidence behind it.
