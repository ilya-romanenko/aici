import json
import os
import time

import requests
from requests import Response

API_KEY = os.getenv("AICI_API_KEY", "")
BASE_URL = "http://127.0.0.1:8500/api/v1"
POLL_INTERVAL_SECONDS = 8
MAX_POLLS = 60
TERMINAL_STATES = {"done", "error", "cancelled"}
PARAM_VALIDATION_HINTS = {
    "n_top_coins": "int [30..300]; total_assets <= n_top_coins",
    "start_date": "YYYY-MM-DD; >= 2021-01-01; not in future",
    "lookback_days": "int [90..720]; >= window_size",
    "window_size": "int [14..120]; <= lookback_days",
    "forecast_horizon": "int [7..60]; <= window_size and <= lookback_days",
    "advanced_forecast": "bool (true/false)",
    "total_assets": "int [5..30]; <= n_top_coins",
    "clustering_metric": "string length 1..64 (usually 'sharpe')",
    "weight_cap": "float [0.08..0.30]; >= max(0.08, 1 / total_assets)",
    "risk_min_weight": "float [0.005..0.08]; <= min(0.08, 1 / total_assets)",
    "risk_max_weight": "float [0.12..0.45]; >= risk_min_weight",
    "vol_floor_ratio": "float [0.25..0.70]",
    "gating_tolerance": "float [0.00..0.10]",
    "run_id": "string 3..64, pattern ^[A-Za-z0-9_.-]+$",
}

params = {
    "n_top_coins": 100,  # int [30..300]; total_assets <= n_top_coins
    "lookback_days": 180,  # int [90..720]; >= window_size
    "window_size": 30,  # int [14..120]; <= lookback_days
    "forecast_horizon": 31,  # int [7..60]; <= window_size and <= lookback_days
    "advanced_forecast": False,  # bool (true/false)
    "total_assets": 8,  # int [5..30]; <= n_top_coins
    "clustering_metric": "sharpe",  # string 1..64 (example: sharpe)
    "weight_cap": 0.25,  # float [0.08..0.30]; >= max(0.08, 1 / total_assets)
    "risk_min_weight": 0.01,  # float [0.005..0.08]; <= min(0.08, 1 / total_assets)
    "risk_max_weight": 0.35,  # float [0.12..0.45]; >= risk_min_weight
    "vol_floor_ratio": 0.3,  # float [0.25..0.70]
    "gating_tolerance": 0.03,  # float [0.00..0.10]
}


def _headers():
    return {"X-API-Key": API_KEY, "Accept": "application/json"}


def _format_loc(raw_loc: object) -> str:
    if isinstance(raw_loc, (list, tuple)):
        return ".".join(str(part) for part in raw_loc)
    if raw_loc is None:
        return "<unknown>"
    return str(raw_loc)


def _extract_error_lines(detail: object) -> list[str]:
    if isinstance(detail, list):
        lines: list[str] = []
        for item in detail:
            if not isinstance(item, dict):
                lines.append(str(item))
                continue
            loc = _format_loc(item.get("loc"))
            msg = str(item.get("msg") or item.get("message") or "Validation error")
            err_type = item.get("type")
            if err_type:
                lines.append(f"{loc}: {msg} ({err_type})")
            else:
                lines.append(f"{loc}: {msg}")
        return lines
    if isinstance(detail, dict):
        if "loc" in detail and ("msg" in detail or "message" in detail):
            loc = _format_loc(detail.get("loc"))
            msg = str(detail.get("msg") or detail.get("message"))
            err_type = detail.get("type")
            if err_type:
                return [f"{loc}: {msg} ({err_type})"]
            return [f"{loc}: {msg}"]
        return [json.dumps(detail, ensure_ascii=False)]
    return [str(detail)]


def _guess_related_params(error_lines: list[str]) -> list[str]:
    lowered = " ".join(error_lines).lower()
    return [name for name in PARAM_VALIDATION_HINTS if name in lowered]


def _print_http_error(response: Response, *, stage: str) -> None:
    status_text = f"{response.status_code} {response.reason}".strip()
    request_url = response.request.url if response.request is not None else response.url
    print(f"[ERROR] {stage}: HTTP {status_text}")
    print(f"   url: {request_url}")
    try:
        payload = response.json()
    except ValueError:
        raw_text = (response.text or "").strip()
        print(f"   non-JSON body: {raw_text or '<empty>'}")
        return

    detail = payload.get("detail", payload) if isinstance(payload, dict) else payload
    error_lines = _extract_error_lines(detail)
    print("   server detail:")
    for line in error_lines:
        print(f"   - {line}")

    related = _guess_related_params(error_lines)
    if not related:
        return
    print("   related params:")
    for name in related:
        print(f"   - {name}: {PARAM_VALIDATION_HINTS[name]}")


def _request_json(method: str, url: str, *, stage: str, timeout: int, params_: dict | None = None) -> dict:
    try:
        response = requests.request(
            method=method,
            url=url,
            params=params_,
            headers=_headers(),
            timeout=timeout,
        )
    except requests.RequestException as exc:
        print(f"[ERROR] {stage}: request failed ({exc.__class__.__name__}).")
        print(f"   details: {exc}")
        raise SystemExit(1) from exc
    if not response.ok:
        _print_http_error(response, stage=stage)
        raise SystemExit(1)
    try:
        return response.json()
    except ValueError:
        print(f"[ERROR] {stage}: server returned non-JSON payload.")
        print(f"   url: {response.url}")
        print(f"   body: {(response.text or '').strip() or '<empty>'}")
        raise SystemExit(1)


if API_KEY == "sk_live_xxxx":
    raise RuntimeError("Set AICI_API_KEY env var before running this snippet.")

print("1/4 Triggering async run...")
payload = _request_json(
    "POST",
    f"{BASE_URL}/run/async",
    stage="POST /run/async",
    timeout=15,
    params_=params,
)
run_id = payload["run_id"]
print("   run_id:", run_id)

print("2/4 Waiting for completion...")
last_line = None
final_state = "pending"
last_progress: dict = {}
for attempt in range(1, MAX_POLLS + 1):
    progress = _request_json(
        "GET",
        f"{BASE_URL}/runs/{run_id}/progress",
        stage=f"GET /runs/{run_id}/progress",
        timeout=15,
    )
    last_progress = progress
    status_line = str(progress.get("status_line") or progress.get("state", "unknown"))
    if status_line != last_line:
        print(f"   [{attempt:02d}/{MAX_POLLS}] {status_line}")
        last_line = status_line

    final_state = str(progress.get("state", "unknown"))
    if final_state in TERMINAL_STATES:
        break
    time.sleep(POLL_INTERVAL_SECONDS)
else:
    raise TimeoutError(f"Run did not finish after {MAX_POLLS * POLL_INTERVAL_SECONDS} seconds.")

if final_state != "done":
    print(f"3/4 Final state: {final_state}.")
    last_message = str(last_progress.get("last_message") or "").strip()
    if last_message:
        print(f"   server message: {last_message}")
    print("4/4 Snapshot is unavailable for this state.")
    raise SystemExit(1)

print("3/4 Fetching run snapshot...")
result = _request_json(
    "GET",
    f"{BASE_URL}/runs/{run_id}/result",
    stage=f"GET /runs/{run_id}/result",
    timeout=30,
)

output = {
    "run_id": run_id,
    "weights": result.get("weights") or {},
    "perf": result.get("perf") or {},
}

print("4/4 Final snapshot:")
print(json.dumps(output, ensure_ascii=False, indent=2))
