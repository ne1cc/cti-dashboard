# CTI Competitive Positioning

## How to use this document

This is competitive and market context, not a commitment list. It exists
to answer one question: given that Citeline, IQVIA, and H1 sell
proprietary clinical-trial intelligence, what can CTI Dashboard credibly
and usefully offer using only free, public ClinicalTrials.gov data — and
to whom?

The backlog near the end is a set of loose ideas that fit that niche, not
a plan. Before building any of them, pick one and run it through this
project's normal process: brainstorm the specific feature, write a design
spec to `docs/superpowers/specs/`, then a task-by-task plan to
`docs/superpowers/plans/`. Do not try to build the whole list at once.

---

Your dashboard has moved beyond a portfolio prototype: it is now a live,
containerized ClinicalTrials.gov intelligence product with a weekly
refresh loop, dbt quality gates, and seven decision-oriented dashboard
pages. The best next step is **not** to imitate Citeline, IQVIA, or H1's
proprietary data; it is to deepen the **free, explainable feasibility
workflow** that public registry data can genuinely support.

## TL;DR

- **Concept:** Position CTI as an open, auditable *trial feasibility and
  competitive-pressure workbench* built from ClinicalTrials.gov — not as a
  commercial site-performance or patient-access database.
- **Use case:** Let a clinical strategy analyst answer: "Where is
  recruitment competition rising, which studies are comparable, and where
  are the most defensible opportunities to investigate?"
- **Key takeaway:** The highest-value next work is **change intelligence,
  protocol comparability, explainable scores, data-confidence signals, and
  decision-ready exports** — all possible with the current free data
  foundation, and all things the proprietary platforms either don't do
  transparently or don't do at all.

## Current Product Assessment

The repository (`ne1cc/cti-dashboard`) is publicly deployed. The
deployment pipeline, first-boot warehouse build, weekly refresh process,
and automatic Fly deployment on pushes to `main` are live. The app runs
one always-on process because the background refresh loop needs to remain
active, persists the warehouse on a mounted volume, and health-checks the
Streamlit service.

The existing analytical foundation is already strong:

| Layer | Current implementation | Why it matters |
|---|---|---|
| Ingestion | ClinicalTrials.gov API ingestion using `requests`, after resolving an API edge-protection issue with `httpx` | Shows real-world source reliability troubleshooting and robust client design |
| Warehouse | DuckDB with Bronze → Silver → Gold/dbt structure | Gives reproducibility, fast local analytics, and transparent transformations |
| Quality | 139 dbt tests and a 306-test pytest suite in the latest pipeline run (measured 2026-09-07 UTC with `uv run dbt parse --project-dir dbt_clinical_trials --profiles-dir dbt_clinical_trials` plus a `resource_type` count over `dbt_clinical_trials/target/manifest.json`, and `uv run pytest --collect-only`; `tests/test_docs_describe_current_paths.py` compares this row to those commands on every run) | Signals production-minded data quality and test discipline |
| History | Weekly snapshot history through `fct_trial_snapshot` | Enables time-series and change-detection features, far more valuable than static registry search |
| Intelligence marts | Trial activity, recruiting competition, site overlap, geography trends, data reliability, feasibility priority queue | Covers the baseline feasibility workflow |
| User experience | Priority Queue, Competition Landscape, Geography Trends, Site Overlap, Sponsor Landscape, Data Reliability, Trial Explorer, Trial Similarity | A coherent decision journey rather than disconnected charts |
| Deployment | Docker, Fly configuration, GitHub Actions auto-deploy, live demo | Makes the project interviewable and usable by others, not just locally runnable |

### What is already comparable

The current feature set maps most closely to the **early feasibility /
competitive-intelligence** parts of Citeline Trialtrove and H1 Site
Universe — and naming that mapping honestly is the core of defining CTI's
niche:

| Enterprise concept | Current free equivalent | Important limitation |
|---|---|---|
| Competitive trial landscape | `mart_recruiting_competition` and Competition Landscape | Registry data will not represent every commercial intelligence signal |
| Geographic trial activity | `mart_condition_geography_trends` and Geography Trends | Site location does not prove recruitment capacity or patient availability |
| Study/site overlap | `mart_site_overlap` and Site Overlap | Co-location signals competition, not verified site performance |
| Trial prioritization | `mart_feasibility_priority_queue` and Priority Queue | Ranking is an analytical hypothesis, not an operational recommendation |
| Sponsor landscape | `dim_sponsor`, bridge table, Sponsor Landscape | Sponsor normalization is difficult with registry-entered organization names |
| Data confidence | `mart_data_reliability` and Data Reliability | Reliability reflects registry completeness and timeliness, not scientific validity |

