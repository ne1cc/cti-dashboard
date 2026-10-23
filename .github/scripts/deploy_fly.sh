#!/usr/bin/env bash
set -euo pipefail

APP_NAME="${FLY_APP_NAME:-cti-dashboard}"
HEALTH_URL="${FLY_HEALTH_URL:-https://${APP_NAME}.fly.dev/_stcore/health}"
SMOKE_MAX_ATTEMPTS="${SMOKE_MAX_ATTEMPTS:-12}"
SMOKE_SLEEP_SECONDS="${SMOKE_SLEEP_SECONDS:-10}"
SMOKE_TIMEOUT_SECONDS="${SMOKE_TIMEOUT_SECONDS:-5}"

echo "==> Fetching current release information for app: $APP_NAME"
PREV_IMAGE=""
if RELEASES_JSON=$(flyctl releases --image --json -a "$APP_NAME" 2>/dev/null); then
    PREV_IMAGE=$(echo "$RELEASES_JSON" | python3 -c '
import json, sys
try:
    data = json.load(sys.stdin)
    if isinstance(data, list) and len(data) > 0:
        candidates = []
        for r in data:
            if isinstance(r, dict):
                img = r.get("ImageRef") or r.get("image_ref") or r.get("Image") or r.get("image")
                status = str(r.get("Status") or r.get("status") or "").lower()
                if img and status not in ("failed", "failure", "error"):
                    candidates.append(r)
        if not candidates:
            for r in data:
                if isinstance(r, dict):
                    img = r.get("ImageRef") or r.get("image_ref") or r.get("Image") or r.get("image")
                    if img:
                        candidates.append(r)
        if candidates:
            def get_version(item):
                v = item.get("Version") or item.get("version") or 0
                try:
                    return int(v)
                except (ValueError, TypeError):
                    return 0
            candidates.sort(key=get_version, reverse=True)
            best_img = candidates[0].get("ImageRef") or candidates[0].get("image_ref") or candidates[0].get("Image") or candidates[0].get("image")
            if best_img:
                print(str(best_img).strip())
except Exception:
    pass
' || true)
fi

if [[ -n "$PREV_IMAGE" ]]; then
    echo "==> Recorded previous release image: $PREV_IMAGE"
else
    echo "==> Warning: No previous release image found for app: $APP_NAME"
fi

perform_rollback_and_exit() {
    local reason="$1"
    echo "==> Failure detected: $reason"
    if [[ -z "$PREV_IMAGE" ]]; then
        echo "==> Warning: No previous release image recorded. Cannot perform automated rollback."
        exit 1
    fi

    echo "==> Initiating automated rollback to previous release image: $PREV_IMAGE"
    if flyctl deploy --image "$PREV_IMAGE" --app "$APP_NAME"; then
        echo "==> Rollback deploy command completed. Polling health once..."
        ROLLBACK_STATUS=$(curl -s -o /dev/null -w "%{http_code}" --max-time "$SMOKE_TIMEOUT_SECONDS" "$HEALTH_URL" || true)
        if [[ "$ROLLBACK_STATUS" == "200" ]]; then
            echo "==> Rollback health check succeeded (HTTP 200)."
        else
            echo "==> Rollback health check returned HTTP status: '$ROLLBACK_STATUS'!"
        fi
    else
        echo "==> Rollback deployment command failed!"
    fi

    echo "==> Exiting with non-zero status to signal deployment failure in CI."
    exit 1
}

echo "==> Deploying $APP_NAME with flyctl deploy --remote-only..."
if ! flyctl deploy --remote-only --app "$APP_NAME"; then
    perform_rollback_and_exit "Initial deployment command failed"
fi

echo "==> Running post-deployment smoke check against: $HEALTH_URL"
attempt=1
smoke_passed=0
while [[ $attempt -le $SMOKE_MAX_ATTEMPTS ]]; do
    echo "==> Smoke check attempt $attempt of $SMOKE_MAX_ATTEMPTS..."
    HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" --max-time "$SMOKE_TIMEOUT_SECONDS" "$HEALTH_URL" || true)
    if [[ "$HTTP_STATUS" == "200" ]]; then
        echo "==> Smoke check passed with HTTP 200!"
        smoke_passed=1
        break
    fi
    echo "==> Health endpoint returned HTTP status: '$HTTP_STATUS' (expected 200)"
    if [[ $attempt -lt $SMOKE_MAX_ATTEMPTS ]]; then
        sleep "$SMOKE_SLEEP_SECONDS"
    fi
    attempt=$((attempt + 1))
done

if [[ $smoke_passed -ne 1 ]]; then
    perform_rollback_and_exit "Smoke check failed or timed out after $SMOKE_MAX_ATTEMPTS attempts"
fi

echo "==> Deployment and smoke check succeeded for $APP_NAME."
exit 0
