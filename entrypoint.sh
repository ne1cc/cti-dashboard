#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${CTI_PROJECT_ROOT:-/app}"
PYTHON_BIN="$APP_DIR/.venv/bin/python"
WAREHOUSE=$("$PYTHON_BIN" -c 'from src.profiles import load_shared_paths; print(load_shared_paths().duckdb)')
DATA_DIR="$(dirname "$(dirname "$WAREHOUSE")")"
MARKER="$DATA_DIR/.last_pipeline_run"
LOG_DIR="$DATA_DIR/logs"
REFRESH_INTERVAL_SECONDS="${CTI_REFRESH_INTERVAL_SECONDS:-604800}"   # 7 days
CHECK_INTERVAL_SECONDS="${CTI_REFRESH_CHECK_SECONDS:-3600}"          # 1 hour
TICK_SECONDS=5
STREAMLIT_BIN="$APP_DIR/.venv/bin/streamlit"

DASHBOARD_PID=""
SHUTDOWN=0
SINCE_CHECK=0

# Stdout is authoritative; a full or broken optional volume log must not hide it.
log_output() {
    local line
    while IFS= read -r line || [ -n "$line" ]; do
        printf '%s\n' "$line"
        { printf '%s\n' "$line" >> "$LOG_DIR/pipeline.log"; } 2>/dev/null || true
    done
}
log() { log_output <<< "[entrypoint] $(date -u +%FT%TZ) $*"; }
run_logged() {
    "$@" 2>&1 | log_output
    return "${PIPESTATUS[0]}"
}

# DuckDB does not create parent directories for a new database file (verified:
# duckdb.connect() raises IOException on a missing directory), and a Fly Volume
# mounts as EMPTY on its very first boot, shadowing whatever the Dockerfile
# baked into this path. Recreate the full tree on every boot from the profile
# registry — a hardcoded list here is how a newly added profile fails on its
# first write in production.
BOOT_PREFLIGHT_OK=0
if "$PYTHON_BIN" -m src.quality.freshness --preflight; then
    BOOT_PREFLIGHT_OK=1
    mkdir -p "$LOG_DIR" || true
    if ! run_logged "$PYTHON_BIN" -m src.cli init-data-dirs; then
        log "init-data-dirs FAILED; aborting boot rather than half-creating the tree"
        exit 1
    fi
else
    log "startup disk preflight FAILED; skipping initialization writes"
fi

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
    if run_logged "$PYTHON_BIN" -m src.quality.freshness --record-attempt \
        && run_logged make pipeline \
        && run_logged "$PYTHON_BIN" -m src.quality.freshness --record-success; then
        if date +%s > "$MARKER"; then
            log "pipeline succeeded"
            return 0
        fi
    fi
    run_logged "$PYTHON_BIN" -m src.quality.freshness --record-failure \
        || log "failed to persist unsuccessful attempt; inspect stdout"
    log "pipeline FAILED (marker not updated, will retry)"
    return 1
}

refresh_if_due() {
    local now last elapsed
    now=$(date +%s)
    last=0
    [ -f "$MARKER" ] && last=$(cat "$MARKER")
    case "$last" in *[!0-9]*|"") last=0 ;; esac
    elapsed=$(( now - last ))
    log "refresh check: now=$now last=$last elapsed=${elapsed}s interval=${REFRESH_INTERVAL_SECONDS}s"
    if [ "$elapsed" -lt "$REFRESH_INTERVAL_SECONDS" ]; then
        return 0
    fi
    if ! run_logged "$PYTHON_BIN" -m src.quality.freshness --preflight; then
        log "refresh skipped: disk preflight FAILED; keeping the existing dashboard serving"
        run_logged "$PYTHON_BIN" -m src.quality.freshness --record-failure \
            || log "failed to persist disk rejection; inspect stdout"
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
    if [ "$BOOT_PREFLIGHT_OK" -eq 1 ]; then
        log "no warehouse found, running first-boot pipeline"
        run_pipeline || log "first-boot pipeline failed; dashboard will show 'warehouse not found' until the next successful refresh"
    else
        log "no warehouse found; cannot bootstrap under the disk floor; no previous warehouse exists"
    fi
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
