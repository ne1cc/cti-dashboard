"""HTTP client for GET /api/v2/studies on ClinicalTrials.gov.

All query parameters come from config; list-valued filters (e.g.
filter.overallStatus) are joined with '|' per the v2 API syntax.

Uses `requests` rather than `httpx`: ClinicalTrials.gov's edge bot-protection
fingerprints httpx's TLS/HTTP handshake and rejects it with 403 even for
otherwise-identical requests, while requests (built on urllib3) is unaffected.
"""

import time
from typing import Any

import requests

from src.config import ApiConfig
from src.ingest.retry_policy import RetryableHTTPStatusError, build_retryer

USER_AGENT = "cti-dashboard/0.1 (local-first analytics portfolio)"


class CTGClient:
    def __init__(self, api_config: ApiConfig, client: requests.Session | None = None):
        self.api = api_config
        self._url = api_config.studies_url
        self._http = api_config.http
        self._client = client or requests.Session()
        self._client.headers.update({"Accept": "application/json", "User-Agent": USER_AGENT})
        self._retryer = build_retryer(api_config.http)
        self._last_request_at: float | None = None

    def build_params(self, condition: str | None = None) -> dict[str, str]:
        params: dict[str, str] = {}
        for key, value in self.api.query_params.items():
            params[key] = "|".join(str(v) for v in value) if isinstance(value, list) else str(value)
        if self.api.fields:
            # `fields` is a pipe-delimited list per the OpenAPI spec
            # (style: pipeDelimited); branch names carry all descendants.
            params["fields"] = "|".join(self.api.fields)
        if condition:
            params["query.cond"] = condition
        params["format"] = self.api.format
        params["pageSize"] = str(self.api.page_size)
        if self.api.count_total:
            params["countTotal"] = "true"
        return params

    def fetch_page(
        self, params: dict[str, str], page_token: str | None = None
    ) -> tuple[dict[str, Any], str]:
        """Fetch one page; returns (parsed payload, unmodified response text)."""
        query = dict(params)
        if page_token:
            query["pageToken"] = page_token

        return self._fetch(self._url, query)

    def _fetch(self, url: str, params: dict[str, str] | None = None) -> tuple[dict[str, Any], str]:
        def _request() -> requests.Response:
            self._throttle()
            response = self._client.get(url, params=params, timeout=self._http.timeout_seconds)
            if response.status_code in self._http.retry_on_status:
                raise RetryableHTTPStatusError(response.status_code, url)
            response.raise_for_status()
            return response

        response = self._retryer(_request)
        return response.json(), response.text

    def _throttle(self) -> None:
        """Space requests at least min_request_interval_seconds apart.

        Applies to every attempt including retries, because a retry is a
        request the server sees too."""
        interval = self.api.min_request_interval_seconds
        if interval <= 0:
            return
        now = time.monotonic()
        if self._last_request_at is not None:
            wait = interval - (now - self._last_request_at)
            if wait > 0:
                time.sleep(wait)
        self._last_request_at = time.monotonic()

    def fetch_studies(self, params: dict[str, Any]) -> tuple[dict[str, Any], str]:
        """Fetch studies with arbitrary parameters; returns (parsed payload, raw text)."""
        query: dict[str, str] = {}
        for k, v in params.items():
            if v is not None:
                query[k] = "|".join(str(item) for item in v) if isinstance(v, list) else str(v)

        return self.fetch_page(query)

    def fetch_study(self, nct_id: str) -> tuple[dict[str, Any], str]:
        """Fetch a study; returns parsed payload and unmodified response text."""
        return self._fetch(f"{self._url.rstrip('/')}/{nct_id}")

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "CTGClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
