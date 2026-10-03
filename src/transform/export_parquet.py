"""Parquet export for silver entities: data/silver/<entity>/run_id=<id>.parquet."""

import os
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from src.utils.paths import ensure_dir

ENTITY_COLUMNS: dict[str, list[str]] = {
    "silver_trials": [
        "ingestion_run_id",
        "indication_profile_id",
        "snapshot_id",
        "retrieved_at_utc",
        "raw_page_reference",
        "raw_study_ordinal",
        "snapshot_timestamp_utc",
        "nct_id",
        "brief_title",
        "official_title",
        "study_type",
        "overall_status",
        "last_known_status",
        "status_verified_date",
        "expanded_access_info",
        "start_date",
        "primary_completion_date",
        "completion_date",
        "study_first_post_date",
        "results_first_post_date",
        "last_update_post_date",
        "phase_raw",
        "phase_normalized",
        "enrollment_count",
        "enrollment_type",
        # flatten_study emits these trial columns beyond the original
        # ENTITY_COLUMNS list; the old pandas export preserved them and
        # dbt staging (stg_trials) selects them, so they are part of the
        # on-disk contract.
        "allocation",
        "primary_purpose",
        "lead_sponsor_name",
        "responsible_party_type",
        "healthy_volunteers",
        "minimum_age",
        "maximum_age",
        "sex",
        "eligibility_criteria_text",
        "has_results_flag",
        "source_json_hash",
        "record_quality_flag",
    ],
    "silver_trial_conditions": [
        "ingestion_run_id",
        "indication_profile_id",
        "snapshot_id",
        "retrieved_at_utc",
        "raw_page_reference",
        "raw_study_ordinal",
        "nct_id",
        "source_json_hash",
        "condition_raw",
        "condition_normalized",
        "condition_group",
        "dementia_relevance_flag",
        "mapping_confidence",
    ],
    "silver_trial_interventions": [
        "ingestion_run_id",
        "indication_profile_id",
        "snapshot_id",
        "retrieved_at_utc",
        "raw_page_reference",
        "raw_study_ordinal",
        "nct_id",
        "source_json_hash",
        "intervention_name",
        "intervention_type",
        "intervention_description",
        "intervention_normalized",
    ],
    "silver_trial_sponsors": [
        "ingestion_run_id",
        "indication_profile_id",
        "snapshot_id",
        "retrieved_at_utc",
        "raw_page_reference",
        "raw_study_ordinal",
        "nct_id",
        "source_json_hash",
        "sponsor_name",
        "sponsor_role",
        "sponsor_class",
        "sponsor_normalized",
    ],
    "silver_trial_locations": [
        "ingestion_run_id",
        "indication_profile_id",
        "snapshot_id",
        "retrieved_at_utc",
        "raw_page_reference",
        "raw_study_ordinal",
        "nct_id",
        "source_json_hash",
        "location_ordinal",
        "facility_name",
        "facility_normalized",
        "city",
        "state",
        "state_normalized",
        "zip_code",
        "country",
        "geo_scope",
        "latitude",
        "longitude",
        "location_status",
        "us_location_flag",
        "usable_geography_flag",
    ],
    "silver_trial_outcomes": [
        "ingestion_run_id",
        "indication_profile_id",
        "snapshot_id",
        "retrieved_at_utc",
        "raw_page_reference",
        "raw_study_ordinal",
        "nct_id",
        "source_json_hash",
        "outcome_type",
        "outcome_index",
        "outcome_measure",
        "outcome_description",
        "time_frame",
    ],
}

# Chunked writing needs one fixed schema per entity for the whole run: pandas
# inference cannot survive it (an all-null chunk infers Arrow `null` and
# conflicts with the next flush; an int-only chunk infers int64 where later
# chunks carry floats). These types mirror what inference produced on real
# silver data; non-listed columns are string, which also covers all-null
# columns like last_known_status.
_NON_STRING_ARROW_TYPES: dict[str, pa.DataType] = {
    "enrollment_count": pa.float64(),
    "latitude": pa.float64(),
    "longitude": pa.float64(),
    "outcome_index": pa.int64(),
    "raw_study_ordinal": pa.int64(),
    "location_ordinal": pa.int64(),
    "expanded_access_info": pa.bool_(),
    "healthy_volunteers": pa.bool_(),
    "has_results_flag": pa.bool_(),
    "dementia_relevance_flag": pa.bool_(),
    "us_location_flag": pa.bool_(),
    "usable_geography_flag": pa.bool_(),
}

