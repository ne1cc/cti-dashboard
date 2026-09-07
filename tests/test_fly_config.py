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

MEASURED_PIPELINE_SECONDS = 130
# two-profile max, measured 2026-09-07, commit 78c2050. Re-measure (the recipe is
# in this file's docstring) after any change to the number of refreshable profiles
# or the pipeline steps — this constant is the only thing the tests below bound
# kill_timeout against, and it does not re-measure itself.
MARGIN_MULTIPLIER = 2
FLY_KILL_TIMEOUT_MAX_SECONDS = 300  # fly.io/docs/reference/configuration, 2026-09-07


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


def test_kill_timeout_rule_still_fits_flys_ceiling() -> None:
    """The two asserts above only coexist while 2x the measurement plus up to 29s
    of round-up-to-30s padding fits under Fly's hard 300s kill_timeout maximum.
    That band closes at MEASURED_PIPELINE_SECONDS > 135: past it, no compliant
    kill_timeout exists at all, and the only honest green is not a wider margin."""
    assert MARGIN_MULTIPLIER * MEASURED_PIPELINE_SECONDS + 29 <= FLY_KILL_TIMEOUT_MAX_SECONDS, (
        f"2 x {MEASURED_PIPELINE_SECONDS}s plus up to 29s of rounding exceeds Fly's "
        f"{FLY_KILL_TIMEOUT_MAX_SECONDS}s kill_timeout maximum: split the pipeline or cut "
        "the run, do not widen the margin (the refresh must become interruptible, not get a "
        "bigger timeout — a slower measured run cannot be covered inside the cap)."
    )
