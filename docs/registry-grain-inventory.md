# Registry-grain migration inventory (#164)

Inventory at main commit `5048c6f`. A literal search for `indication_profile_id` finds 15 files in `src`, 7 in `dashboard`, 66 in `dbt_clinical_trials`, 46 in `tests`, and 10 in `docs`. These counts are a dependency inventory, not a claim that every occurrence needs changing.

## Current boundaries

- `src/transform/build_silver_entities.py` deduplicates by NCT ID and profile; `flatten_studies.py` stamps profile identity. `src/transform/preset_runs.py` builds preset runs from registry pages.
- `dim_trial.sql` hashes NCT ID plus profile into `trial_key`, while its additive `registry_trial_key` hashes NCT ID alone. `bridge_trial_condition.sql` carries both trial keys and the profile-specific condition key. Removing the profile without changing joins can fan out shared trials.
- `dashboard/components/data.py` scopes readers to profile and registry-condition keys. `src/presets.py` derives preset keys. The existing tests cover shared condition nodes and unmapped bridge rows, while the new shared-trial test pins the two profile identities to one registry identity.
- `mart_data_reliability.sql`, `mart_study_snapshot_audit.sql` and other profile-grain reports need explicit preset membership and snapshot semantics. They cannot simply drop the profile column or be assumed to filter at the trial grain.
- The baseline under `tests/fixtures/mart_baseline/` captures ADRD and NSCLC profile outputs; `tests/test_mart_baseline.py` checks prior columns and rows. `tests/test_presets.py` compares selected readers. The fixture is not evidence of full-registry parity.

## Version and field conflict policy for review

This is the proposed contract for phases 2–5, not behavior implemented by #166.
The current profile-specific behavior is pinned by the distinct-snapshot tests and measured
in `docs/profile-retirement-expected-diffs.md`.
Approval of the staged plan does not settle the open choices at the end of this document.

1. **Identity and observation.** Registry identity is `md5(nct_id)`. An observation is
   `(nct_id, source_registry_run_id)`; preset membership is separate. Prefer the original
   whole-registry payload, not one of its taxonomy overlays. Two derived profiles referring
   to that same source run must not create two canonical observations. A retained legacy
   profile run without a verifiable source link cannot be silently declared a registry snapshot.
2. **Version order.** Only successful, complete snapshots may be published. Order observations
   by the source manifest's `started_at_utc` normalized to UTC, as of the requested snapshot.
   Registry posted/verified dates can be partial or absent; do not use them as a version clock.
   A newer observation wins as a whole record. Incremental carry-forward keeps the original
   observation's payload/provenance and separately records the containing snapshot; it does
   not make the old payload a new observation. A chain without a retained full base fails.
3. **Ties and duplicates.** Identical NCT/source/payload hashes may coalesce, retaining every
   provenance reference. Conflicting hashes within one source snapshot, or equal source
   timestamps with conflicting payloads and no authoritative ordering, fail with the NCT ID,
   run IDs and hashes. Neither profile name, arbitrary run-ID sorting nor page order breaks
   the tie. A different run ID alone is not evidence that one version is newer.
4. **Field conflicts.** Take status, enrollment, dates, sponsor, eligibility, locations,
   outcomes and derived MeSH from that one winning payload. Do not `coalesce` newer NULLs
   with older values, union different versions' locations/MeSH, or borrow a field from an
   overlapping preset. Keep raw date strings and NULLs. Missing required identity/source
   metadata, failed parsing of required provenance or incomplete entity output prevents
   publication; optional absent registry modules remain absent. Keep legacy outputs intact.
5. **Overlays and reports.** Map YAML groups at `(preset, node, registry trial, snapshot)`;
   a shared group slug is not permission to merge profile-specific taxonomy definitions.
   Direct and ancestor MeSH edges deduplicate at `(trial, snapshot, node)`, with direct taking
   precedence when both occur. Recompute reliability, audit and transparency from the same
   preset membership and snapshot; preserve source-run totals separately from preset totals.
   Count an overlapping trial once within each preset and once in a registry union, while
   preserving that it legitimately belongs to multiple presets.

## Exact later-PR inventory

Paths below are the concrete planning inventory against `5048c6f` plus #166 head `4c3317e`.
They are repo-relative, not glob patterns. Repeated paths are intentional dependencies between
phases. Lists describe planned edits and compatibility verification, not completed changes;
`NEW` marks a proposed file that does not exist yet. Re-audit current main before each PR and
seek renewed approval for a material inventory or contract change. Phase 4 must be split into
small PRs at the reader/report boundaries listed, each with an exact subset in its PR body.
No later-phase implementation or baseline replacement is authorized by this inventory alone.

