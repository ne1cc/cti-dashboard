"""Per-client sliding-window limiter for routes that call the upstream registry."""

import os
import time
from collections import deque

MAX_TRACKED_CLIENTS = 10_000  # shortcut: one process only; use a shared store if we scale out


class SlidingWindowLimiter:
    def __init__(self, limit: int | None = None, window_seconds: float = 60.0) -> None:
        self.limit = (
            limit if limit is not None else int(os.environ.get("CTI_LIVE_RATE_LIMIT", "30"))
        )
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = {}

    def retry_after(self, client: str, now: float | None = None) -> int:
        """Record a hit; return 0 if allowed, else whole seconds until a slot frees."""
        if self.limit <= 0:
            return 0
        now = time.monotonic() if now is None else now
        hits = self._hits.setdefault(client, deque())
        while hits and now - hits[0] >= self.window:
            hits.popleft()
        if len(hits) >= self.limit:
            return max(1, int(self.window - (now - hits[0])) + 1)
        hits.append(now)
        if len(self._hits) > MAX_TRACKED_CLIENTS:
            for key in [k for k, v in self._hits.items() if not v or now - v[-1] >= self.window]:
                del self._hits[key]
        return 0
