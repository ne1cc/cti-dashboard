# Assumptions and Limitations

## Source-inherited limitations
1. **Current-records-only API.** History exists only from this project's
   own snapshot cadence; transition metrics are zero until snapshots
   accrue. Registry-date proxies are labeled wherever used.
2. **Registry lag and inconsistency.** Sponsors update listings on their
   own schedule; statuses, enrollment counts, and dates may be stale.
3. **Partial dates.** Registry dates may be YYYY or YYYY-MM; parsed
   leniently with `*_raw` preserved. Day-level precision is not implied.
4. **Free-text facilities.** Facility names are not stable identifiers.
   Matching is normalized-text best-effort; overlap metrics inherit this
   fuzziness. Missing facility names exclude facility identity
   analysis but retain usable state geography.

## Modeling assumptions
5. **Deterministic taxonomy.** Condition grouping is a version-controlled
   YAML keyword taxonomy (first-match-wins, confidence-tagged); no runtime
   LLM or external ontology. Low-confidence mappings are counted in
   `mart_data_reliability`, not hidden.
6. **Raw recruiting-count proxy.** Recruiting-listing count stands in for competition
   pressure. It is not population-adjusted (ACS layer is on the roadmap)
   and does not observe actual enrollment competition.
7. **Segment grain.** Trials without a usable U.S. location are excluded
   from segment marts (U.S.-scope MVP); original rows remain inspectable in audit marts; bronze availability follows retention.
8. **Score normalization.** Min-max within the scored population makes the
   score *relative to the current snapshot's segments*; scores are not
   comparable across projects or absolute over time.
9. **Bands.** Competition low/moderate/elevated bands use relative
   percentile cuts; priority watch/review/priority_review bands use fixed
   score thresholds. Neither is a validated risk tier.

## Scenario-model assumptions
10. Every ROI figure is arithmetic over user-editable assumptions in
    `config/roi_assumptions.yml` (costs, shares, cycle sizes). Nothing is
    observed; the disclaimer is embedded in every output object.

## Operational limitations
11. Profile-scoped indications (ADRD and NSCLC); additional
    areas require configuration changes and taxonomy review.
12. Weekly cadence is recommended, not enforced; gaps widen transition
    windows.
13. Local-first DuckDB warehouse; multi-user concurrency and cloud
    orchestration are roadmap items.
14. **Portfolio demonstration.** Real feasibility decisions require
    qualified clinical-operations review and additional data sources.

## Audit interpretation and retention

15. Snapshot IDs identify captured successful ingestion runs, not historic registry
    versions before collection began. Daily metrics choose the latest per-study/date
    observation; the audit records the actual contributing runs, including several
    runs on one date. Active rule hashes do not establish unrecorded historical
    taxonomy/geography configuration identity.
16. Pipeline age, public posted-update age, and verification month age answer
    different questions. They do not establish present recruitment activity. Legacy
    page retrieval times remain unknown; month/year text does not imply a day.
    The configurable project-defined warning (default >180 days) retains records
    and is not a validity cutoff.
17. State geography uses reported country/state, not coordinates or a radius. An
    unresolved location means undetermined region membership unless another location
    supplies a selected-state match; it is not evidence of membership in that state.
    Coverage denominators retain these omissions before geography exclusion and
    measure captured-record inclusion, not total competitive-market coverage.
18. Estimated enrollment is a study target; reported actual enrollment is a distinct
    category. Missing values and unknown types remain separate. No enrollment is
    allocated among locations; repeated locations/history do not multiply totals.
19. Normalized facility/city/state text can merge different facilities or split one
    facility. Recorded-location ordinals distinguish rows only. Study segment counts
    overlap and must not be summed into a unique-study total. Derived scores, sponsor
    concentration, percentiles and growth remain warehouse calculations; the panel
    enumerates count inputs and qualifying growth/proxy events with predecessor/source
    evidence, and discloses formulas, windows and displayed values.
20. Raw pages are immutable while retained. Configured pruning can remove them;
    exported references do not guarantee future resolution or indefinite bronze
    retention. Rebuild historical silver from retained bronze to expose new provenance
    fields, then rebuild dbt; this does not invent absent historical receipt timestamps.
    Missing audit marts produce rebuild guidance rather than a substitute audit.
21. The isolated UTC-boundary fixture pins DuckDB dbt sessions to
    `America/Los_Angeles` and asserts explicit UTC alignment.

Registry-derived signals support preliminary feasibility review. They do not measure site-level recruitment performance or establish scientific validity. Counts reflect captured public records and the displayed inclusion rules.
