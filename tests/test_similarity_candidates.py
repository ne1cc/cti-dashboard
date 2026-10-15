"""Bounded candidate discovery for mart_trial_similarity.

Two warehouses, both built from one dense ADRD snapshot (24 cloned "Alzheimer
Disease" listings push the alzheimers_disease token to df 27, so candidate
sets outnumber the top-25 cap) plus the base NSCLC page:
`similarity_equivalence_root` keeps the default `similarity_max_token_df` and
builds `mart_trial_similarity_reference` -- the pre-2026-10 unbounded
self-join, var-gated because at registry scale it is the measured 28 GB run.
`similarity_cap_root` sets `similarity_max_token_df: 4` so the dominant
alzheimers token drops out of discovery while the small lewy token survives.

The load-bearing property is exact, not statistical: scoring is untouched and
only pair discovery narrowed, so for every (profile, trial) the new mart's
rows must equal the reference mart's rows for that trial restricted to
candidate pairs, re-ranked by the same order. Anything weaker (sampling,
thresholds) would let a scoring change hide behind a discovery change.
"""

from pathlib import Path

import duckdb

from tests.test_dbt_fixture_build import _rows

# The mart contract's columns, in model order: the equivalence compare is a
# full-row EXCEPT, so both sides must project the same columns in the same
# order. Keep in sync with models/marts/_marts.yml and
# mart_trial_similarity_reference.sql.
_SIMILARITY_COLUMNS = [
    "trial_similarity_key",
    "indication_profile_id",
    "nct_id_a",
    "nct_id_b",
    "similarity_score",
    "similarity_rank",
    "same_condition",
    "weight_same_condition",
    "weighted_same_condition",
    "same_phase",
    "weight_same_phase",
    "weighted_same_phase",
    "geography_overlap",
    "weight_geography_overlap",
    "weighted_geography_overlap",
    "intervention_type_overlap",
    "weight_intervention_type_overlap",
    "weighted_intervention_type_overlap",
    "study_design_match",
    "weight_study_design_match",
    "weighted_study_design_match",
    "eligibility_compatible",
    "weight_eligibility_compatible",
    "weighted_eligibility_compatible",
    "enrollment_band_match",
    "weight_enrollment_band_match",
    "weighted_enrollment_band_match",
    "similarity_explanation",
]


def _expected_sql() -> str:
    """The equivalence target as a CTE: reference rows on candidate pairs,
    re-ranked with the mart's own order (score desc, nct_id_b), top 25 kept.

    Exactly what the new mart computes if, and only if, its scoring matches
    the reference's. A CTE, not a bare parenthesized query: DuckDB cannot
    parse `with` inside a set operation's parenthesized branch.
    """
    return """
    expected as (
        select r.*
        from main_marts.mart_trial_similarity_reference r
        semi join main_intermediate.int_similarity_candidates c
            on r.indication_profile_id = c.indication_profile_id
            and r.nct_id_a = c.nct_id_a
            and r.nct_id_b = c.nct_id_b
    ),
    reranked as (
        select *,
            row_number() over (
                partition by indication_profile_id, nct_id_a
                order by similarity_score desc, nct_id_b
            ) as expected_rank
        from expected
    ),
    expected_top25 as (
        select
            trial_similarity_key, indication_profile_id, nct_id_a, nct_id_b,
            similarity_score, expected_rank as similarity_rank,
            same_condition, weight_same_condition, weighted_same_condition,
            same_phase, weight_same_phase, weighted_same_phase,
            geography_overlap, weight_geography_overlap, weighted_geography_overlap,
            intervention_type_overlap, weight_intervention_type_overlap,
            weighted_intervention_type_overlap,
            study_design_match, weight_study_design_match, weighted_study_design_match,
            eligibility_compatible, weight_eligibility_compatible,
            weighted_eligibility_compatible,
            enrollment_band_match, weight_enrollment_band_match,
            weighted_enrollment_band_match,
            similarity_explanation
        from reranked
        where expected_rank <= 25
    )
    """


def _mart_columns_select() -> str:
    return ", ".join(_SIMILARITY_COLUMNS)


def test_mart_equals_reference_restricted_to_candidates(
    similarity_equivalence_root: Path,
) -> None:
    """The equivalence theorem, both directions, over every profile and trial."""
    columns = _mart_columns_select()
    for label, first, second in (
        ("expected rows absent from the mart", "expected_top25", "actual"),
        ("mart rows that are not reference-on-candidates", "actual", "expected_top25"),
    ):
        sql = f"""
        with
        {_expected_sql()},
        actual as (
            select {columns} from main_marts.mart_trial_similarity
        )
        select count(*) from (select * from {first} except select * from {second})
        """
        mismatches = _rows(similarity_equivalence_root, sql)[0][0]
        assert mismatches == 0, f"{mismatches} {label}"


