"""Warehouse-level tests for baseline demographics and coverage (issue #74).

Reads the `demographics_fixture_root` warehouse (the base two runs plus two
ADRD runs: page=00001's five studies with the messy real label shapes, and
page=00002 with NCT10000003's amended sex-only snapshot and NCT10000006's
denominator-scope and word-boundary-title shapes).
The expected label outcomes below are the reviewed decisions recorded in
dbt_clinical_trials/seeds/omb_race_ethnicity_mapping.csv; change the seed and
these tests together.
"""

import os
from pathlib import Path

import duckdb


def _rows(root: Path, sql: str) -> list[tuple]:
    con = duckdb.connect(str(root / "data/warehouse/clinical_trials.duckdb"), read_only=True)
    start = Path(os.getcwd())
    try:
        os.chdir(root)
        return con.execute(sql).fetchall()
    finally:
        os.chdir(start)
        con.close()


def _one(root: Path, sql: str) -> tuple:
    rows = _rows(root, sql)
    assert len(rows) == 1, f"expected 1 row, got {len(rows)}: {sql}"
    return rows[0]


class TestStagingRows:
    def test_row_count_only_from_demographics_run(self, demographics_fixture_root: Path) -> None:
        (total,) = _one(
            demographics_fixture_root,
            "select count(*) from main_staging.stg_trial_baseline_demographics",
        )
        # 26 + 22 + 18 + 6 + 14 measurements across the five page=00001 studies,
        # + 4 (NCT10000003's amended sex-only snapshot) + 18 (NCT10000006) from
        # page=00002.
        assert total == 108
        (runs,) = _one(
            demographics_fixture_root,
            "select count(distinct ingestion_run_id)"
            " from main_staging.stg_trial_baseline_demographics",
        )
        assert runs == 2

    def test_profile_stamped(self, demographics_fixture_root: Path) -> None:
        (profiles,) = _one(
            demographics_fixture_root,
            "select count(distinct indication_profile_id)"
            " from main_staging.stg_trial_baseline_demographics",
        )
        assert profiles == 1

    def test_dimension_classification(self, demographics_fixture_root: Path) -> None:
        rows = _rows(
            demographics_fixture_root,
            """select dimension, count(*) from main_staging.stg_trial_baseline_demographics
               group by 1 order by 1""",
        )
        by_dimension = dict(rows)
        assert by_dimension == {
            "age": 6,
            "ethnicity": 12,
            "other": 2,
            "race": 18,
            "race_ethnicity_combined": 44,
            "race_ethnicity_not_collected": 2,
            "sex": 24,
        }

    def test_measure_title_word_boundaries(self, demographics_fixture_root: Path) -> None:
        rows = _rows(
            demographics_fixture_root,
            """select measure_title, dimension
               from main_staging.stg_trial_baseline_demographics
               where nct_id = 'NCT10000006'
               group by 1, 2""",
        )
        # Review round 1, finding 4: "Gender" is sex, "Mean Age" is age, and
        # "GRACE score" is not race — plain substring matching got all three wrong.
        assert dict(rows) == {
            "Gender": "sex",
            "Mean Age (Years)": "age",
            "GRACE score": "other",
            "Race (NIH/OMB)": "race",
            "Ethnicity (NIH/OMB)": "ethnicity",
        }

    def test_denominator_taken_from_the_measure_scope(
        self, demographics_fixture_root: Path
    ) -> None:
        rows = _rows(
            demographics_fixture_root,
            """select distinct participant_count, group_denominator, percent_of_group_denominator
               from main_staging.stg_trial_baseline_demographics
               where nct_id = 'NCT10000006' and measure_title = 'Race (NIH/OMB)'
                 and category_title = 'White'""",
        )
        # Review round 1, finding 2 (mirrors live NCT03046121): the module
        # denominates 458/464 participants, but the race measure denominates its
        # own groups at 229/232 — the percent base must be the measure's.
        assert sorted(rows) == [(134, 229, 134 / 229), (140, 232, 140 / 232)]

    def test_module_fallback_ignores_non_participant_units(
        self, demographics_fixture_root: Path
    ) -> None:
        rows = _rows(
            demographics_fixture_root,
            """select distinct group_denominator
               from main_staging.stg_trial_baseline_demographics
               where nct_id = 'NCT10000006' and measure_title = 'GRACE score'""",
        )
        # NCT10000006's module denoms list "hospital units" (3/3) before
        # "Participants" (458/464); the first entry must not win.
        assert sorted(rows) == [(458,), (464,)]

    def test_race_rows_only_in_the_superseded_run(self, demographics_fixture_root: Path) -> None:
        rows = _rows(
            demographics_fixture_root,
            """select ingestion_run_id, count(*) filter (resolved_dimension = 'race')
               from main_staging.stg_trial_baseline_demographics
               where nct_id = 'NCT10000003'
               group by 1 order by 1""",
        )
        # The amended 13:00 snapshot keeps only the sex table; NCT10000003's
        # race rows exist solely in the superseded 12:00 snapshot (review
        # round 1, finding 3).
        assert rows == [("20260901T120002Z_fixture05", 6), ("20260901T130000Z_fixture07", 0)]


