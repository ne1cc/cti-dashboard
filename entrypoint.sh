#!/usr/bin/env bash
set -euo pipefail

DATA_DIR="/app/data"
WAREHOUSE="$DATA_DIR/warehouse/clinical_trials.duckdb"
MARKER="$DATA_DIR/.last_pipeline_run"
LOG_DIR="$DATA_DIR/logs"
REFRESH_INTERVAL_SECONDS="${CTI_REFRESH_INTERVAL_SECONDS:-604800}"   # 7 days
CHECK_INTERVAL_SECONDS="${CTI_REFRESH_CHECK_SECONDS:-3600}"          # 1 hour
TICK_SECONDS=5
STREAMLIT_BIN="/app/.venv/bin/streamlit"

DASHBOARD_PID=""
SHUTDOWN=0
SINCE_CHECK=0

# The append must not be fatal: on 2026-10-07 a full data volume made this very
# echo fail with ENOSPC, `set -euo pipefail` killed the machine within seconds
# of boot, and the crash loop could not log its own cause. Losing a log line on
# a full disk is acceptable; losing the boot is not.
log() { echo "[entrypoint] $(date -u +%FT%TZ) $*" >> "$LOG_DIR/pipeline.log" 2>/dev/null || true; }

# DuckDB does not create parent directories for a new database file (verified:
# duckdb.connect() raises IOException on a missing directory), and a Fly Volume
# mounts as EMPTY on its very first boot, shadowing whatever the Dockerfile
# baked into this path. Recreate the full tree on every boot from the profile
# registry — a hardcoded list here is how a newly added profile fails on its
# first write in production.
mkdir -p "$LOG_DIR"
/app/.venv/bin/python -m src.cli init-data-dirs >> "$LOG_DIR/pipeline.log" 2>&1 || {
    log "init-data-dirs FAILED; aborting boot rather than half-creating the tree"
    exit 1
}

start_dashboard() {
    "$STREAMLIT_BIN" run dashboard/app.py \
        --server.address=0.0.0.0 \
        --server.port=8501 \
        --server.headless=true &
    DASHBOARD_PID=$!
}

stop_dashboard() {
    if [ -n "$DASHBOARD_PID" ] && kill -0 "$DASHBOARD_PID" 2>/dev/null; then
        kill "$DASHBOARD_PID"
        wait "$DASHBOARD_PID" 2>/dev/null || true
    fi
    DASHBOARD_PID=""
}

request_shutdown() { SHUTDOWN=1; }
trap request_shutdown TERM INT

run_pipeline() {
    log "starting make pipeline"
    if make pipeline >> "$LOG_DIR/pipeline.log" 2>&1; then
        date +%s > "$MARKER"
        log "pipeline succeeded"
        return 0
    fi
    log "pipeline FAILED (marker not updated, will retry)"
    return 1
}

run_similarity_rebuild() {
    log "starting scoped Trial Similarity dbt build"
    if make dbt-seed >> "$LOG_DIR/pipeline.log" 2>&1 \
        && uv run dbt build --project-dir dbt_clinical_trials --profiles-dir dbt_clinical_trials --select +mart_trial_similarity --resource-type model --threads 1 >> "$LOG_DIR/pipeline.log" 2>&1; then
        log "Trial Similarity schema rebuild succeeded"
        return 0
    fi
    log "Trial Similarity schema rebuild FAILED"
    return 1
}

similarity_mart_is_current() {
    [ -f "$WAREHOUSE" ] || return 1
    /app/.venv/bin/python - "$WAREHOUSE" <<'PY'
import duckdb
import sys

try:
    connection = duckdb.connect(sys.argv[1], read_only=True)
    columns = {
        row[0]
        for row in connection.execute(
            "select column_name from information_schema.columns "
            "where table_schema = 'main_marts' and table_name = 'mart_trial_similarity'"
        ).fetchall()
    }
    connection.close()
except Exception:
    raise SystemExit(1)

required = {"indication_profile_id", "nct_id_a", "nct_id_b", "similarity_rank"}
raise SystemExit(0 if required <= columns else 1)
PY
}

refresh_if_due() {
    local now last elapsed
    now=$(date +%s)
    last=0
    [ -f "$MARKER" ] && last=$(cat "$MARKER")
    elapsed=$(( now - last ))
    log "refresh check: now=$now last=$last elapsed=${elapsed}s interval=${REFRESH_INTERVAL_SECONDS}s"
    if [ "$elapsed" -lt "$REFRESH_INTERVAL_SECONDS" ]; then
        return 0
    fi
    # DuckDB allows one read-write connection or read-only ones, never both, and
    # the dashboard caches its read-only connection for the life of the process
    # (dashboard/components/data.py). The pipeline can only take the writer lock
    # while nothing is serving.
    log "refresh due; pausing dashboard to release the warehouse lock"
    stop_dashboard
    run_pipeline || true
    start_dashboard
    log "dashboard serving again (pid=$DASHBOARD_PID)"
}

# First-boot bootstrap: block startup until the warehouse exists at least once,
# so the dashboard never shows "warehouse not found" on a fresh volume.
if [ ! -f "$WAREHOUSE" ]; then
    log "no warehouse found, running first-boot pipeline"
    run_pipeline || log "first-boot pipeline failed; dashboard will show 'warehouse not found' until the next successful refresh"
elif ! similarity_mart_is_current; then
    log "trial similarity mart schema is outdated; rebuilding its dbt dependency graph before serving"
    run_similarity_rebuild || log "schema rebuild failed; dashboard will show a rebuild message for Trial Similarity"
fi

start_dashboard
log "dashboard serving (pid=$DASHBOARD_PID)"

while :; do
    if [ "$SHUTDOWN" -eq 1 ]; then
        log "shutdown requested; stopping dashboard"
        break
    fi
    if ! kill -0 "$DASHBOARD_PID" 2>/dev/null; then
        log "dashboard exited; exiting so Fly restarts the machine"
        exit 1
    fi
    # Background sleep + wait: bash returns from `wait` when a signal is
    # trapped, so a SIGTERM from `fly deploy` is honoured within TICK_SECONDS
    # instead of after a full check interval.
    sleep "$TICK_SECONDS" &
    wait $! 2>/dev/null || true
    SINCE_CHECK=$(( SINCE_CHECK + TICK_SECONDS ))
    if [ "$SINCE_CHECK" -ge "$CHECK_INTERVAL_SECONDS" ]; then
        SINCE_CHECK=0
        refresh_if_due
    fi
done

stop_dashboard
