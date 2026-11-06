# Dashboard Specification

Audience: analysts doing landscape screening (a hypothesis, not a validated user; see ADR 0082).
Stack: Streamlit multipage over read-only DuckDB (`make dashboard`).
Every page renders the shared disclaimer banner via
`components/guardrails.page_setup()` (test-enforced) and a guarded footer.

## Shared components (`dashboard/components/`)
| Module | Responsibility |
|---|---|
| data.py | cached read-only warehouse access; every reader takes the selected `Preset` first and scopes by the preset's owning profile and `registry_condition_key`; `require_warehouse()` stops with build instructions if missing |
| guardrails.py | disclaimer banner, snapshot-proxy note, footer |
| filters.py | condition/state/phase sidebar multiselects with row-count caption |
| profile.py | sidebar "Indication preset" selector (`render_preset_selector()` returns the `Preset`) with a "Default filters" caption; options come from `src/presets.py` (`config/profiles/*.yml`), not a warehouse query, so it renders before the first build |
| navigation.py | `st.navigation` registry: pages grouped Compare / Inspect / Verify; `app.py` only runs it. Page URLs match the former file-based paths |
| formatting.py | display labels for phase (from `dbt_clinical_trials/seeds/phase_mapping.csv`), number and percentage formatting, and snapshot date |

## Pages

### Overview (`overview.py`)
KPIs (trials tracked, recruiting, states, facilities), snapshot date and
count, top-10 segment preview with display labels, link to
the full queue, "Where to look next" cards built from the navigation
sections, and three separately named metric audits (see [Competition Audit](competition_audit.md)).

### 1 · Registry Segments
Segment-level activity metrics, condition/state/phase multiselects with
dynamic row-count reconciliation caption, history warning when snapshot
history is too short for recruiting growth, interactive selectable raw metrics table sorted
by active recruiting trials (click a row to inspect segment details), CSV
export button with full decision-brief columns and embedded guardrail
notes, horizontal bar chart of active recruiting trials across top 15
segments, interpretation note.

### 2 · Competition Landscape
Latest-snapshot segments: density vs sponsor-HHI scatter (bubble = listed
sites, color = signal band with explicit "relative percentile cuts"
caption) + sortable segment table.

### 3 · Geography Trends
County access map (Issue #73, `dashboard/components/county_access.py`) → top-states
table, monthly trend line — or an honest notice showing how many snapshot
months exist when a series is not yet possible.

The county map reads `mart_trial_access_by_geo` for the selected preset's
profile (the mart is keyed on MeSH node, so it narrows to the profile only, #149).
Controls: condition, optional "Compare with" condition, 25/50/100 miles, RUCA and SVI
filters, and a switch between residents within N miles and distance to the nearest
open site. Always visible: the "what this means / does not mean" box, the denominator,
the data date (latest capture), site-coordinate coverage and the methodology line. The
box, the caption, the methodology line and the CSV header also say the map covers only
the seeded counties (count and states are read from the rows); other counties have no
data, which is not zero.
Counties under 5,000 residents, or conditions whose open sites have no usable
coordinates, are grey "insufficient data", excluded from every total and never zero.
Colour keys: Viridis for counties with an open site in range; white for "no open site
within N mi" (distance view: "100+ mi"), apart from grey "insufficient data", so a small
in-range county never looks like a county with no site.
Beside the map: a table of the same facts as the hover (RUCA group, SVI quartile,
"within N mi", nearest site as text such as "no site within 100 mi"; tooltips are not
keyboard reachable),
a CSV with a methodology header, and a table of rural (small-town, rural) counties with
no open site within 100 miles for either compared condition. Colours are Viridis.
County shapes: `dashboard/static/us_counties.geojson` (Census county boundaries via the
plotly/datasets FIPS GeoJSON, coordinates rounded to 3 decimals). The state-level
per-100k / raw-count toggle from #51 is gone; the top-states table keeps both columns.

### 4 · Site Overlap
Multi-trial facility table (default filter on), states ranked by
multi-trial facilities, mandatory caption: best-effort matching, not
workload/performance.

### 5 · Sponsor Landscape
Top lead sponsors by recruiting listings (colored by registry sponsor
class), full table, "not market share" caption.

### 6 · Data Reliability
Run reliability table (success + partial runs shown; only success feeds
analytics), latest-run confidence metrics, data reconciliation status,
and known limitations.

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
Landscape and Registry Segments pages) not itself a competition or recruitment
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