### Phase 2 — parallel canonical silver and staging

Keep the profile writer and its callers working; add a separate registry output using the same
bronze source and validation. Nine registry entity streams parallel the current nine silver
entities; snapshot manifests retain their own source identity. Stage them independently.

```text
src/transform/build_silver_entities.py
src/transform/flatten_studies.py
src/transform/export_parquet.py
src/transform/silver_stats.py
dbt_clinical_trials/models/staging/_sources.yml
dbt_clinical_trials/models/staging/_staging.yml
dbt_clinical_trials/macros/silver_parquet_schema.sql
tests/test_build_silver.py
tests/test_normalization.py
tests/test_condition_mesh.py
tests/test_fixture_warehouse.py
tests/fixture_warehouse.py
tests/conftest.py
tests/preset_helpers.py
docs/data_dictionary.md
docs/OPERATIONS.md
docs/profile-retirement-expected-diffs.md
```

```text
NEW dbt_clinical_trials/models/staging/stg_registry_trials.sql
NEW dbt_clinical_trials/models/staging/stg_registry_trial_conditions.sql
NEW dbt_clinical_trials/models/staging/stg_registry_trial_condition_mesh.sql
NEW dbt_clinical_trials/models/staging/stg_registry_trial_interventions.sql
NEW dbt_clinical_trials/models/staging/stg_registry_trial_arms.sql
NEW dbt_clinical_trials/models/staging/stg_registry_trial_sponsors.sql
NEW dbt_clinical_trials/models/staging/stg_registry_trial_locations.sql
NEW dbt_clinical_trials/models/staging/stg_registry_trial_outcomes.sql
NEW dbt_clinical_trials/models/staging/stg_registry_trial_baseline_demographics.sql
NEW dbt_clinical_trials/models/staging/stg_registry_trial_snapshots.sql
NEW dbt_clinical_trials/tests/assert_registry_observation_integrity.sql
```

### Phase 3 — parallel dimensions, membership and compatibility

Build registry dimensions and a snapshot-aware preset bridge alongside legacy dimensions;
keep old names and keys available until their readers pass parity. Validate shared-node,
no-MeSH, duplicate-source, same-timestamp-conflict and distinct-snapshot cases.

```text
dbt_clinical_trials/models/intermediate/int_trial_status_history.sql
dbt_clinical_trials/models/intermediate/int_current_trial_status.sql
dbt_clinical_trials/models/intermediate/int_trial_condition_mapping.sql
dbt_clinical_trials/models/intermediate/_intermediate.yml
dbt_clinical_trials/models/marts/dim_trial.sql
dbt_clinical_trials/models/marts/dim_condition.sql
dbt_clinical_trials/models/marts/bridge_trial_condition.sql
dbt_clinical_trials/models/marts/dim_sponsor.sql
dbt_clinical_trials/models/marts/bridge_trial_sponsor.sql
dbt_clinical_trials/models/marts/dim_geography.sql
dbt_clinical_trials/models/marts/_marts.yml
dbt_clinical_trials/tests/assert_registry_keys_unique_at_grain.sql
dbt_clinical_trials/tests/assert_mesh_identity_unique_per_trial_snapshot.sql
dbt_clinical_trials/tests/assert_one_current_record_per_trial.sql
dbt_clinical_trials/tests/assert_dim_geography_profile_state_is_unique.sql
tests/test_mart_baseline.py
tests/test_presets.py
tests/test_condition_mesh.py
tests/test_dbt_fixture_build.py
tests/preset_helpers.py
tests/fixture_warehouse.py
tests/conftest.py
docs/data_dictionary.md
docs/profile-retirement-expected-diffs.md
```

```text
NEW dbt_clinical_trials/models/marts/dim_registry_trial.sql
NEW dbt_clinical_trials/models/marts/dim_registry_condition.sql
NEW dbt_clinical_trials/models/marts/bridge_registry_trial_condition.sql
NEW dbt_clinical_trials/models/marts/bridge_preset_trial.sql
NEW dbt_clinical_trials/tests/assert_preset_membership_integrity.sql
```

### Phase 4 — reports and readers, subdivided before execution

**4a. Facts, condition/activity, competition, priority, sites, access, demographics and similarity.**
All joins must choose a canonical observation before expanding condition/site membership.

