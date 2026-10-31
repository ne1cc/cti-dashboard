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
3. **Incremental Refresh:** Use Essie filter `AREA[LastUpdatePostDate]RANGE[<since>,MAX]` with 1-day safety overlap. Upsert incremental silver rows by NCT ID onto the previous full snapshot.
4. **Registry Taxonomy:** Provide `REGISTRY_TAXONOMY` in `normalize_conditions.py` mapping unclassified conditions to `uncategorized`, allowing full-registry silver Parquet generation.
5. **Storage Budget:**
   - Registry bronze JSON: ~4.7 GB for the latest run (`bronze_runs_to_keep: 1`).
   - Weekly incremental bronze: ~70 MB (~9k studies).
   - Registry silver Parquet: ~0.5–1.5 GB retained across `snapshot_runs_to_keep: 3`.

Fallback plan: If NLM ever throttles paginated calls at pageSize 1000, the bulk download (`JSON.zip`) remains documented as the secondary ingestion path.