class TestOMBMapping:
    def test_mapping_seed_loaded(self, demographics_fixture_root: Path) -> None:
        (count,) = _one(
            demographics_fixture_root,
            "select count(*) from main_seeds.omb_race_ethnicity_mapping",
        )
        assert count == 15

    def _label_rows(self, root: Path, label: str) -> list[tuple]:
        return _rows(
            root,
            f"""select omb_category, resolved_dimension, mapping_confidence,
                       mapping_manually_reviewed, mapping_unmapped_flag,
                       is_ambiguous_combined_flag
                from main_staging.stg_trial_baseline_demographics
                where category_title = '{label}'
                group by 1, 2, 3, 4, 5, 6""",
        )

    def test_combined_measure_race_label_maps_via_seed_scope(
        self, demographics_fixture_root: Path
    ) -> None:
        # "White" inside a combined table: the reviewed seed row itself scopes
        # the label to race, so it maps without any dimension guess.
        rows = self._label_rows(demographics_fixture_root, "White")
        assert rows == [("White", "race", "high", True, False, False)]

    def test_combined_measure_custom_buckets_stay_unmapped_and_flagged(
        self, demographics_fixture_root: Path
    ) -> None:
        rows = self._label_rows(
            demographics_fixture_root, "Not Reported Due to Confidentiality Regulations"
        )
        assert rows == [(None, None, None, None, True, True)]
        rows = self._label_rows(demographics_fixture_root, "Other")
        assert rows == [(None, None, None, None, True, True)]

    def test_combined_multiple_race_label_maps_medium_confidence(
        self, demographics_fixture_root: Path
    ) -> None:
        rows = self._label_rows(
            demographics_fixture_root, "Multiple: American Indian or Alaska Native and White"
        )
        assert rows == [("More than one race", "race", "medium", True, False, False)]

    def test_class_titled_labels_map(self, demographics_fixture_root: Path) -> None:
        rows = _rows(
            demographics_fixture_root,
            """select omb_category, resolved_dimension, is_ambiguous_combined_flag
               from main_staging.stg_trial_baseline_demographics
               where class_title = 'Hispanic or Latino' and category_title is null
               group by 1, 2, 3""",
        )
        assert rows == [("Hispanic or Latino", "ethnicity", False)]

    def test_class_titled_ambiguous_labels_flagged(self, demographics_fixture_root: Path) -> None:
        for label in ("Not Stated", "Unknown"):
            rows = _rows(
                demographics_fixture_root,
                f"""select omb_category, is_ambiguous_combined_flag
                    from main_staging.stg_trial_baseline_demographics
                    where class_title = '{label}' and category_title is null
                    group by 1, 2""",
            )
            assert rows == [(None, True)], label

    def test_unknown_not_reported_maps_only_on_single_dimension_measure(
        self, demographics_fixture_root: Path
    ) -> None:
        rows = _rows(
            demographics_fixture_root,
            """select dimension, omb_category, mapping_confidence, resolved_dimension,
                      is_ambiguous_combined_flag
               from main_staging.stg_trial_baseline_demographics
               where category_title = 'Unknown or Not Reported'
               group by 1, 2, 3, 4, 5""",
        )
        # Race measure: mapped (seed applies_to 'both' + single-dimension measure).
        # Ethnicity measure: mapped the same way — both groups carry the label.
        assert all(row[1] == "Unknown or Not Reported" and row[2] == "high" for row in rows)
        assert {row[0] for row in rows} == {"race", "ethnicity"}
        assert all(row[4] is False for row in rows)

    def test_prefixed_labels_resolve_dimension_and_strip_prefix(
        self, demographics_fixture_root: Path
    ) -> None:
        rows = _rows(
            demographics_fixture_root,
            """select omb_category, resolved_dimension, is_ambiguous_combined_flag
               from main_staging.stg_trial_baseline_demographics
               where category_title in ('Race: White', 'Ethnicity: Hispanic or Latino')
               group by 1, 2, 3""",
        )
        assert sorted(rows) == [
            ("Hispanic or Latino", "ethnicity", False),
            ("White", "race", False),
        ]

    def test_prefixed_declined_label_stays_unmapped_without_ambiguity(
        self, demographics_fixture_root: Path
    ) -> None:
        rows = self._label_rows(demographics_fixture_root, "Ethnicity: Prefer not to say")
        # Prefix resolves the dimension (ethnicity), so it is not ambiguous —
        # but "Prefer not to say" has no reviewed OMB equivalent and stays unmapped.
        assert rows == [(None, "ethnicity", None, None, True, False)]

    def test_sex_rows_never_flagged_as_unmapped(self, demographics_fixture_root: Path) -> None:
        (count, unmapped) = _one(
            demographics_fixture_root,
            """select count(*),
                      count(*) filter (mapping_unmapped_flag or is_ambiguous_combined_flag)
               from main_staging.stg_trial_baseline_demographics
               where dimension = 'sex'""",
        )
        assert count == 24
        assert unmapped == 0

    def test_percent_share_computed_where_denominator_exists(
        self, demographics_fixture_root: Path
    ) -> None:
        rows = _rows(
            demographics_fixture_root,
            """select distinct participant_count, group_denominator, percent_of_group_denominator
               from main_staging.stg_trial_baseline_demographics
               where nct_id = 'NCT10000001'
                 and measure_title = 'Ethnicity (NIH/OMB)'
                 and category_title = 'Hispanic or Latino'""",
        )
        assert sorted(rows) == [(3, 78, 3 / 78), (4, 68, 4 / 68)]


