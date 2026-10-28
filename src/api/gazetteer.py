"""Offline U.S. Census Gazetteer centroid lookup (Issue #63).

Loads the public-domain Census Gazetteer ZCTA and place centroid CSVs
(downloaded once via `make gazetteer` into a local reference directory) and
exposes them two ways:

- `resolve_postal_code`: 5-digit ZCTA -> GeoPoint, used to turn a
  patient's postal code into search coordinates. No third-party geocoding
  API is contacted and no user address is stored.
- `register_gazetteer_views`: registers `gazetteer_zcta` / `gazetteer_place`
  as temporary DuckDB views so warehouse SQL can fall back to ZIP/city
  centroids for trial sites missing registry `geoPoint` coordinates.

Both views exist even when the reference files are missing (empty), so
queries stay bindable; lookups simply return no fallback coordinates.
"""

from __future__ import annotations

import csv
import logging
import os
from functools import lru_cache
from pathlib import Path

import duckdb

from src.api.models import GeoPoint
from src.utils.paths import project_root

logger = logging.getLogger(__name__)

GAZETTEER_DIR_ENV = "CTI_GAZETTEER_DIR"
DEFAULT_GAZETTEER_DIR = Path("data/reference")
ZCTA_FILENAME = "gazetteer_zcta.csv"
PLACE_FILENAME = "gazetteer_place.csv"

# Census Gazetteer column names (stable across recent gazetteer releases).
_ZCTA_ID_COLUMN = "GEOID"
_LAT_COLUMN = "INTPTLAT"
_LON_COLUMN = "INTPTLONG"
_PLACE_STATE_COLUMN = "USPS"
_PLACE_NAME_COLUMN = "NAME"


def resolve_gazetteer_dir(gazetteer_dir: str | Path | None = None) -> Path:
    """Explicit argument -> CTI_GAZETTEER_DIR -> <project root>/data/reference."""
    if gazetteer_dir is not None:
        return Path(gazetteer_dir)
    env_dir = os.environ.get(GAZETTEER_DIR_ENV)
    if env_dir:
        return Path(env_dir)
    return project_root() / DEFAULT_GAZETTEER_DIR


def _sql_string(path: Path) -> str:
    return "'" + str(path).replace("'", "''") + "'"


@lru_cache(maxsize=8)
def _load_zcta_centroids(csv_path: str) -> dict[str, tuple[float, float]]:
    centroids: dict[str, tuple[float, float]] = {}
    try:
        with open(csv_path, encoding="utf-8-sig", newline="") as handle:
            # Census Gazetteer releases are tab-separated; keep comma working
            # for hand-made or re-exported files.
            first_line = handle.readline()
            handle.seek(0)
            delimiter = "\t" if "\t" in first_line else ","
            for row in csv.DictReader(handle, delimiter=delimiter):
                # Census files pad header names (and the whole line) with spaces.
                row = {key.strip(): value for key, value in row.items() if key}
                try:
                    zcta = (row.get(_ZCTA_ID_COLUMN) or "").strip()
                    lat = float((row.get(_LAT_COLUMN) or "").strip())
                    lon = float((row.get(_LON_COLUMN) or "").strip())
                except ValueError:
                    continue
                if len(zcta) == 5 and -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0:
                    centroids[zcta] = (lat, lon)
    except OSError:
        return {}
    return centroids


def resolve_postal_code(
    postal_code: str, gazetteer_dir: str | Path | None = None
) -> GeoPoint | None:
    """Resolve a 5-digit postal code to its Gazetteer ZCTA centroid, or None."""
    cleaned = (postal_code or "").strip()
    if len(cleaned) != 5 or not cleaned.isdigit():
        return None
    csv_path = resolve_gazetteer_dir(gazetteer_dir) / ZCTA_FILENAME
    point = _load_zcta_centroids(str(csv_path)).get(cleaned)
    if point is None:
        return None
    return GeoPoint(lat=point[0], lon=point[1])


# Census Gazetteer place names carry a trailing type word ("Seattle city",
# "Abbeville CDP") in some releases and not others ("Seattle"). Registry city
# values never carry it, so the view exposes both the full normalized name and
# the suffix-stripped form; SQL joins on either.
_PLACE_SUFFIX_PATTERN = (
    " (city|town|township|village|borough|cdp|municipality|plantation|balance|territory)"
)


def _normalized_place_expr() -> str:
    return (
        "regexp_replace(regexp_replace(lower(trim(CAST("
        f'"{_PLACE_NAME_COLUMN}" AS VARCHAR))), '
        "'[^a-z0-9 ]', ' ', 'g'), '\\s+', ' ', 'g')"
    )


def register_gazetteer_views(
    conn: duckdb.DuckDBPyConnection, gazetteer_dir: str | Path | None = None
) -> None:
    """Create temporary `gazetteer_zcta` / `gazetteer_place` views for SQL.

    Missing reference files yield empty views with the same columns, so
    downstream queries always bind and simply find no fallback coordinates.
    A present-but-unreadable file (e.g. a truncated download) degrades the
    same way instead of failing every warehouse connection.
    """
    directory = resolve_gazetteer_dir(gazetteer_dir)
    zcta_csv = directory / ZCTA_FILENAME
    place_csv = directory / PLACE_FILENAME

    zcta_sql = f"""
        CREATE OR REPLACE TEMP VIEW gazetteer_zcta AS
        SELECT
            CAST("{_ZCTA_ID_COLUMN}" AS VARCHAR) AS zcta,
            CAST("{_LAT_COLUMN}" AS DOUBLE) AS latitude,
            CAST("{_LON_COLUMN}" AS DOUBLE) AS longitude
        FROM read_csv({_sql_string(zcta_csv)}, header = true, all_varchar = true)
    """
    zcta_empty_sql = """
        CREATE OR REPLACE TEMP VIEW gazetteer_zcta AS
        SELECT
            CAST(NULL AS VARCHAR) AS zcta,
            CAST(NULL AS DOUBLE) AS latitude,
            CAST(NULL AS DOUBLE) AS longitude
        WHERE false
    """
    place_sql = f"""
        CREATE OR REPLACE TEMP VIEW gazetteer_place AS
        SELECT
            CAST("{_PLACE_STATE_COLUMN}" AS VARCHAR) AS state_usps,
            {_normalized_place_expr()} AS city_name,
            regexp_replace(
                {_normalized_place_expr()},
                '{_PLACE_SUFFIX_PATTERN}$', '', 'g'
            ) AS city_name_stripped,
            CAST("{_LAT_COLUMN}" AS DOUBLE) AS latitude,
            CAST("{_LON_COLUMN}" AS DOUBLE) AS longitude
        FROM read_csv({_sql_string(place_csv)}, header = true, all_varchar = true)
    """
    place_empty_sql = """
        CREATE OR REPLACE TEMP VIEW gazetteer_place AS
        SELECT
            CAST(NULL AS VARCHAR) AS state_usps,
            CAST(NULL AS VARCHAR) AS city_name,
            CAST(NULL AS VARCHAR) AS city_name_stripped,
            CAST(NULL AS DOUBLE) AS latitude,
            CAST(NULL AS DOUBLE) AS longitude
        WHERE false
    """

    for create_sql, fallback_sql in ((zcta_sql, zcta_empty_sql), (place_sql, place_empty_sql)):
        try:
            conn.execute(create_sql)
        except duckdb.Error:
            logger.warning("Gazetteer reference unreadable; falling back to empty view")
            conn.execute(fallback_sql)
