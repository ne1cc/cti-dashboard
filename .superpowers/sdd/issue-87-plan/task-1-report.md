# Task 1 Implementation Report: Single Source of Truth for ADRD Scope (#87)

**Date**: 2026-10-23
**Branch**: `feat/reconcile-adrd-scope-truth`
**Issue**: [#87](https://github.com/ne1cc/cti-dashboard/issues/87)

---

## 1. Executive Summary

Reconciled `config/project_config.yml` with `config/profiles/adrd.yml` so that `config/profiles/adrd.yml` is the single source of truth for ADRD scope (all 8 overall statuses: `RECRUITING`, `ACTIVE_NOT_RECRUITING`, `NOT_YET_RECRUITING`, `COMPLETED`, `TERMINATED`, `WITHDRAWN`, `SUSPENDED`, `ENROLLING_BY_INVITATION`), while retaining backward compatibility, standalone custom configuration semantics, project-level path/retention/guardrail settings, and HTTP environment variable overrides.

---

## 2. Changes Implemented

### Configuration (`config/project_config.yml`)
- Replaced duplicate `api.query_params` and `scope` blocks with `default_profile: adrd`.
- Retained project-level settings: `project`, `api` base configuration and HTTP parameters, `paths`, `ingestion`, `retention`, and `guardrails`.

### Core Configuration Loader (`src/config.py`)
- Added optional `default_profile: str | None = None` to `ProjectConfig`.
- In `load_config()`:
  - If `default_profile` is present in the raw YAML:
    - Performs local import (`from src.profiles import PROFILES_DIR, load_profile, normalize_profile_id`) to prevent circular import loops between `src.config` and `src.profiles`.
    - Resolves the profile file path across candidate locations (direct path, relative to config file directory, relative to `project_root()`, and in `config/profiles/`).
    - Fails cleanly with `FileNotFoundError` if the profile file is missing, or `ValueError` if `default_profile` is empty.
    - Dynamically loads `api.query_params` and `scope` from the referenced profile.
  - If `default_profile` is omitted (e.g. standalone custom YAML or test fixtures):
    - Preserves standalone config semantics as before, reading `api.query_params` and `scope` directly from the YAML.
  - Retains HTTP environment variable overrides (`CTI_HTTP_TIMEOUT_SECONDS`, `CTI_MAX_RETRIES`).

### Profile Framework (`src/profiles.py`)
- Updated module docstrings under "Backward compatibility" to describe delegation to `config/profiles/adrd.yml` as single source of truth via `default_profile: adrd`.

### Documentation
- `README.md`:
  - Updated Section 7 ("Project scope") to list all 8 overall statuses.
  - Documented downstream implications on tracked trial totals, stopped studies, and similarity candidate pool vs. recruiting competition.
- `PROJECT_DOCUMENTATION.md`:
  - Updated Section 1 ("Project at a glance") to record the 8 statuses and their downstream implications.
  - Clarified historical baseline numbers (2,592 studies) from the initial 4-status query in Section 6.
  - Updated Section 16 ("Configuration reference") table describing `config/project_config.yml` delegating scope to `config/profiles/adrd.yml`.
- `docs/source_documentation.md`:
  - Updated API parameters to list all 8 statuses for `filter.overallStatus` and document delegation to `config/profiles/adrd.yml`.
  - Added "Downstream Implications of the 8-Status Scope" section.
- `docs/metric_definitions.md`:
  - Documented scope authority under Global rules and detailed the downstream effects on tracked trials, stopped study analytics, and similarity vs. competition metrics.

### Automated Tests
- `tests/test_profiles.py`:
  - Extended `test_default_config_bronze_paths_match_the_adrd_profile` to assert complete query parameters dictionary and documented scope equality with the ADRD profile, including all 8 statuses.
  - Added `test_editing_referenced_profile_updates_default_api_and_scope_dynamically` verifying that editing the referenced profile updates default API and scope dynamically upon reload.
  - Added `test_standalone_yaml_works_without_default_profile` verifying standalone YAML works without `default_profile`.
  - Added `test_missing_default_profile_fails` verifying clean failure on missing or blank profile references.
  - Added `test_both_import_orders_work_without_error` proving both import orders (`src.config` then `src.profiles`, and `src.profiles` then `src.config`) execute cleanly without circular import errors.
- `tests/test_config.py`:
  - Added `test_default_config_resolves_adrd_profile`.
  - Added `test_load_config_env_var_overrides` for HTTP timeout and retry overrides.
  - Added `test_standalone_config_semantics` for custom configs without `default_profile`.

---

## 3. Verification Results

1. **Targeted Tests**:
   ```bash
   uv run pytest tests/test_config.py tests/test_profiles.py tests/test_profile_configs.py tests/test_cli.py tests/test_build_silver.py tests/test_quality.py
   ```
   - Result: **101 passed** in 5.05s.

2. **Linting and Formatting**:
   ```bash
   make lint
   ```
   - `ruff check src tests dashboard`: All checks passed.
   - `ruff format --check src tests dashboard`: 96 files formatted.
   - `mypy`: Success: no issues found in 26 source files.

3. **Full Pytest Suite**:
   ```bash
   uv run pytest
   ```
   - Result: **419 passed, 9 skipped** in 410.28s (0:06:50).

---

## 4. Downstream Implications Documented

1. **Tracked trial totals**: Total trial counts in the warehouse (`dim_trial`, `trial_count`, `fct_trial_snapshot`) capture all 8 interventional statuses, not just active/completed studies.
2. **Stopped studies & feasibility risk**: Trials that stopped prematurely (`TERMINATED`, `WITHDRAWN`, `SUSPENDED`) are retained, powering why-stopped and feasibility risk analyses.
3. **Similarity pool vs. recruiting competition**: The trial similarity candidate pool includes all 8 statuses to identify operational and scientific precedents across all historical trials, whereas active competition metrics (`recruiting_trial_count`, `new_recruiting_30d/90d`, `sponsor_hhi`, and priority scoring) strictly isolate confirmed currently-recruiting studies (`overall_status = 'RECRUITING'`).