```text
dbt_clinical_trials/models/intermediate/int_condition_geography_activity.sql
dbt_clinical_trials/models/intermediate/int_geography_normalized.sql
dbt_clinical_trials/models/intermediate/int_similarity_candidates.sql
dbt_clinical_trials/models/intermediate/int_sponsor_concentration.sql
dbt_clinical_trials/models/intermediate/int_trial_comparability_features.sql
dbt_clinical_trials/models/intermediate/int_trial_site_activity.sql
dbt_clinical_trials/models/marts/fct_trial_site.sql
dbt_clinical_trials/models/marts/fct_trial_snapshot.sql
dbt_clinical_trials/models/marts/mart_baseline_demographics_coverage.sql
dbt_clinical_trials/models/marts/mart_condition_access_rates.sql
dbt_clinical_trials/models/marts/mart_condition_geography_trends.sql
dbt_clinical_trials/models/marts/mart_feasibility_priority_queue.sql
dbt_clinical_trials/models/marts/mart_participation_prevalence_ratio.sql
dbt_clinical_trials/models/marts/mart_recruiting_competition.sql
dbt_clinical_trials/models/marts/mart_site_overlap.sql
dbt_clinical_trials/models/marts/mart_trial_access_by_geo.sql
dbt_clinical_trials/models/marts/mart_trial_access_by_stratum.sql
dbt_clinical_trials/models/marts/mart_trial_activity.sql
dbt_clinical_trials/models/marts/mart_trial_similarity.sql
dbt_clinical_trials/models/marts/mart_trial_similarity_reference.sql
dbt_clinical_trials/models/intermediate/_intermediate.yml
dbt_clinical_trials/models/marts/_marts.yml
dbt_clinical_trials/tests/assert_competition_growth_needs_history.sql
dbt_clinical_trials/tests/assert_no_self_similarity_pairs.sql
dbt_clinical_trials/tests/assert_similarity_rank_is_dense_sequence.sql
dbt_clinical_trials/tests/assert_similarity_score_within_bounds.sql
dbt_clinical_trials/tests/assert_sponsor_hhi_needs_min_sponsors.sql
dbt_clinical_trials/tests/assert_trends_growth_needs_full_window.sql
dbt_clinical_trials/tests/assert_trends_growth_needs_two_months.sql
dbt_clinical_trials/tests/assert_trial_access_grain_and_nesting.sql
dbt_clinical_trials/tests/assert_trial_site_relationship_integrity.sql
dbt_clinical_trials/analyses/analysis_site_overlap_hotspots.sql
dbt_clinical_trials/analyses/analysis_sponsor_landscape.sql
dbt_clinical_trials/analyses/analysis_top_priority_segments.sql
src/analysis/density_proxy_test.py
tests/test_density_proxy_test.py
tests/test_similarity.py
tests/test_similarity_candidates.py
tests/test_sponsor_hhi_threshold.py
tests/test_demographics_coverage.py
tests/test_geography_population.py
tests/test_trial_access_geo.py
tests/test_participation_prevalence.py
tests/test_dbt_fixture_build.py
```

**4b. Reliability, transparency, source audit and change events.** Preserve reports and source
provenance. Distinguish a source ingestion's completeness from a preset's membership denominator.

```text
dbt_clinical_trials/models/intermediate/int_trusted_change_runs.sql
dbt_clinical_trials/models/intermediate/int_trial_outcome_sets.sql
dbt_clinical_trials/models/intermediate/int_trial_outcome_changes.sql
dbt_clinical_trials/models/marts/mart_data_reliability.sql
dbt_clinical_trials/models/marts/mart_study_snapshot_audit.sql
dbt_clinical_trials/models/marts/mart_location_snapshot_audit.sql
dbt_clinical_trials/models/marts/mart_trial_change_events.sql
dbt_clinical_trials/models/marts/mart_early_stop_transparency.sql
dbt_clinical_trials/models/marts/mart_results_posting_transparency.sql
dbt_clinical_trials/models/marts/_marts.yml
dbt_clinical_trials/analyses/analysis_data_reliability_trend.sql
dbt_clinical_trials/tests/assert_snapshot_completeness.sql
dbt_clinical_trials/tests/assert_audit_enrollment_join_invariance.sql
dbt_clinical_trials/tests/assert_audit_exclusion_reconciliation.sql
src/quality/data_quality_report.py
src/quality/reconciliation.py
src/quality/freshness.py
dashboard/components/audit.py
dashboard/components/audit_panel.py
tests/test_audit.py
tests/test_audit_marts.py
tests/test_change_radar.py
tests/test_trial_transparency.py
tests/test_reconciliation.py
tests/test_freshness.py
tests/test_quality.py
tests/test_dashboard_audit.py
```

