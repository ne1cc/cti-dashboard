# Clinical Trial Access & Recruitment Competition Intelligence
# All commands are reproducible entry points. Run `make help` for a summary.

PYTHON      := uv run python
DBT         := uv run dbt
DBT_DIR     := dbt_clinical_trials
DBT_FLAGS   := --project-dir $(DBT_DIR) --profiles-dir $(DBT_DIR)
CONDITION   ?= Alzheimer Disease

.DEFAULT_GOAL := help

.PHONY: help setup init-dirs env ingest full-refresh ingest-full-catalog full-catalog-full-refresh \
        orchestrate orchestrate-full-refresh prune-data prune-data-dry \
        transform dbt-deps dbt-seed dbt-run dbt-test \
        dbt-docs quality-report dashboard test lint format clean pipeline

help: ## Show available commands
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

setup: ## Install dependencies and prepare local environment
	uv sync --all-groups
	@test -f .env || cp .env.example .env
	@test -f $(DBT_DIR)/profiles.yml || { test -f $(DBT_DIR)/profiles.yml.example && cp $(DBT_DIR)/profiles.yml.example $(DBT_DIR)/profiles.yml || true; }
	@$(PYTHON) -m src.cli init-data-dirs >/dev/null
	@echo "Setup complete. Edit .env if needed, then run: make ingest"

init-dirs: ## Create every profile's data directories from config/profiles/
	$(PYTHON) -m src.cli init-data-dirs

ingest: ## Run an incremental ingestion snapshot from ClinicalTrials.gov (Phase 2)
	$(PYTHON) -m src.cli ingest --condition "$(CONDITION)"

full-refresh: ## Re-ingest all pages ignoring incremental state (Phase 2)
	$(PYTHON) -m src.cli ingest --condition "$(CONDITION)" --full-refresh

ingest-nsclc: ## Snapshot Non-Small Cell Lung Cancer studies into bronze
	$(PYTHON) -m src.cli ingest --profile oncology_nsclc

transform-nsclc: ## Flatten bronze NSCLC runs into silver Parquet entities
	$(PYTHON) -m src.cli transform --profile oncology_nsclc

ingest-full-catalog: ## Opt-in: snapshot the full ClinicalTrials.gov registry, all conditions worldwide (bronze only)
	$(PYTHON) -m src.cli ingest --profile full_catalog

full-catalog-full-refresh: ## Opt-in: force a full re-pull of the full-catalog profile
	$(PYTHON) -m src.cli ingest --profile full_catalog --full-refresh

orchestrate: ## Run ingest + transform for all indication profiles in config/profiles/
	$(PYTHON) -m src.cli orchestrate

orchestrate-full-refresh: ## Force full re-ingest + transform for all indication profiles
	$(PYTHON) -m src.cli orchestrate --full-refresh

prune-data: ## Delete run artifacts past the per-profile retention horizon
	$(PYTHON) -m src.cli prune-data

prune-data-dry: ## Report what prune-data would delete
	$(PYTHON) -m src.cli prune-data --dry-run

transform: ## Flatten bronze JSON into silver Parquet entities (Phase 3)
	$(PYTHON) -m src.cli transform

dbt-deps: ## Install dbt packages (Phase 4)
	$(DBT) deps $(DBT_FLAGS)

dbt-seed: ## Load dbt seeds: score weights, phase/status mappings (Phase 4)
	$(DBT) seed $(DBT_FLAGS)

dbt-run: dbt-seed ## Build staging, intermediate, and mart models (Phase 4)
	$(DBT) run $(DBT_FLAGS)

dbt-test: ## Run dbt data tests (Phase 4)
	$(DBT) test $(DBT_FLAGS)

dbt-docs: ## Generate dbt documentation site (Phase 4)
	$(DBT) docs generate $(DBT_FLAGS)

quality-report: ## Build the data-quality report (Phase 5)
	$(PYTHON) -m src.cli quality-report

dashboard: ## Launch the Streamlit dashboard (Phase 6)
	uv run streamlit run dashboard/app.py

pipeline: ingest transform dbt-run dbt-test quality-report ## Full end-to-end refresh

test: ## Run Python unit tests
	uv run pytest

lint: ## Lint and type-check Python code
	uv run ruff check src tests dashboard
	uv run ruff format --check src tests dashboard
	uv run mypy

format: ## Auto-format Python code
	uv run ruff format src tests dashboard

clean: ## Remove caches and build artifacts (never touches data/)
	rm -rf .pytest_cache .ruff_cache htmlcov .coverage $(DBT_DIR)/target $(DBT_DIR)/logs logs
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