class TestCoverageMart:
    def test_denominator_is_completed_trials_with_posted_results(
        self, demographics_fixture_root: Path
    ) -> None:
        rows = _rows(
            demographics_fixture_root,
            """select condition_group, completion_year, trials_with_results_count
               from main_marts.mart_baseline_demographics_coverage
               order by 1, 2""",
        )
        # Only the demographics runs carry baseline sections, but the base
        # fixtures already hold two COMPLETED trials with posted results and no
        # demographics (ADRD "Type 2 Diabetes" -> non_dementia_other, NSCLC
        # squamous) — both stay in the denominator with zero reporting.
        assert rows == [
            ("alzheimers_disease", 2022, 1),
            ("alzheimers_disease", 2024, 2),
            ("frontotemporal_dementia", 2023, 1),
            ("lewy_body_dementia", 2025, 1),
            ("mild_cognitive_impairment", 2025, 1),
            ("non_dementia_other", 2025, 1),
            ("nsclc_squamous", 2025, 1),
        ]

    def test_coverage_counts_and_shares(self, demographics_fixture_root: Path) -> None:
        rows = _rows(
            demographics_fixture_root,
            """select condition_group, completion_year,
                      reports_sex_count, reports_age_count,
                      reports_race_count, reports_ethnicity_count,
                      ambiguous_combined_trials_count,
                      reports_age_share, reports_ethnicity_share,
                      ambiguous_combined_share
               from main_marts.mart_baseline_demographics_coverage
               order by 1, 2""",
        )
        # alzheimers 2024 = canonical study + combined-category study: age only
        # on the canonical one; ethnicity only on the canonical one; ambiguity
        # from the combined table's unresolvable custom buckets.
        # alzheimers 2022 = NCT10000006: Gender + Mean Age + NIH/OMB measures
        # all report, nothing ambiguous.
        assert rows == [
            ("alzheimers_disease", 2022, 1, 1, 1, 1, 0, 1.0, 1.0, 0.0),
            ("alzheimers_disease", 2024, 2, 1, 2, 1, 1, 0.5, 0.5, 0.5),
            # FTD study: prefixed labels resolve, no sex measure at all.
            ("frontotemporal_dementia", 2023, 0, 1, 1, 1, 0, 1.0, 1.0, 0.0),
            # DLB study: sex reported, race/ethnicity explicitly not collected.
            ("lewy_body_dementia", 2025, 1, 0, 0, 0, 0, 0.0, 0.0, 0.0),
            # MCI study: the current (09-02) snapshot reports sex only. Its
            # race/ethnicity rows live in the superseded 09-01 snapshot and
            # must not count (review round 1, finding 3): a nct_id-only join
            # reported this row as (1, 0, 1, 1, 1, 0.0, 1.0, 1.0).
            ("mild_cognitive_impairment", 2025, 1, 0, 0, 0, 0, 0.0, 0.0, 0.0),
            # Base-fixture trials with posted results but no demographics data.
            ("non_dementia_other", 2025, 0, 0, 0, 0, 0, 0.0, 0.0, 0.0),
            ("nsclc_squamous", 2025, 0, 0, 0, 0, 0, 0.0, 0.0, 0.0),
        ]

    def test_not_collected_trial_excluded_from_race_ethnicity_numerators(
        self, demographics_fixture_root: Path
    ) -> None:
        rows = _rows(
            demographics_fixture_root,
            """select reports_race_count, reports_ethnicity_count, ambiguous_combined_trials_count
               from main_marts.mart_baseline_demographics_coverage
               where condition_group = 'lewy_body_dementia'""",
        )
        assert rows == [(0, 0, 0)]