**4c. Preset readers and dashboard.** Preserve YAML-only preset creation and the unmapped-trial
behavior until an explicitly reviewed change. Verify page callers even where they have no literal
`indication_profile_id` reference.

```text
src/presets.py
dashboard/components/data.py
dashboard/components/profile.py
dashboard/components/filters.py
dashboard/components/guidance.py
dashboard/components/access_rates.py
dashboard/components/county_access.py
dashboard/overview.py
dashboard/pages/10_Access_Rates.py
dashboard/pages/11_Participation_Prevalence.py
dashboard/pages/1_Priority_Queue.py
dashboard/pages/2_Competition_Landscape.py
dashboard/pages/3_Geography_Trends.py
dashboard/pages/4_Site_Overlap.py
dashboard/pages/5_Sponsor_Landscape.py
dashboard/pages/6_Data_Reliability.py
dashboard/pages/7_Trial_Explorer.py
dashboard/pages/8_Trial_Similarity.py
dashboard/pages/9_Trial_Transparency.py
tests/test_presets.py
tests/test_dashboard_profile_scope.py
tests/test_dashboard_county_access.py
tests/test_dashboard_resilience.py
tests/test_dashboard_smoke.py
tests/test_dashboard_audit.py
tests/test_dashboard_content.py
```

**4d. API warehouse consumers.** These also depend on the profile grain and cannot be ignored
at cutover. Preserve external request/response fields through a compatibility mapping. Removing
or renaming a public field requires separate explicit approval, not a silent internal migration.

```text
src/api/warehouse.py
src/api/conditions.py
src/api/app.py
src/api/live.py
src/api/models.py
src/api/sql/detail.sql
tests/test_api_warehouse.py
tests/test_api_contract.py
tests/test_consumer_conditions.py
docs/api/openapi.yaml
docs/api/source-field-map.md
docs/api/examples.md
```

**4e. Shared parity evidence (each sub-PR).** Project onto the existing baseline and compare every
row before replacing anything. Only measured, approved differences may enter replacement JSON.

```text
tests/test_mart_baseline.py
tests/test_presets.py
tests/mart_baseline.py
tests/preset_helpers.py
tests/fixture_warehouse.py
tests/conftest.py
tests/fixtures/mart_baseline/README.md
tests/fixtures/mart_baseline/bridge_trial_condition.json
tests/fixtures/mart_baseline/bridge_trial_sponsor.json
tests/fixtures/mart_baseline/dim_condition.json
tests/fixtures/mart_baseline/dim_geography.json
tests/fixtures/mart_baseline/dim_sponsor.json
tests/fixtures/mart_baseline/dim_trial.json
tests/fixtures/mart_baseline/fct_trial_site.json
tests/fixtures/mart_baseline/fct_trial_snapshot.json
tests/fixtures/mart_baseline/mart_baseline_demographics_coverage.json
tests/fixtures/mart_baseline/mart_condition_geography_trends.json
tests/fixtures/mart_baseline/mart_data_reliability.json
tests/fixtures/mart_baseline/mart_feasibility_priority_queue.json
tests/fixtures/mart_baseline/mart_location_snapshot_audit.json
tests/fixtures/mart_baseline/mart_recruiting_competition.json
tests/fixtures/mart_baseline/mart_site_overlap.json
tests/fixtures/mart_baseline/mart_study_snapshot_audit.json
tests/fixtures/mart_baseline/mart_trial_activity.json
tests/fixtures/mart_baseline/mart_trial_change_events.json
tests/fixtures/mart_baseline/mart_trial_similarity.json
docs/profile-retirement-expected-diffs.md
docs/metric_definitions.md
docs/dashboard_spec.md
docs/condition_mesh_coverage.md
docs/evidence/build_feasibility_case_study.py
```

### Phase 5 — cutover and legacy-path retirement

Revisit the exact files in phases 2–4 to remove legacy columns/aliases only after their migrated
consumers pass. Additionally retire profile-derived ingestion/transform scheduling and obsolete
configuration in the files below. Keep preset labels, filters and optional YAML overlays; renaming
or moving preset YAMLs is an unresolved decision, so no replacement path is claimed here. Retain
all existing user bronze/silver/warehouse data. Rebuild into a new location, compare, switch the
configured warehouse only after review, and roll back by selecting the retained old code/config
and warehouse together. Do not run retention or prune as part of migration.