ENTITY_ARROW_SCHEMAS: dict[str, pa.Schema] = {
    entity: pa.schema(
        [pa.field(col, _NON_STRING_ARROW_TYPES.get(col, pa.string())) for col in cols]
    )
    for entity, cols in ENTITY_COLUMNS.items()
}

DEFAULT_FLUSH_ROWS = 50_000


def _table_from_rows(rows: list[dict[str, Any]], schema: pa.Schema) -> pa.Table:
    # row.get: missing keys become null, matching the previous pandas
    # behaviour; extra keys are dropped instead of widening the schema.
    arrays = [pa.array([row.get(field.name) for row in rows], type=field.type) for field in schema]
    return pa.Table.from_arrays(arrays, schema=schema)


class SilverRunWriter:
    """Streams flattened rows into one Parquet file per entity for a run.

    Rows are buffered per entity and flushed as a row group whenever the
    buffer reaches flush_rows, so peak memory is bounded by flush size rather
    than run size. Files are written to `<name>.parquet.tmp` and moved into
    place on close(), so a failed run never leaves a half-written file for
    dbt globs or _already_transformed to see.
    """

    def __init__(self, run_id: str, silver_dir: Path, flush_rows: int = DEFAULT_FLUSH_ROWS):
        self.run_id = run_id
        self.silver_dir = silver_dir
        self.flush_rows = flush_rows
        self.row_counts: dict[str, int] = {name: 0 for name in ENTITY_ARROW_SCHEMAS}
        self._buffers: dict[str, list[dict[str, Any]]] = {name: [] for name in ENTITY_ARROW_SCHEMAS}
        self._writers: dict[str, pq.ParquetWriter] = {}
        self._tmp_paths: dict[str, Path] = {}
        self._closed = False

    def __enter__(self) -> "SilverRunWriter":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._closed:
            return
        if exc_type is None:
            self.close()
        else:
            self.discard()

    def add_rows(self, rows_by_entity: dict[str, list[dict[str, Any]]]) -> None:
        for entity, rows in rows_by_entity.items():
            if not rows:
                continue
            buffer = self._buffers[entity]
            buffer.extend(rows)
            if len(buffer) >= self.flush_rows:
                self._flush(entity)

    def close(self) -> dict[str, int]:
        if self._closed:
            return dict(self.row_counts)
        for entity in ENTITY_ARROW_SCHEMAS:
            self._flush(entity)
            if entity in self._writers:
                self._writers[entity].close()
        for entity in ENTITY_ARROW_SCHEMAS:
            final_path = self._final_path(entity)
            if entity in self._tmp_paths:
                os.replace(self._tmp_paths[entity], final_path)
            else:
                tmp_path = self._tmp_path(entity)
                pq.write_table(ENTITY_ARROW_SCHEMAS[entity].empty_table(), tmp_path)
                os.replace(tmp_path, final_path)
        self._closed = True
        return dict(self.row_counts)

    def discard(self) -> None:
        if self._closed:
            return
        for writer in self._writers.values():
            try:
                writer.close()
            except Exception:
                pass
        for path in self._tmp_paths.values():
            path.unlink(missing_ok=True)
        self._closed = True

    def _flush(self, entity: str) -> None:
        rows = self._buffers[entity]
        if not rows:
            return
        writer = self._writer(entity)
        writer.write_table(_table_from_rows(rows, ENTITY_ARROW_SCHEMAS[entity]))
        self.row_counts[entity] += len(rows)
        self._buffers[entity] = []

    def _writer(self, entity: str) -> pq.ParquetWriter:
        if entity not in self._writers:
            tmp_path = self._tmp_path(entity)
            self._tmp_paths[entity] = tmp_path
            self._writers[entity] = pq.ParquetWriter(
                tmp_path, ENTITY_ARROW_SCHEMAS[entity], compression="snappy"
            )
        return self._writers[entity]

    def _tmp_path(self, entity: str) -> Path:
        return self._entity_dir(entity) / f"run_id={self.run_id}.parquet.tmp"

    def _final_path(self, entity: str) -> Path:
        return self._entity_dir(entity) / f"run_id={self.run_id}.parquet"

    def _entity_dir(self, entity: str) -> Path:
        return ensure_dir(self.silver_dir / entity)


def export_entity(
    rows: list[dict[str, Any]], entity_name: str, run_id: str, silver_dir: Path
) -> tuple[Path, int]:
    with SilverRunWriter(run_id, silver_dir) as writer:
        writer.add_rows({entity_name: rows})
        counts = writer.close()
    return silver_dir / entity_name / f"run_id={run_id}.parquet", counts[entity_name]
