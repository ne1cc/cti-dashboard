"""Falsification test of the recruiting-density proxy using the project's own snapshots.

Claim tested: trials in segments with higher `recruiting_trial_count` (potential
competition signal) later leave RECRUITING for a terminal status at a different
rate than trials in lower-density segments. Inconclusive or null results are
reported as such. This is not an enrollment prediction; see
docs/clinical_interpretation_guardrails.md.

Usage:
    uv run python -m src.analysis.density_proxy_test [--db PATH]
"""

from __future__ import annotations

import argparse
from pathlib import Path

import duckdb

MIN_BIN_N = 30
TERMINAL = ("TERMINATED", "WITHDRAWN", "SUSPENDED")
TABLES = (
    "mart_recruiting_competition",
    "fct_trial_snapshot",
    "fct_trial_site",
    "bridge_trial_condition",
)
BINS = ("low", "mid", "high")

# Trial-in-segment observations: RECRUITING at t, outcome read at the profile's next snapshot.
# Terciles are taken across states within profile x condition_group x phase x snapshot.
# shortcut: ntile breaks ties by state name, so heavy ties blur bins; groups with a single
# density value are dropped. Upgrade if real data shows many ties.
_SQL = """
with snaps as (
    select distinct indication_profile_id as p, snapshot_date as t from fct_trial_snapshot
),
nxt as (
    select p, t, lead(t) over (partition by p order by t) as t1 from snaps
),
binned as (
    select indication_profile_id as p, snapshot_date as t, condition_group, state_normalized as st,
        phase_normalized as ph, recruiting_trial_count as n,
        ntile(3) over (
            partition by indication_profile_id, snapshot_date, condition_group, phase_normalized
            order by recruiting_trial_count, state_normalized
        ) as b,
        min(recruiting_trial_count) over w as lo, max(recruiting_trial_count) over w as hi
    from mart_recruiting_competition
    window w as (
        partition by indication_profile_id, snapshot_date, condition_group, phase_normalized
    )
),
members as (
    select distinct s.nct_id, s.indication_profile_id as p, s.snapshot_date as t,
        c.condition_group, f.state_normalized as st, s.phase_normalized as ph
    from fct_trial_snapshot s
    join bridge_trial_condition c using (nct_id, indication_profile_id)
    join fct_trial_site f on f.nct_id = s.nct_id
        and f.indication_profile_id = s.indication_profile_id and f.snapshot_date = s.snapshot_date
    where s.overall_status = 'RECRUITING'
)
select b.b, m.nct_id, n.t1, o.overall_status as next_status
from members m
join binned b using (p, t, condition_group, st, ph)
join nxt n using (p, t)
left join fct_trial_snapshot o on o.nct_id = m.nct_id
    and o.indication_profile_id = m.p and o.snapshot_date = n.t1
where b.hi > b.lo and n.t1 is not null
"""


def _use_mart_schemas(con: duckdb.DuckDBPyConnection) -> None:
    rows = con.execute(
        f"select distinct table_schema from information_schema.tables where table_name in {TABLES}"
    ).fetchall()
    schemas = ["main"] + [r[0] for r in rows if r[0] != "main"]
    con.execute(f"SET search_path = '{','.join(schemas)}'")


def run_test(con: duckdb.DuckDBPyConnection) -> str:
    """Return a printable report for the density-proxy falsification test."""
    _use_mart_schemas(con)
    try:
        n_snaps = con.execute(
            "select coalesce(min(c), 0) from (select count(distinct snapshot_date) c "
            "from fct_trial_snapshot group by indication_profile_id)"
        ).fetchone()[0]  # type: ignore[index]
    except duckdb.Error as exc:
        return f"INCONCLUSIVE: required tables are missing or unreadable ({exc})."
    if n_snaps < 2:
        return (
            "INCONCLUSIVE: fewer than 2 snapshots in at least one profile; "
            "no snapshot t -> t+1 comparison is possible."
        )
    rows = con.execute(_SQL).fetchall()
    stats = {i: {"n": 0, "term": 0, "rec": 0, "absent": 0} for i in (1, 2, 3)}
    for b, _nct, _t1, status in rows:
        s = stats[b]
        if status is None:
            s["absent"] += 1
            continue
        s["n"] += 1
        s["term"] += status in TERMINAL
        s["rec"] += status == "RECRUITING"
    lines = [
        "Density proxy falsification (trial-in-segment obs, RECRUITING at t, outcome at t+1)",
        f"{'bin':<6}{'n':>8}{'absent_t1':>11}{'left->terminal':>16}{'still_recruiting':>18}",
    ]
    for i, name in enumerate(BINS, 1):
        s = stats[i]
        term, rec = (f"{s[k]} ({s[k] / s['n']:.1%})" if s["n"] else "n/a" for k in ("term", "rec"))
        lines.append(f"{name:<6}{s['n']:>8}{s['absent']:>11}{term:>16}{rec:>18}")
    small = [BINS[i - 1] for i in stats if stats[i]["n"] < MIN_BIN_N]
    if small:
        lines.append(
            f"INCONCLUSIVE: bins too small (n<{MIN_BIN_N}): {', '.join(small)}. "
            "Rates above are descriptive only."
        )
    else:
        gap = stats[3]["term"] / stats[3]["n"] - stats[1]["term"] / stats[1]["n"]
        lines.append(
            f"High-minus-low terminal-exit rate gap: {gap:+.1%} (descriptive; no significance "
            "test, observations overlap across states and snapshots). Near 0 is a null result."
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--db", type=Path, default=Path("data/warehouse/clinical_trials.duckdb"))
    args = parser.parse_args()
    con = duckdb.connect(str(args.db), read_only=True)
    try:
        print(run_test(con))
    finally:
        con.close()


if __name__ == "__main__":
    main()