That scope is valuable on its own. It answers the defensible question:
**"What can we infer from public trial registrations, and how confident
should we be in that inference?"** — a question Citeline, IQVIA, and H1
answer with proprietary data CTI will never have, and doesn't need to
compete on.

## Product Positioning

Use this concise project description:

> **CTI Dashboard is an open, reproducible clinical-trial feasibility and
> competition-intelligence workbench. It transforms public
> ClinicalTrials.gov records and weekly snapshots into explainable signals
> for trial landscape research, recruitment-competition monitoring,
> geographic saturation analysis, and data-confidence-aware
> prioritization.**

That communicates ambition without making unsupported claims.

### Terms to use carefully

| Term | Safe usage in CTI | Avoid claiming |
|---|---|---|
| Site selection | "Registry-informed site research" or "site-location overlap analysis" | "Optimal site selection" or proven site performance |
| Recruitment | "Recruitment competition signal" or "planned enrollment burden proxy" | Actual recruitment rates or patient availability |
| Feasibility | "Public-registry feasibility research" | End-to-end operational feasibility |
| Risk score | "Explainable analytical priority signal" | Clinical, regulatory, or operational risk prediction |
| Patient access | "Not available from registry-only data" | Patient identification or patient-level recruitment potential |
| Benchmark | "Registry-derived descriptive benchmark" | Validated commercial performance benchmark |

## Feature Backlog

Loose ideas that fit the niche above, grouped into rough priority tiers.
Each one needs its own brainstorm and spec before implementation — this
is context for that conversation, not a spec itself.

### Tier 1 — builds directly on what already exists

- **Trial Change Intelligence.** A static dashboard tells users what
  exists; this would tell them what changed since last week — status
  transitions, enrollment changes, sites added or removed. The weekly
  snapshot history (`fct_trial_snapshot`) already makes this possible and
  nothing else in the free-tooling space does it well.
- **Explainable Feasibility Scorecard.** The priority queue's score is
  only as useful as its explanation. Surfacing each score component,
  weight, and data-confidence flag per trial (using the existing
  `feasibility_score_weights` seed) turns a ranking into an auditable
  decision tool instead of a black box.
- **Priority Queue filtering and export.** Add condition/phase/state/
  sponsor/freshness filters and a "download the filtered view" option —
  low effort, direct usability win for an analyst doing real research.

### Tier 2 — meaningful differentiators, more design work

- **Protocol Similarity Explorer.** "Same condition" is often too broad
  to mean "competing." Ranking genuinely comparable trials by condition,
  phase, geography, intervention type, and eligibility overlap would
  mirror what Citeline sells as trial benchmarking, using only registry
  fields and full transparency about *why* two trials are comparable.
- **Geographic Saturation view.** Extends the existing geography trends
  mart with growth/momentum, not just current volume — letting a user
  distinguish an emerging market from an already-saturated one.
- **Sponsor Competitive Momentum.** Extends the sponsor landscape to show
  whether sponsors are expanding, concentrating, or withdrawing in a
  market over time, not just a static count.

### Tier 3 — later, once the above prove out

- **Saved research scenarios** (shareable filter/weight state via URL or
  session state, no auth required).
- **Decision brief export** (a downloadable report — filters, caveats,
  ranked studies, methodology — with every line traceable to a metric,
  never generative text asserting something uncomputed).
- **Public metric documentation** for every KPI (definition, grain,
  lineage, and validity caveats) — as much a positioning asset as a
  technical one.

## Trade-offs

- **Coverage:** Free ClinicalTrials.gov data can give excellent
  registry-based competition and study-design intelligence, but it cannot
  validate real site throughput, patient availability, investigator
  relationships, claims-derived incidence, CTMS workflow status, or
  confidential enrollment performance. Do not imply otherwise.
- **Compute:** Any trial-pair comparison work (e.g. Protocol Similarity)
  can become expensive quickly if done naively — pre-filter candidates
  and avoid full pairwise computation at request time.
- **Maintenance:** Anything built on change detection needs stable
  normalization. If condition, sponsor, intervention, or location parsing
  changes, version the logic so a true source change is never confused
  with a change introduced by the transformation code itself.

## Staying honest: two questions for every new feature

1. Can every score, label, and recommendation-like statement be traced to
   a dbt model, source field, formula, snapshot date, and data-quality
   rule?
2. Does the page visibly state what public registry data can show, what
   it cannot show, and when the displayed evidence was last refreshed?

## External references consulted

- [IQVIA — Clinical Data Analytics Solutions](https://www.iqvia.com/solutions/technologies/orchestrated-clinical-trials/clinical-data-analytics-solutions/clinical-analytics)
- [H1 — Clinical](https://h1.com/clinical/)
- [Citeline — Plus](https://www.citeline.com/en/plus)
- [Citeline — Trialtrove](https://www.citeline.com/en/products-services/clinical/trialtrove)
