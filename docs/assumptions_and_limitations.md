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

## Operational limitations
10. Profile-scoped indications; additional areas require configuration changes
    and taxonomy review.
11. Weekly cadence is recommended, not enforced; gaps widen transition
    windows.
12. Local-first DuckDB warehouse; multi-user concurrency and cloud
    orchestration are roadmap items.
13. **Portfolio demonstration.** Real feasibility decisions require
    qualified clinical-operations review and additional data sources.

## Audit interpretation and retention

14. Snapshot IDs identify captured successful ingestion runs, not historic registry
    versions before collection began. Daily metrics choose the latest per-study/date
    observation; the audit records the actual contributing runs, including several
    runs on one date. Active rule hashes do not establish unrecorded historical
    taxonomy/geography configuration identity.
15. Pipeline age, public posted-update age, and verification month age answer
    different questions. They do not establish present recruitment activity. Legacy
    page retrieval times remain unknown; month/year text does not imply a day.
    The configurable project-defined warning (default >180 days) retains records
    and is not a validity cutoff.
16. State geography uses reported country/state, not coordinates or a radius. An
    organization evaluating regional site proximity should supplement state-level
    listings with postal coordinates or driving distance.
