# AGENTS.md

Streamlit dashboard and data pipeline over public ClinicalTrials.gov API v2 records (bronze → silver → gold, dbt, DuckDB). Product context is in `README.md`.

Workflow rules for agents: read `.github/AGENT-WORKFLOW.md` before your first commit.

## Commands
Same checks CI runs (`.github/workflows/ci.yml`); copy them into an issue's "Verify with".
- Install: `uv sync --all-groups --frozen`
- Lint: `uv run ruff check src tests dashboard && uv run ruff format --check src tests dashboard`
- Format: `uv run ruff format src tests dashboard`
- Type check: `uv run mypy`
- Test: `uv run pytest --cov=src --cov-report=term-missing --cov-fail-under=71`
- Other entry points: `make help`

## Architecture
- `src/`: pipeline code (`ingest/`, `transform/`, `analysis/`, `quality/`, `orchestration/`, `cli.py`)
- `dashboard/`: Streamlit app (`app.py`, `pages/`, `components/`)
- `dbt_clinical_trials/`: dbt project; `config/`: YAML taxonomies, geography rules, score weights, profiles
- `tests/`: pytest (`test_*.py`, fixtures in `tests/fixtures/`); `docs/`: metric definitions and case studies

## Conventions
- Python 3.12 with `uv`; code must pass ruff, ruff format and mypy.
- New behavior gets a test; the coverage gate is 71%.
- Metric definitions live in `docs/metric_definitions.md`; keep it in sync when a definition changes.

## Do not touch
- `data/bronze|silver|gold/**` and `data/warehouse/*.duckdb`: generated, never committed.
- `.env` and anything holding secrets; add new variables to `.env.example`.
- Deploy path: the `deploy` job in `ci.yml`, `fly.toml`, `.github/scripts/deploy_fly.sh`, unless the issue says so.
- `make prune-data` deletes run artifacts (`make prune-data-dry` previews); never run it unless the issue says so.
- `uv.lock`: change it only through `uv`, never by hand.
