# Direction progression: how the project's goals moved

Status: working record for the scope re-mapping (2026-10-31). Sources: git history
(267 commits, 2026-07-24 → 2026-10-30), GitHub issues #40–#114, ADRs 0061/0065/0077/0082,
and the design conversation that preceded this audit. Every claim below was checked
against the repo; where the conversation and the repo disagree, the repo wins and the
difference is listed in [Conversation vs repo](#conversation-vs-repo).

## One-line arc

Resume-grade data-engineering build for a **hypothetical feasibility analyst** →
audit finds the analyst case unvalidated and the metrics misleading → **open access
platform for patients, caregivers and analysts** (decided on paper, one backend
slice built, no patient screen yet).

## Phases

| # | Dates | Who it was for | What was built | Evidence it left |
|---|---|---|---|---|
| 0 | 2026-07-24 | Director of Clinical Operations / Head of Site Feasibility | Bronze→silver→gold pipeline, dbt/DuckDB marts, Streamlit, a ranked "Feasibility Review Priority Queue" for Alzheimer's/US | Initial README: "Clinical Trial Access & Recruitment Competition Intelligence"; guardrails already say "not a recruitment forecast" |
| 1 | 2026-08-21 | Same, now pitched | Executive business case, stakeholder brief | `docs/archive/business_case.md` (assumed spreadsheet workflow, time-saved KPI, ROI calculator) |
| 2 | 2026-09-04 → 09-07 | Same, "interviewable" portfolio | Fly.io deploy, Dagster, explainable score breakdown, trial-similarity explorer, full-catalog bronze, pluggable indication profiles, profile-scoped dbt grain, CI depth | Commit log; `docs/competitive_positioning.md` positions against Citeline/IQVIA/H1 as a "free feasibility workbench" |
| 3 | 2026-09-11 → 10-05 | Same | In-app guidance and "decision playbooks", competition audit drill-through (`competition-audit-v2`), national Phase 3 Alzheimer's case study, 10 more indication profiles | `docs/competition_audit.md`, `docs/feasibility_case_study.md`; 2026-10-04 "frame CTI around feasibility research decision" |
| 4 | 2026-10-07 → 10-18 | Same | Outage (12 profiles filled the volume; 28 GB DuckDB temp from the similarity mart), production cut back to `adrd,oncology_nsclc`; UI polish (themes, tables, navigation) | `fly.toml` comments; issue #55 |
| 5 | 2026-10-23 | **Re-audit** | No product code. Audit of commit `b93a5d9` → roadmap #40 and 7 epics (#41–#47), 4 `decision-needed` issues | Issue #40 findings table |
| 6 | 2026-10-24 → 10-30 | Patients, caregivers, public; analysts demoted | ADR 0061 (dual front end), ADR 0065 (no personal contacts), OpenAPI contract, eligibility fields in gold, FastAPI read API with warehouse and live-registry backends, offline Census Gazetteer location search (ZIP, radius), composite score removed (ADR 0077), analyst persona demoted to hypothesis (ADR 0082) | Commits `535d040` … `cdd9c2b`; README now headlines "open, multi-stakeholder" |

### What the 2026-10-23 audit said was wrong (issue #40)

1. Headline KPI counted each facility once per retained snapshot (#48).
2. Priority score had no coherent direction, rewarded concentration, carried an inert "data confidence" term (#77, #78).
3. Raw-count state map mostly mapped population (#51).
4. Patient-relevant fields were dropped or captured and never shown (#62, #64, #66).
5. Central contacts blanket-excluded without a policy (#65).
6. Hand-built YAML taxonomies while the API's MeSH data went unused (#52).
7. Did not generalize: ADRD-coupled UI, two profiles in prod, storage-bound (#49, #53, #54, #57).
8. Deploy not gated on CI; coverage skipped the dashboard; docs drifted (#83–#85).
9. Caveat and playbook text rivalled the analysis (#50, #81).

### Decisions already taken (do not reopen without new evidence)

| Decision | Source | Effect |
|---|---|---|
| Composite priority score and bands removed; raw sortable metrics only | ADR 0077, #77 | Scoring is no longer a product claim |
| Analyst business case is an unvalidated hypothesis, archived | ADR 0082, #82 | README headlines open discovery; ROI and time-saved claims are not product claims |
| Patient app is server-rendered FastAPI + Jinja2, analysts keep Streamlit | ADR 0061, #61 | **Decided, not built** |
| Official registry link plus site list; zero personal contact data stored or shown | ADR 0065, #65 | Enforced by an empty `stg_trial_contacts`, `scrub_contact_pii`, `tests/test_contact_policy.py` |
| No predictions of enrollment, no judgments of named sites/sponsors, no ROI-as-observed | `docs/clinical_interpretation_guardrails.md` | Stays |

## The design conversation, in order

1. **Phenomenon** — "sponsor who pays most wins": later sponsors outbid signed site contracts.
2. **Bid defense** — how a CRO wins the sponsor award (a procurement step).
3. **Players and priority scheme** — scarce eligible patients and investigator time outrank CRO fee.
4. **Reframe: "it's CRO again"** — the real contest is sponsor vs sponsor for site capacity, not CRO vs CRO.
5. **Books** — read for the contest the registry only shadows.
6. **Next priorities** — falsify the density proxy; tighter "same patient" grain; staleness as a finding; population adjustment; conservative site overlap; refuse site-performance features; do not add CRO awards or bid outcomes.
7. **Why the test and the refusal are required** — a number that looks like a decision, over data that records only listings; ecological fallacy.
8. **The original plan** — one analyst, reproducible shortlist, stop before picking a winner.
9. **Patient pivot** — two products on one warehouse; harm profile changed from "wasted analyst hour" to "caregiver reads a colored map as advice".

Steps 1–7 argue for a **competition-intelligence** direction (analyst/sponsor side).
Steps 8–9 and the repo's recent work move toward **patient access**. The two pull in
different directions and the project has not yet chosen between them or defined how
they share one warehouse. That choice is the main open decision.

## Conversation vs repo

| Claim in the conversation | What the repo shows |
|---|---|
| Patient app has server-rendered pages `/`, `/trials`, `/trials/{nct_id}` | **Not built.** No Jinja2 dependency, no templates, no HTML routes. `src/api/app.py` serves JSON only: `/v1/trials`, `/v1/trials/{nct_id}`, `/healthz`, `/readyz`. ADR 0061 describes these pages as the target. |
| Patient search is a live product | The API is not deployed. `fly.toml` routes port 8501 (Streamlit); the `api` image stage exists but no CI job builds or deploys it. The live demo is still the analyst dashboard. |
| "Success was preparation time…" | Matches `docs/archive/business_case.md` — and none of it was ever measured. |
| Dashboard spec still names a feasibility director | True: `docs/dashboard_spec.md` line 3. |
| Contact fields dropped on ingest, contact table forced empty | True (`flatten_studies.py`, `stg_trial_contacts.sql`). |
| Recruiting-density proxy has been tested against later status changes | **Never done.** No code or doc tests it. Snapshot history exists (weekly), so the test is feasible. |

## Book evidence: what holds up

Books are useful for framing; they are not proof the proxy works. I could not read the
books themselves. I checked what is publicly documented about each.

| Book | Use it for | Caution found |
|---|---|---|
| Getz, *The Gift of Participation* | Patient-facing language, what participants need to know | It is a plain-language guide for prospective participants (CISCRP-published), **not** the source of the protocol-burden / site-load findings the conversation attributed to it. Those come from Tufts CSDD papers by Getz. |
| Anderson, *A Guide to Patient Recruitment* | Recruitment workflow map | Dated (c. 2001); treat as workflow vocabulary only |
| Pisano, *Science Business* | R&D financing and integration economics of biotech | Its argument is about biotech business models and uncertainty. The claim that it explains *duplicate trials in the same indication* is not supported by what I found. Data ends c. 2004. |
| Werth, *The Billion-Dollar Molecule* | Narrative of racing rivals | Narrative, one company; not evidence |
| Friedman/Furberg/DeMets, *Fundamentals of Clinical Trials* | Phase, eligibility and recruitment definitions | Textbook; supports definitions, not market claims |
| Goldacre, *Bad Pharma* | Registry-reporting caution | Supports the "registry ≠ performance" guardrail |

Empirical figure the conversation used: roughly 11% of sites enroll no patient. This
traces to Tufts CSDD (Getz), 2012–2016 reporting. The base differs by source (all
selected sites vs activated sites vs a roundtable sample) and 37–48% under-enrolling
depends on whether zero-enrollers are counted. Cite it with its base or not at all.

## Inputs for the next scope decision

1. **Pick the primary user, in writing.** Patient/caregiver finder, analyst/sponsor competition view, or both with a stated priority. The audit evidence supports neither the analyst ROI nor patient demand; the #70 five-person usability study and #82 interviews are the only planned validation and neither has run.
2. **If patient-first:** build the ADR 0061 HTML front end and deploy the API; today there is nothing a patient can open.
3. **If competition-first:** run the proxy-falsification test on existing snapshots before adding profiles, ACS data or a warehouse port. It needs no new data source.
4. **Either way:** keep the refusals (no site grades, no enrollment forecasts, no personal contacts) as hard constraints; they were the one point every phase agreed on.
5. **Cut before expanding:** the ROI calculator, ADRD-only playbook copy and 12 per-disease profiles (production runs two) are the surface area that has not earned its keep (#50, #49, #54).
