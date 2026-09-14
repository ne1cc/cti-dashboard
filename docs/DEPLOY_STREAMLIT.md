# Deploy the Streamlit dashboard (Streamlit Community Cloud)

The dashboard reads a local DuckDB warehouse built by the pipeline. Community Cloud
does not run `make ingest` automatically, so use the **demo warehouse bootstrap**
before deploying.

## One-time local bootstrap (commit the demo warehouse or use secrets)

For a public portfolio demo, build a small deterministic warehouse locally:

```bash
make setup
make ingest CONDITION="Alzheimer Disease"   # or use bundled snapshot if available
make transform
make dbt-run && make dbt-test
```

Verify locally:

```bash
make dashboard   # or: uv run streamlit run dashboard/app.py
```

## Streamlit Community Cloud settings

| Setting | Value |
|---------|--------|
| Repository | `n1ecC/clinical-trial-intelligence` |
| Branch | `main` |
| Main file | `dashboard/app.py` |
| Python | 3.12 |

## App entry

```bash
streamlit run dashboard/app.py
```

## Notes

- The warehouse path comes from `.env` / `config/project_config.yml` (see `.env.example`).
- If the app shows "Warehouse not found", the DuckDB file is missing from the deploy
  environment — rebuild locally and ensure `data/warehouse/` is populated before demoing.
- This app is a **portfolio demonstration**, not clinical decision support.