def test_candidate_sets_outnumber_the_cap(
    similarity_equivalence_root: Path,
) -> None:
    """The fixture is dense enough that top-25 cuts inside the candidates.

    A candidate-poor fixture makes the qualify invisible: if every trial's
    candidate count stayed under 25, the equivalence theorem above would pass
    even if the mart dropped the cap entirely. alzheimers_disease's df of 27
    (24 clones + 3 base trials carrying an Alzheimer condition) is what makes
    the cap bind.
    """
    alzheimers_df = _rows(
        similarity_equivalence_root,
        """
        select count(distinct nct_id)
        from main_marts.bridge_trial_condition
        where indication_profile_id = 'adrd'
          and condition_group = 'alzheimers_disease'
        """,
    )[0][0]
    assert alzheimers_df >= 26
    max_rank = _rows(
        similarity_equivalence_root,
        "select max(similarity_rank) from main_marts.mart_trial_similarity "
        "where indication_profile_id = 'adrd'",
    )[0][0]
    assert max_rank == 25


def test_ranks_are_contiguous_per_trial(
    similarity_equivalence_root: Path,
) -> None:
    """Per (profile, nct_id_a): ranks run 1..k with no gaps or repeats."""
    gaps = _rows(
        similarity_equivalence_root,
        """
        select count(*)
        from (
            select indication_profile_id, nct_id_a,
                   count(*) as rows_n,
                   count(distinct similarity_rank) as distinct_ranks,
                   max(similarity_rank) as max_rank,
                   min(similarity_rank) as min_rank
            from main_marts.mart_trial_similarity
            group by 1, 2
        )
        where distinct_ranks != rows_n
           or max_rank != rows_n
           or min_rank != 1
        """,
    )[0][0]
    assert gaps == 0


def test_cap_drops_only_dominant_tokens(similarity_cap_root: Path) -> None:
    """At df 4 the alzheimers token (df 27) leaves discovery; lewy (df 3) stays.

    "Lewy Body Dementia" and "Frontotemporal Dementia" both contain the
    substring "dementia", so dementia_unspecified also crosses the threshold
    (df 4) — the dropped set is therefore asserted by membership on both sides
    of the threshold rather than as an exact list: the property under test is
    the cap's boundary behavior, not the fixture's taxonomy arithmetic.
    """
    token_dfs = dict(
        _rows(
            similarity_cap_root,
            """
            select condition_group, count(distinct nct_id) as trial_count
            from main_marts.bridge_trial_condition
            where indication_profile_id = 'adrd'
            group by 1
            """,
        )
    )
    assert token_dfs["alzheimers_disease"] >= 4
    assert token_dfs["lewy_body_dementia"] < 4

    # A dominant-group-only trial loses every pair; a small-group trial keeps
    # its same-group peers; the reference still carries the old rows for both.
    filler_pairs = _rows(
        similarity_cap_root,
        """
        select count(*)
        from main_marts.mart_trial_similarity
        where indication_profile_id = 'adrd'
          and (nct_id_a = 'NCT00000020' or nct_id_b = 'NCT00000020')
        """,
    )[0][0]
    assert filler_pairs == 0

    filler_reference_pairs = _rows(
        similarity_cap_root,
        """
        select count(*)
        from main_marts.mart_trial_similarity_reference
        where indication_profile_id = 'adrd'
          and (nct_id_a = 'NCT00000020' or nct_id_b = 'NCT00000020')
        """,
    )[0][0]
    assert filler_reference_pairs > 0

    lewy_pairs = _rows(
        similarity_cap_root,
        """
        select count(*)
        from main_marts.mart_trial_similarity
        where indication_profile_id = 'adrd'
          and nct_id_a = 'NCT00000006'
          and nct_id_b in ('NCT00000044', 'NCT00000045')
        """,
    )[0][0]
    assert lewy_pairs == 2


def test_reference_model_is_not_built_without_its_var(
    fixture_project_root: Path,
) -> None:
    """The 28 GB oracle must stay out of every default graph."""
    warehouse = fixture_project_root / "data" / "warehouse" / "clinical_trials.duckdb"
    conn = duckdb.connect(str(warehouse), read_only=True)
    try:
        present = conn.execute(
            """
            select count(*)
            from duckdb_views()
            where schema_name = 'main_marts'
              and view_name = 'mart_trial_similarity_reference'
            """
        ).fetchone()
    finally:
        conn.close()
    assert present is not None and present[0] == 0
