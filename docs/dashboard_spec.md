# Dashboard Specification

Audience: Director of Clinical Operations / Head of Site Feasibility.
Stack: Streamlit multipage over read-only DuckDB (`make dashboard`).
Every page renders the shared disclaimer banner via
`components/guardrails.page_setup()` (test-enforced) and a guarded footer.

## Shared components (`dashboard/components/`)
| Module | Responsibility |
|---|---|
| data.py | cached read-only warehouse access; `require_warehouse()` stops with build instructions if missing |
| guardrails.py | disclaimer banner, snapshot-proxy note, footer |
| filters.py | condition/state/phase sidebar multiselects with row-count caption |
| profile.py | sidebar indication-profile selector; options come from `get_registry().refreshable()`, not a warehouse query, so it renders before the first build |

## Pages

### Overview (`app.py`)
KPIs (trials tracked, recruiting, states, facilities), snapshot count,
top-10 queue preview, "how to read this dashboard". Link to the full
queue.

### 1 · Priority Queue
Band KPIs (priority_review / review / watch), priority band sidebar
multiselect with dynamic row-count reconciliation caption, proxy warning
when growth uses the registry-date fallback, interactive selectable ranked
table (click a row to inspect its exact weighted score breakdown), CSV
export button with full decision-brief columns and embedded guardrail
notes, horizontal stacked bar of normalized *unweighted* components for
the top 15, interpretation note.

### 2 · Competition Landscape
Latest-snapshot segments: density vs sponsor-HHI scatter (bubble = listed
sites, color = signal band with explicit "relative percentile cuts"
caption) + sortable segment table.

### 3 · Geography Trends
Condition-group selector → USA choropleth of recruiting listings by
state, top-states table, monthly trend line — or an honest notice showing
how many snapshot months exist when a series is not yet possible.

### 4 · Site Overlap
Multi-trial facility table (default filter on), states ranked by
multi-trial facilities, mandatory caption: best-effort matching, not
workload/performance.

### 5 · Sponsor Landscape
Top lead sponsors by recruiting listings (colored by registry sponsor
class), full table, "not market share" caption.

### 6 · Data Reliability & Assumptions
Run reliability table (success + partial runs shown; only success feeds
analytics), latest-run confidence metrics, known limitations, and the
**illustrative scenario explorer**: sliders adjust session-only copies of
`config/roi_assumptions.yml`; the disclaimer always renders above results
and the file is never written.

### 7 · Trial Explorer
Individual registry records with status/phase filters and free-text
search (title, sponsor, NCT ID); each row links to the authoritative
public record via `registry_url`
(`https://clinicaltrials.gov/study/<NCT_ID>`, a `dim_trial` column).
Caption states rows reflect this project's latest snapshot and that
listed enrollment is the sponsor-reported plan, not actual accrual.

### 8 · Trial Similarity
Deterministic protocol comparability for one index trial. A banner sets the
scope first: shared phase, geography, intervention type, study design and
eligibility — **not** clinical equivalence, and (unlike the Competition
Landscape and Priority Queue pages) not itself a competition or recruitment
signal. Free-text search over brief title / NCT ID narrows the profile's trial
set, and a selectbox offers the first 50 matches labelled
`NCT — title [indication_profile_id]`.

`data.trial_similarity(profile_id, nct_id)` reads `mart_trial_similarity` for
the selected profile and renders rank, matched NCT ID, indication, brief title,
registry link, `similarity_score` (4 dp) and `similarity_explanation`. Selecting
a row re-renders with the seven-factor breakdown — match, weight and weighted
contribution per factor — plus a "Weighted total" metric whose help notes that
contributions are rounded before display, so their sum can differ in the last
decimal. A search that matches nothing, and an index trial with no comparable
trial in the warehouse, each say so instead of rendering an empty table.

## Non-functional rules
- Warehouse opened `read_only=True`; the dashboard can never mutate data.
- Queries cached (`st.cache_data`, TTL 600s); connection cached per
  process.
- No contact/investigator data exists to display.
- Smoke coverage: `tests/test_dashboard_smoke.py` runs all 8 scripts via
  Streamlit `AppTest` (auto-skips without a warehouse).
