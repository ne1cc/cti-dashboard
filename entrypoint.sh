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

log() { echo "[entrypoint] $(date -u +%FT%TZ) $*" >> "$LOG_DIR/pipeline.log"; }

mkdir -p "$LOG_DIR"

# DuckDB does not create parent directories for a new database file (verified:
# duckdb.connect() raises IOException on a missing directory), and a Fly Volume
# mounts as EMPTY on its very first boot, shadowing whatever the Dockerfile
# baked into the image at this path. Recreate the full tree on every boot
# (idempotent, cheap) so `make pipeline` never fails here. Bronze is
# profile-scoped to adrd, matching config/project_config.yml.
mkdir -p "$DATA_DIR/bronze/adrd/api_responses" "$DATA_DIR/bronze/adrd/manifests" \
    "$DATA_DIR/silver" "$DATA_DIR/gold" "$DATA_DIR/warehouse"

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
