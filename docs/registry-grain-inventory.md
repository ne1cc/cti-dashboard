# Registry-grain migration inventory (#164)

Inventory at main commit `5048c6f`. A literal search for `indication_profile_id` finds 15 files in `src`, 7 in `dashboard`, 66 in `dbt_clinical_trials`, 46 in `tests`, and 10 in `docs`. These counts are a dependency inventory, not a claim that every occurrence needs changing.

## Current boundaries

- `src/transform/build_silver_entities.py` deduplicates by NCT ID and profile; `flatten_studies.py` stamps profile identity. `src/transform/preset_runs.py` builds preset runs from registry pages.
- `dim_trial.sql` hashes NCT ID plus profile into `trial_key`, while its additive `registry_trial_key` hashes NCT ID alone. `bridge_trial_condition.sql` carries both trial keys and the profile-specific condition key. Removing the profile without changing joins can fan out shared trials.
- `dashboard/components/data.py` scopes readers to profile and registry-condition keys. `src/presets.py` derives preset keys. The existing tests cover shared condition nodes and unmapped bridge rows, while the new shared-trial test pins the two profile identities to one registry identity.
- `mart_data_reliability.sql`, `mart_study_snapshot_audit.sql` and other profile-grain reports need explicit preset membership and snapshot semantics. They cannot simply drop the profile column or be assumed to filter at the trial grain.
- The baseline under `tests/fixtures/mart_baseline/` captures ADRD and NSCLC profile outputs; `tests/test_mart_baseline.py` checks prior columns and rows. `tests/test_presets.py` compares selected readers. The fixture is not evidence of full-registry parity.

## Decisions to resolve before implementation

For conflicting versions of an NCT ID across retained snapshots, define a deterministic source and snapshot ordering and the treatment of equal timestamps with conflicting payloads. Preserve provenance and fail rather than silently choose when the ordering cannot distinguish conflicts. Define membership for trials lacking derived MeSH and how audit, reliability and transparency aggregates behave when presets overlap. The existing limitations and unmeasured live-run differences remain documented in `docs/profile-retirement-expected-diffs.md`.

Before each subsequent PR, enumerate its affected files and contracts from current main, specify migration and rollback behavior, and add measured differences to the expected-differences document before changing baselines. A warehouse on the old grain requires a full rebuild at cutover; no data deletion is implied.
