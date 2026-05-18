# AI Crypto Index Python SDK

The `aici-sdk` package exposes a typed, retry-aware HTTP client for the AI Crypto Index self-serve API. It wraps the most common allocation and performance endpoints with small data classes so you can focus on product logic instead of request plumbing.

## Installation

```bash
pip install -e ./sdk/python
```

Or install directly from the repo without editable mode:

```toml
# pyproject.toml
[project]
dependencies = [
    "aici-sdk @ file:///ABSOLUTE/PATH/TO/repo/sdk/python"
    # or git+ssh://<your-clone-url>#subdirectory=sdk/python
]
```

## Quick start

```python
import os
from aici_sdk import AiciClient

client = AiciClient(
    api_key=os.environ["AICI_API_KEY"],
    base_url=os.getenv("AICI_BASE_URL", "https://aici.pro/api/v1"),
)
weights = client.get_latest_weights()
print(weights.run_id, weights.top_assets(5))

perf = client.get_run_performance(weights.run_id)
print(perf.metrics["sharpe"])

historical = client.get_run_weights(run_id=weights.run_id)
print(len(historical.items))
```

See `examples/sdk_python_quickstart/main.py` for a runnable walkthrough that streams weights into a pandas DataFrame.

## Features

- Typed wrappers for `/weights/latest`, `/runs/{run_id}/weights`, `/runs/{run_id}/perf`, `/index-composition`, and `/performance`.
- Automatic retries with exponential backoff for `429` and transient `5xx` responses.
- Customizable timeout, telemetry `User-Agent`, and pluggable `httpx` transport for advanced usage.
- Convenience helpers to transform weights into dictionaries or pandas objects.

## Authentication and environment

- Set `AICI_API_KEY` in your environment and let the client read it; keys are scoped per workspace and enforce IP allow lists (403 outside the list).
- Default base URL is `https://aici.pro/api/v1`; override with `AICI_BASE_URL` or the `base_url` argument when hitting staging.
- Sandbox keys use delayed data and smaller quotas; request an upgrade in `/app` for production limits.

## Configuration

| Argument | Default | Description |
| --- | --- | --- |
| `api_key` | _required_ | Secret value issued in the dashboard (sent via `X-API-Key`). |
| `base_url` | `https://aici.pro/api/v1` | Override for staging or self-hosted deployments. |
| `timeout` | `30.0` seconds | Per-request timeout passed to `httpx`. |
| `retries` | `2` | Number of retry attempts for retryable status codes. |
| `backoff_factor` | `0.5` | Sleep multiplier between retries (`backoff_factor * 2**attempt`). |

## Rate limits and retries

- Default throttle is ~120 requests per 60 seconds for sandbox keys. Responses include `X-RateLimit-*` and `Retry-After`.
- The client retries `429`/`5xx` with exponential backoff. For long-running batch jobs, cap concurrency so you do not exhaust burst limits.

## Endpoint coverage

- `get_latest_weights()` → `GET /weights/latest` returning `run_id` and `items` with weights.
- `get_run_weights(run_id)` → `GET /runs/{run_id}/weights` for historical allocations.
- `get_run_performance(run_id)` → `GET /runs/{run_id}/perf` with `cagr`, `annual_volatility`, `max_drawdown`, and `sharpe`.

All client methods raise `AiciApiError` (and subclasses) on failure. Catch `AiciAuthenticationError` for 401/403 responses and `AiciRateLimitError` for `429` throttling signals.
