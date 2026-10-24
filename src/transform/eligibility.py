"""ClinicalTrials.gov eligibility age and sex parsing, classification, and validation.

Converts age strings across supported registry units (years, months, weeks, days, hours,
minutes) into explicit fractional years, while preserving raw strings and distinguishing
missing, N/A, malformed, and contradictory bounds.
"""

import re
from dataclasses import dataclass

# Exact unit conversion multipliers to fractional years
# 1 Year = 1.0
# 1 Month = 1/12
# 1 Week = 1/52
# 1 Day = 1/365
# 1 Hour = 1/8760
# 1 Minute = 1/525600
UNIT_MULTIPLIERS = {
    "year": 1.0,
    "years": 1.0,
    "month": 1.0 / 12.0,
    "months": 1.0 / 12.0,
    "week": 1.0 / 52.0,
    "weeks": 1.0 / 52.0,
    "day": 1.0 / 365.0,
    "days": 1.0 / 365.0,
    "hour": 1.0 / 8760.0,
    "hours": 1.0 / 8760.0,
    "minute": 1.0 / 525600.0,
    "minutes": 1.0 / 525600.0,
}

_AGE_PATTERN = re.compile(
    r"^\s*(\d+(?:\.\d+)?)\s*(year|years|month|months|week|weeks|day|days|hour|hours|minute|minutes)\s*$",
    re.IGNORECASE,
)

_NOT_APPLICABLE_VALUES = {"N/A", "NA", "NOT APPLICABLE"}


def parse_age_to_years(raw: str | None) -> float | None:
    """Parse ClinicalTrials.gov age strings to fractional years.

    Returns None for missing, empty, N/A, invalid units, negative, or malformed strings.
    """
    if raw is None:
        return None
    s = raw.strip()
    if not s or s.upper() in _NOT_APPLICABLE_VALUES:
        return None
    match = _AGE_PATTERN.match(s)
    if not match:
        return None
    value_str, unit_str = match.groups()
    try:
        val = float(value_str)
        multiplier = UNIT_MULTIPLIERS[unit_str.lower()]
        return val * multiplier
    except (ValueError, KeyError):
        return None


def classify_age_bound(raw: str | None) -> str:
    """Classify the bound into: 'missing', 'not_applicable', 'valid', or 'malformed'."""
    if raw is None:
        return "missing"
    s = raw.strip()
    if not s:
        return "missing"
    if s.upper() in _NOT_APPLICABLE_VALUES:
        return "not_applicable"
    if parse_age_to_years(s) is not None:
        return "valid"
    return "malformed"


@dataclass(frozen=True)
class AgeBoundsResult:
    min_age_raw: str | None
    max_age_raw: str | None
    min_age_years: float | None
    max_age_years: float | None
    min_bound_status: str  # missing, not_applicable, valid, malformed
    max_bound_status: str  # missing, not_applicable, valid, malformed
    is_valid: bool
    is_contradictory: bool
    has_unspecified_min_age: bool
    has_unspecified_max_age: bool


def validate_age_bounds(min_raw: str | None, max_raw: str | None) -> AgeBoundsResult:
    """Validate paired age bounds, calculating fractional years and bounds integrity."""
    min_years = parse_age_to_years(min_raw)
    max_years = parse_age_to_years(max_raw)

    min_status = classify_age_bound(min_raw)
    max_status = classify_age_bound(max_raw)

    is_contradictory = min_years is not None and max_years is not None and min_years > max_years
    is_valid = (
        min_status in ("missing", "not_applicable", "valid")
        and max_status in ("missing", "not_applicable", "valid")
        and not is_contradictory
    )
    return AgeBoundsResult(
        min_age_raw=min_raw,
        max_age_raw=max_raw,
        min_age_years=min_years,
        max_age_years=max_years,
        min_bound_status=min_status,
        max_bound_status=max_status,
        is_valid=is_valid,
        is_contradictory=is_contradictory,
        has_unspecified_min_age=(min_years is None),
        has_unspecified_max_age=(max_years is None),
    )


def matches_age(
    target_age_years: float,
    min_age_years: float | None,
    max_age_years: float | None,
) -> bool:
    """Determine if a target age matches the trial's age bounds.

    Unstated/unknown bounds are permissive: a trial with no maximum stated is
    not excluded for being older than an unstated limit.
    """
    if min_age_years is not None and target_age_years < min_age_years:
        return False
    if max_age_years is not None and target_age_years > max_age_years:
        return False
    return True
