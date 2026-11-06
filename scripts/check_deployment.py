"""Outside-in check of the live demo: Streamlit health plus the published freshness marker.

Prints one line per problem and exits 1 if there are any, 0 if healthy, 2 if the script itself
crashed (so a crash is never mistaken for a list of problems). Stdlib only so the
scheduled workflow needs no install step. Usage:

    python scripts/check_deployment.py https://host [--marker-file PATH] [--max-age-days 10]

--marker-file reads the marker from disk instead of <url>/app/static/freshness.json (local tests).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

MARKER_URL_PATH = "/app/static/freshness.json"
HEALTH_URL_PATH = "/_stcore/health"


def _get(url: str, attempts: int = 3) -> tuple[int, str]:
    """GET with retries so one dropped request does not open an incident."""
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(url, timeout=20) as response:  # noqa: S310 (https only)
                return response.status, response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            return exc.code, ""
        except OSError as exc:
            last = exc
            if attempt + 1 < attempts:
                time.sleep(5)
    raise OSError(str(last))


def freshness_problems(raw: str, now: datetime, max_age_days: float) -> list[str]:
    """Stale or unreadable marker text -> problems. Mirrors src.quality.freshness.status()."""
    try:
        marker: dict[str, Any] = json.loads(raw)
        profiles = marker["last_success"]["profiles"]
        if marker["attempt_status"] != "success" or not profiles:
            return [f"Published marker is not a verified success ({marker['attempt_status']})"]
        problems = []
        for pid, capture in sorted(profiles.items()):
            captured = datetime.fromisoformat(capture["captured_at_utc"])
            age = (now - captured).total_seconds() / 86400
            if age < 0:  # a naive timestamp raises TypeError above and reads as unreadable
                problems.append(f"{pid}: capture time is in the future")
            elif age > max_age_days:
                problems.append(f"{pid}: capture is stale ({age:.2f} days > {max_age_days:g})")
        return problems
    except json.JSONDecodeError as exc:  # a host without static serving answers with the SPA HTML
        return [f"Published marker is missing or not served: body is not JSON ({exc})"]
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        return [f"Published marker is unreadable: {exc!r}"]


def check(base_url: str, marker_file: Path | None, max_age_days: float) -> list[str]:
    base = base_url.rstrip("/")
    problems: list[str] = []
    try:
        status, _ = _get(base + HEALTH_URL_PATH)
        if status != 200:
            problems.append(f"{HEALTH_URL_PATH} returned HTTP {status}")
    except OSError as exc:
        problems.append(f"{HEALTH_URL_PATH} unreachable: {exc}")
    if marker_file is not None:
        raw = marker_file.read_text(encoding="utf-8")
    else:
        try:
            status, raw = _get(base + MARKER_URL_PATH)
        except OSError as exc:
            return [*problems, f"{MARKER_URL_PATH} unreachable: {exc}"]
        if status != 200:
            return [*problems, f"{MARKER_URL_PATH} returned HTTP {status}"]
    return [*problems, *freshness_problems(raw, datetime.now(UTC), max_age_days)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base_url")
    parser.add_argument("--marker-file", type=Path)
    parser.add_argument("--max-age-days", type=float, default=10)
    args = parser.parse_args(argv)
    if not args.max_age_days > 0:
        parser.error("--max-age-days must be positive")
    try:
        problems = check(args.base_url, args.marker_file, args.max_age_days)
    except Exception:
        traceback.print_exc()
        return 2
    print("\n".join(problems) if problems else "OK: healthy and fresh")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
