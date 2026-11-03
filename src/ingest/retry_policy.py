"""Retry policy for ClinicalTrials.gov API calls: exponential backoff on
retryable HTTP statuses, timeouts, and transport errors."""

from typing import Any

import requests
from loguru import logger
from tenacity import Retrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from src.config import HttpConfig
from src.utils.privacy import patient_request


class RetryableHTTPStatusError(Exception):
    def __init__(self, status_code: int, url: str):
        self.status_code = status_code
        self.url = url
        super().__init__(f"Retryable HTTP status {status_code} from {url}")


RETRYABLE_EXCEPTIONS = (
    RetryableHTTPStatusError,
    requests.Timeout,
    requests.ConnectionError,
)


def build_retryer(http_config: HttpConfig) -> Retrying:
    def _before_sleep(retry_state: Any) -> None:
        logger.warning(
            "Retryable error on attempt {}/{}: {}",
            retry_state.attempt_number,
            http_config.max_retries,
            type(retry_state.outcome.exception()).__name__
            if patient_request.get()
            else retry_state.outcome.exception(),
        )

    return Retrying(
        retry=retry_if_exception_type(RETRYABLE_EXCEPTIONS),
        wait=wait_exponential(
            multiplier=http_config.backoff_initial_seconds,
            max=http_config.backoff_max_seconds,
        ),
        stop=stop_after_attempt(http_config.max_retries),
        before_sleep=_before_sleep,
        reraise=True,
    )
