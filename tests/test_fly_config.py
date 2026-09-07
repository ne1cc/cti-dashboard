"""fly.toml must keep covering a real pipeline run.

The number below is the measurement it was set from, not a guess: the same
dated figure docs/DEPLOY_FLY.md quotes. A widening that doubles the run without
touching kill_timeout is exactly the failure that made the first week in
production look healthy while it refreshed nothing (docs/DEPLOY_FLY.md, "What the
first week in production caught").

How the measurement was taken, so it can be repeated rather than re-guessed:
`docker build -t cti-budget .`, then a fresh `docker volume create` mounted at
/app/data, then `make pipeline` (orchestrate -> prune-data -> dbt-run -> dbt-test
-> quality-report) for both refreshable profiles, timed with `date +%s`.
"""

import tomllib
from pathlib import Path

FLY_TOML = Path(__file__).resolve().parents[1] / "fly.toml"

MEASURED_PIPELINE_SECONDS = 130  # two-profile max, measured 2026-09-07, commit 78c2050
MARGIN_MULTIPLIER = 2


def _kill_timeout_seconds() -> int:
    raw = tomllib.loads(FLY_TOML.read_text(encoding="utf-8"))["kill_timeout"]
    assert isinstance(raw, str) and raw.endswith("s"), raw
    return int(raw[:-1])


def test_kill_timeout_covers_two_measured_pipeline_runs() -> None:
    assert _kill_timeout_seconds() >= MARGIN_MULTIPLIER * MEASURED_PIPELINE_SECONDS


def test_kill_timeout_is_not_padded_past_its_rule() -> None:
    """A timeout set by a rule stays honest only while something can check it.
    Rounding up to 30s costs at most 29s of slow shutdown; more than that means
    the number was edited, not measured."""
    assert _kill_timeout_seconds() <= MARGIN_MULTIPLIER * MEASURED_PIPELINE_SECONDS + 29
