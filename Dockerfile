FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates make \
    && rm -rf /var/lib/apt/lists/*

RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:${PATH}"

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY dbt_clinical_trials/ dbt_clinical_trials/
COPY src/ src/
COPY dashboard/ dashboard/
COPY config/ config/
COPY tests/ tests/
COPY .streamlit/ .streamlit/
COPY Makefile README.md ./

RUN uv sync --all-groups --frozen
RUN uv run dbt deps --project-dir dbt_clinical_trials --profiles-dir dbt_clinical_trials
RUN test -f dbt_clinical_trials/profiles.yml || cp dbt_clinical_trials/profiles.yml.example dbt_clinical_trials/profiles.yml
RUN uv run python -m src.cli init-data-dirs > /dev/null

COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

EXPOSE 8501

ENTRYPOINT ["/app/entrypoint.sh"]
