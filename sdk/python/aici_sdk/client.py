"""HTTP client that wraps the self-serve API endpoints."""

from __future__ import annotations

import logging
import time
from typing import Any, Mapping, MutableMapping

import httpx

from .errors import AiciApiError, AiciAuthenticationError, AiciError, AiciRateLimitError
from .models import (
    IndexComposition,
    PerformanceSnapshot,
    RunPerformance,
    WeightsSnapshot,
    parse_performance_snapshots,
)

LOGGER = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://aici.pro/api/v1"
DEFAULT_USER_AGENT = "aici-sdk/0.1.0"


class AiciClient:
    """Thin wrapper above the REST API with retry/backoff support."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 30.0,
        retries: int = 2,
        backoff_factor: float = 0.5,
        default_headers: Mapping[str, str] | None = None,
        transport: httpx.BaseTransport | None = None,
        user_agent: str = DEFAULT_USER_AGENT,
    ) -> None:
        if not api_key:
            raise ValueError("api_key must be provided")
        normalized_base = base_url.rstrip("/")
        if not normalized_base:
            raise ValueError("base_url must be a valid HTTP(S) endpoint")
        self._api_key = api_key
        self._base_url = normalized_base
        self._timeout = timeout
        self._retries = max(0, int(retries))
        self._backoff_factor = max(0.0, float(backoff_factor))
        headers: MutableMapping[str, str] = {
            "Accept": "application/json",
            "X-API-Key": api_key,
            "User-Agent": user_agent,
        }
        if default_headers:
            headers.update(default_headers)
        self._client = httpx.Client(
            base_url=self._base_url,
            timeout=self._timeout,
            headers=headers,
            transport=transport,
        )

    def close(self) -> None:
        """Close the underlying HTTP session."""
        self._client.close()

    def __enter__(self) -> "AiciClient":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def _should_retry(self, status_code: int) -> bool:
        if status_code in (408, 425, 429, 500, 502, 503, 504):
            return True
        return False

    def _sleep(self, attempt: int) -> None:
        if self._backoff_factor <= 0:
            return
        delay = self._backoff_factor * (2**attempt)
        time.sleep(delay)

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        normalized_path = path if path.startswith("/") else f"/{path}"
        last_error: Exception | None = None

        for attempt in range(self._retries + 1):
            try:
                response = self._client.request(
                    method=method.upper(),
                    url=normalized_path,
                    params=params,
                    json=json,
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = exc
                LOGGER.warning("HTTP error (%s): %s", type(exc).__name__, exc)
            else:
                if response.status_code >= 400:
                    # Authentication errors
                    if response.status_code in (401, 403):
                        raise AiciAuthenticationError(response.status_code, response.text or response.reason_phrase)
                    # Rate limit
                    if response.status_code == 429:
                        raise AiciRateLimitError(response.status_code, response.text or "rate_limit_exceeded")
                    # Retryable server errors
                    if self._should_retry(response.status_code) and attempt < self._retries:
                        LOGGER.debug("Retryable status %s, attempt %s", response.status_code, attempt)
                        self._sleep(attempt)
                        continue
                    detail = response.text or response.reason_phrase or "api_error"
                    try:
                        payload = response.json()
                    except ValueError:
                        payload = None
                    raise AiciApiError(response.status_code, detail, payload=payload)

                try:
                    return response.json()
                except ValueError as exc:  # pragma: no cover - unexpected payload
                    raise AiciApiError(response.status_code, "Response is not JSON") from exc

            if attempt < self._retries:
                self._sleep(attempt)

        raise AiciError(f"Request to {normalized_path} failed") from last_error

    # Public helpers -----------------------------------------------------------------

    def get_latest_weights(self) -> WeightsSnapshot:
        """Fetch the freshest allocation snapshot available to this workspace."""
        payload = self._request("GET", "/weights/latest")
        return WeightsSnapshot.from_payload(payload)

    def get_run_weights(self, run_id: str) -> WeightsSnapshot:
        """Fetch weights for a particular backtest or rebalance run."""
        payload = self._request("GET", f"/runs/{run_id}/weights")
        return WeightsSnapshot.from_payload(payload)

    def get_run_performance(self, run_id: str) -> RunPerformance:
        """Fetch performance metrics (CAGR, volatility, drawdown, Sharpe...) for a run."""
        payload = self._request("GET", f"/runs/{run_id}/perf")
        return RunPerformance.from_payload(payload)

    def get_index_composition(self) -> IndexComposition:
        """Return the rendered index table plus summary stats."""
        payload = self._request("GET", "/index-composition")
        return IndexComposition.from_payload(payload)

    def list_performance_snapshots(self) -> tuple[str, dict[str, PerformanceSnapshot]]:
        """Return available strategies plus their charts/metric cards."""
        payload = self._request("GET", "/performance")
        return parse_performance_snapshots(payload)
