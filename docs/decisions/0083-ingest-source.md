# ADR 0083: Primary Ingestion Source for Registry-Wide Build

- **Status:** Accepted (Approved Plan)
- **Deciders:** Repository Owner / Architecture Review
- **Date:** 2026-10-30
- **Technical Story:** Issue #53 (Part of Epic #43 — Generalize to the whole registry)

---

## Context

Prior to Issue #53, `src/ingest/ctg_client.py::build_params` sent no `fields` parameter to the ClinicalTrials.gov API v2 (`GET /api/v2/studies`). Consequently, bronze ingestion retained full study JSON records (~17.3 KB/study average). At registry scale (~610k studies), a full snapshot amounted to ~10.5 GB of raw JSON per run. Furthermore, the `full_catalog` profile was bronze-only because no taxonomy existed to map non-ADRD indications, and ingestion required an explicit indication profile.

Issue #53 scoped a spike comparing three potential ingestion sources for a full registry build:
1. Paginated API (`GET /api/v2/studies`) with `fields` projection
2. ClinicalTrials.gov Bulk Download (`AllPublicXML.zip` / `JSON.zip`)
3. AACT (Aggregate Analysis of ClinicalTrials.gov by CTTI)

---

## Comparison Matrix

| Evaluation Dimension | 1. Paginated API (Selected) | 2. Registry Bulk Download | 3. AACT Relational Dump |
|---|---|---|---|
| **Data Freshness** | Real-time (live API v2, updated daily by NLM) | Daily (bulk archive generated nightly) | Delayed (daily/weekly batch refresh by CTTI) |
| **Field Projection** | Supported via `fields` parameter (~7.7 KB/study) | Not supported; full record zip only (~10.5 GB) | Tabular relational export, not raw JSON |
| **Storage per Full Run** | ~4.7 GB bronze JSON (projected) | ~10.5 GB uncompressed JSON | ~5–8 GB relational CSVs/SQL dump |
| **Incremental Refresh** | Supported natively via Essie date range filter `AREA[LastUpdatePostDate]RANGE[<since>,MAX]` (~70 MB/week) | Not supported; full re-download required every run | Incremental changelogs complex; no raw API hash |
| **Schema Drift & Provenance** | Preserves `source_json_hash`, per-page metadata, and existing `schema_drift.py` | Preserves raw JSON, but requires custom archive extraction pipeline | Breaks raw JSON hashing, provenance, and drift detection |
| **Terms of Use & Access** | Public API v2, no authentication required | Public archive, no authentication required | Third-party terms, account registration required |
| **Pipeline Compatibility** | 100% compatible with existing `CTGClient`, retry/backoff, and pagination | Requires new zip download/extraction adapter | Requires secondary ETL and database sync |

---

## Decision

The platform adopts **Option 1: Paginated API with `fields` Projection**:
1. **Field Projection:** Pass minimal fields covering search, detail, geography, and results demographics:
   - `protocolSection.identificationModule`
   - `protocolSection.statusModule`
   - `protocolSection.designModule`
   - `protocolSection.sponsorCollaboratorsModule`
   - `protocolSection.conditionsModule`
   - `protocolSection.armsInterventionsModule`
   - `protocolSection.eligibilityModule`
   - `protocolSection.contactsLocationsModule`
   - `protocolSection.outcomesModule`
   - `resultsSection.participantFlowModule`
   - `resultsSection.baselineCharacteristicsModule`
   - `hasResults`
2. **Page Size & Pacing:** Set `pageSize: 1000` (API-coerced maximum) and `min_request_interval_seconds: 1.2` (~50 req/min, complying with community consensus while backing off on 429s).
3. **Incremental Refresh:** Use Essie filter `AREA[LastUpdatePostDate]RANGE[<since>,MAX]` with 1-day safety overlap. Upsert incremental silver rows by NCT ID onto the previous completed snapshot.
4. **Registry Taxonomy:** Provide `REGISTRY_TAXONOMY` in `normalize_conditions.py` mapping unclassified conditions to `uncategorized`, allowing full-registry silver Parquet generation.
5. **Measured Benchmark & Sizing (Verified live 2026-11-02 outside data/):**
   - **Bronze Ingestion:**
     - Live 2,000-study probe (2 pages x 1,000 studies): 16,992,233 bytes (16.21 MB).
     - Average size per study: 8,496 bytes (~8.30 KB).
     - Average page transfer time: 1.27s (combined with 1.2s pacing = ~2.47s/page).
     - Full registry projection (~610k studies, ~610 pages): ~5.18 GB bronze JSON, ~25.1 minutes total fetch time.
     - Weekly incremental (~9,000 studies, ~9 pages): ~76.5 MB bronze JSON, ~22 seconds fetch time.
   - **Silver Transformation:**
     - 2,000 studies transformed in 1.04s (~1,923 studies/second).
     - Full registry silver build time: ~5.3 minutes (~317s).
     - Memory footprint: peak RSS 326.4 MB with streaming batch processing (`iter_batches`).
     - Total silver size for 2,000 studies: 5,478,465 bytes (5.22 MB) = 2,739 bytes (~2.67 KB) per study.
       - `silver_trials`: 2.13 MB (2,000 rows)
       - `silver_trial_outcomes`: 1.64 MB (12,627 rows)
       - `silver_trial_locations`: 0.64 MB (8,832 rows)
       - `silver_trial_interventions`: 0.58 MB (3,386 rows)
       - `silver_trial_conditions`: 0.25 MB (3,570 rows)
       - `silver_trial_sponsors`: 0.25 MB (3,044 rows)
     - Full registry silver size: ~1.67 GB per complete snapshot.
   - **Storage Budget & Retention on Volume:**
     - Retention policy retains 1 bronze snapshot (`bronze_runs_to_keep: 1`) and 3 silver snapshots (`snapshot_runs_to_keep: 3`).
     - Each incremental run produces a complete silver snapshot copy carrying forward unchanged studies: 3 snapshots x ~1.67 GB = ~5.01 GB total silver.
     - Total steady-state storage: 5.18 GB bronze + 5.01 GB silver ≈ 10.19 GB.
     - Transient peak during new full run ingestion: ~15.37 GB (previous bronze retained until `prune-data` runs).

Fallback plan: If NLM ever throttles paginated calls at pageSize 1000, the bulk download (`JSON.zip`) remains documented as the secondary ingestion path.