```text
src/transform/preset_runs.py
src/transform/normalize_conditions.py
src/profiles.py
src/config.py
src/cli.py
src/ingest/extract_studies.py
src/ingest/snapshot_manifest.py
src/utils/retention.py
src/orchestration/assets/bronze.py
src/orchestration/assets/silver.py
src/orchestration/assets/dbt_assets.py
src/orchestration/checks.py
src/orchestration/definitions.py
config/project_config.yml
config/condition_taxonomy.yml
config/condition_taxonomy_nsclc.yml
config/profiles/adrd.yml
config/profiles/full_catalog.yml
config/profiles/heart_failure.yml
config/profiles/multiple_sclerosis.yml
config/profiles/oncology_breast.yml
config/profiles/oncology_colorectal.yml
config/profiles/oncology_melanoma.yml
config/profiles/oncology_nsclc.yml
config/profiles/oncology_ovarian.yml
config/profiles/oncology_prostate.yml
config/profiles/parkinsons.yml
config/profiles/rheumatoid_arthritis.yml
config/profiles/type2_diabetes.yml
dbt_clinical_trials/models/staging/_sources.yml
dbt_clinical_trials/models/staging/_staging.yml
dbt_clinical_trials/models/staging/stg_trial_arms.sql
dbt_clinical_trials/models/staging/stg_trial_baseline_demographics.sql
dbt_clinical_trials/models/staging/stg_trial_condition_mesh.sql
dbt_clinical_trials/models/staging/stg_trial_conditions.sql
dbt_clinical_trials/models/staging/stg_trial_contacts.sql
dbt_clinical_trials/models/staging/stg_trial_interventions.sql
dbt_clinical_trials/models/staging/stg_trial_locations.sql
dbt_clinical_trials/models/staging/stg_trial_outcomes.sql
dbt_clinical_trials/models/staging/stg_trial_snapshots.sql
dbt_clinical_trials/models/staging/stg_trial_sponsors.sql
dbt_clinical_trials/models/staging/stg_trials.sql
entrypoint.sh
Makefile
tests/test_preset_runs.py
tests/test_profiles.py
tests/test_profile_configs.py
tests/test_config.py
tests/test_cli.py
tests/test_extract_studies.py
tests/test_snapshot_manifest.py
tests/test_retention.py
tests/test_orchestration_assets.py
tests/test_orchestration_definitions.py
tests/test_docs_describe_current_paths.py
README.md
PROJECT_DOCUMENTATION.md
docs/OPERATIONS.md
docs/architecture.md
docs/data_dictionary.md
docs/registry-grain-inventory.md
docs/profile-retirement-expected-diffs.md
```

The final literal search must cover `src`, `dashboard`, `dbt_clinical_trials`, `tests`, and `docs`.
Keep historical explanations in `docs/registry-grain-inventory.md`,
`docs/profile-retirement-expected-diffs.md` and `tests/fixtures/mart_baseline/README.md`; list exact
remaining historical occurrences in the cutover PR. An old baseline mentioning the old column
is acceptable only as an explicitly identified historical artifact, never as the live contract.
`data/`, secrets, `uv.lock`, deployment workflows and third-party reference seeds are excluded.

## Unresolved decisions and implementation gates

- **Legacy input authority:** approve rebuilding from an authoritative retained registry run
  versus importing mixed profile-only snapshots. The latter needs a reviewed provenance mapping;
  ambiguous legacy rows must fail rather than overwrite canonical data.
- **Version clock:** approve the source-manifest clock above and define treatment of interrupted
  or same-timestamp captures. Tests here preserve current per-profile observations; they do not
  prove the proposed canonical conflict rejection, which belongs to phase 2.
- **Unmapped/no-MeSH membership:** current already-ingested trials with no bridge row stay in
  their owning profile's counts. Registry MeSH selection excludes no-MeSH records before transform.
  Approve a registry-wide no-MeSH policy (explicit unassigned scope or another reviewed rule)
  before replacing that fallback. Do not silently drop these trials.
- **Reports and overlays:** approve exact membership denominators for overlapping presets,
  source-wide versus preset-wide audit/reliability fields and conflicting YAML groups before 4b.
  Preserve all three report families. Their live numerical differences remain unmeasured.
- **Configuration and API:** decide whether preset YAML stays in `config/profiles` and how old
  CLI/profile selectors map to memberships. Public API fields keep their contract pending a
  separately approved version change. These choices can materially change the phase 5 inventory.
- **Live evidence:** this PR measures deterministic fixtures only. A retained production run or
  full registry capture and matched before/after rebuilds are still needed before cutover or a
  claim of registry-wide ADRD/NSCLC parity. No live data was invented or baseline regenerated.
