# Clinical Trial Access & Recruitment Competition Intelligence — Documentation Index

> [!NOTE]
> This file is a compatibility index pointing to the project's primary entry point in [README.md](README.md) and dedicated specialist documentation in [`docs/`](docs/). Comprehensive project documentation has been consolidated into topic-specific guides to prevent drift and ensure single sources of truth.

## Primary Entry Point

- **[README.md](README.md)** — Project overview, architecture summary, quickstart with fixture warehouse data, core CLI workflows, and repository layout.

## Specialist Documentation

| Topic | Document | Description |
|---|---|---|
| **System Architecture** | [`docs/architecture.md`](docs/architecture.md) | Medallion architecture (Bronze/Silver/Gold), pipeline flow, layer contracts, grain definitions, and technology choices |
| **Data Dictionary** | [`docs/data_dictionary.md`](docs/data_dictionary.md) | Entity specifications for Silver Parquet files and Gold dbt models (dimensions, facts, marts) |
| **Metric Definitions** | [`docs/metric_definitions.md`](docs/metric_definitions.md) | Grains and formulas for the raw registry segment metrics, and the ADR 0077 rationale for removing the composite score and priority bands |
| **Source & Ingestion** | [`docs/source_documentation.md`](docs/source_documentation.md) | ClinicalTrials.gov API v2 protocol, rate limiting, pagination, manifest format, and quarantine handling |
| **Data Quality Framework** | [`docs/data_quality_framework.md`](docs/data_quality_framework.md) | Ingestion validation, Silver transformations, dbt schema/data tests, reconciliation gates, and CI checks |
| **Dashboard Specification** | [`docs/dashboard_spec.md`](docs/dashboard_spec.md) | Streamlit dashboard layout, page structure, UI components, caching strategy, and filters |
| **Competition Audit** | [`docs/competition_audit.md`](docs/competition_audit.md) | Competition audit panel details, drill-through definitions, audit JSON schema, and limitations |
| **Clinical Guardrails** | [`docs/clinical_interpretation_guardrails.md`](docs/clinical_interpretation_guardrails.md) | Rules for interpreting public registry signals without overstating operational or clinical claims |
| **Assumptions & Limitations** | [`docs/assumptions_and_limitations.md`](docs/assumptions_and_limitations.md) | Source data biases, coverage boundaries, and analytical assumptions |
| **Business Case (archived)** | [`docs/archive/business_case.md`](docs/archive/business_case.md) | Archived exploratory analyst persona and business case hypothesis (ADR 0082) |
| **Competitive Positioning** | [`docs/competitive_positioning.md`](docs/competitive_positioning.md) | Strategic positioning as an open, auditable feasibility workbench vs. commercial intelligence platforms |
| **Production Deployment** | [`docs/DEPLOY_FLY.md`](docs/DEPLOY_FLY.md) | Fly.io container deployment, memory envelopes, volume persistence, and operation runbooks |
| **Study Guide** | [`docs/study_guide.md`](docs/study_guide.md) | Architectural interview talking points, system design walkthroughs, and topic guides |
| **Executive Memo Template** | [`docs/executive_memo_template.md`](docs/executive_memo_template.md) | Template for briefing clinical operations leadership on trial feasibility findings |

## Historical Archives

Historical notes and dated build milestones are retained for provenance:

- [`docs/archive/development_log.md`](docs/archive/development_log.md) — Step-by-step development record across initial project build phases.
- [`docs/archive/platform_updates_2026-09-04.md`](docs/archive/platform_updates_2026-09-04.md) — Historical milestone notes.
