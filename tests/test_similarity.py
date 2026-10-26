"""Trial similarity weight-configuration tests: config/seed consistency."""

import csv
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SIMILARITY_WEIGHTS_YML = ROOT / "config" / "similarity_weights.yml"
SEED_CSV = ROOT / "dbt_clinical_trials" / "seeds" / "similarity_score_weights.csv"


def load_similarity_config() -> dict:
    return yaml.safe_load(SIMILARITY_WEIGHTS_YML.read_text(encoding="utf-8"))


def load_seed_weights() -> dict[str, float]:
    with open(SEED_CSV, encoding="utf-8") as handle:
        return {row["component"]: float(row["weight"]) for row in csv.DictReader(handle)}


def test_similarity_weights_sum_to_one():
    weights = load_similarity_config()["similarity"]["weights"]
    assert abs(sum(weights.values()) - 1.0) < 1e-9


def test_yaml_weights_match_dbt_seed():
    yaml_weights = load_similarity_config()["similarity"]["weights"]
    assert yaml_weights == load_seed_weights()


def test_similarity_models_reference_indication_profile_id():
    candidates_path = ROOT / (
        "dbt_clinical_trials/models/intermediate/int_similarity_candidates.sql"
    )
    int_path = ROOT / "dbt_clinical_trials/models/intermediate/int_trial_comparability_features.sql"
    mart_path = ROOT / "dbt_clinical_trials/models/marts/mart_trial_similarity.sql"
    candidates_sql = candidates_path.read_text()
    int_sql = int_path.read_text()
    mart_sql = mart_path.read_text()

    assert "indication_profile_id" in int_sql
    assert "indication_profile_id" in mart_sql
    # Pair discovery self-joins within a profile in the candidates model (the
    # bounded replacement for the mart's old in-model self-join), and the mart
    # carries the profile through on both sides of its scoring join.
    assert "a.indication_profile_id = b.indication_profile_id" in candidates_sql
    assert "c.indication_profile_id = a.indication_profile_id" in mart_sql
    assert "c.indication_profile_id = b.indication_profile_id" in mart_sql


def test_comparability_features_references_eligibility_fields():
    int_path = ROOT / "dbt_clinical_trials/models/intermediate/int_trial_comparability_features.sql"
    int_sql = int_path.read_text()
    assert "minimum_age_years" in int_sql
    assert "maximum_age_years" in int_sql
    assert "healthy_volunteers" in int_sql
    assert "sex" in int_sql
