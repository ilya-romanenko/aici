"""Быстрый скрипт для проверки запуска расчёта и получения /weights/latest."""

import time
from typing import Iterable

import requests
from requests import Response

API_KEY = "aici_live_Mj_yHkAWF02lmpC33o47pyKZHWHOjIY3IINwXDlIXEc"
BASE_URL = "http://127.0.0.1:8000/api/v1"

PARAMS = {
    "n_top_coins": 120,
    "lookback_days": 180,
    "window_size": 30,
    "forecast_horizon": 30,
    "weight_cap": 0.15,
    "risk_min_weight": 0.03,
    "risk_max_weight": 0.25,
    "clustering_metric": "sharpe",
}

CONNECT_TIMEOUT = 5
READ_TIMEOUT = 120  # предыдущий таймаут 15с не успевал дождаться ответа
POLL_DELAY = 5
POLL_MAX_SECONDS = 900
RETRIES = 3
BACKOFF = 5


def fetch_latest_weights(
    base_url: str = BASE_URL,
    api_key: str = API_KEY,
    params: dict | None = None,
) -> Iterable[dict]:
    params = params or PARAMS
    headers = {
        "X-API-Key": api_key,
        "Accept": "application/json",
        "Connection": "close",  # не держим keep-alive, иначе uvicorn рвёт соединение через 5с
    }
    with requests.Session() as session:
        response = session.post(
            f"{base_url}/run/async",
            params=params,
            headers=headers,
            timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
        )
        response.raise_for_status()
        run_id = response.json()["run_id"]
        print("Run id:", run_id)

        started_at = time.monotonic()
        while True:
            prog_resp = _with_retries(
                session,
                "get",
                f"{base_url}/runs/{run_id}/progress",
                headers=headers,
            )
            prog = prog_resp.json()
            elapsed = int(time.monotonic() - started_at)
            _print_progress(prog, elapsed)
            state = prog.get("state")
            if state in {"done", "error", "cancelled"}:
                break
            if elapsed > POLL_MAX_SECONDS:
                raise TimeoutError(f"Не дождались завершения выполнения run_id={run_id} за {POLL_MAX_SECONDS} сек.")
            time.sleep(POLL_DELAY)

        if state != "done":
            raise RuntimeError(f"Выполнение остановлено: state={state}")

        weights_resp = _with_retries(
            session,
            "get",
            f"{base_url}/runs/{run_id}/weights",
            headers=headers,
        )
        weights_payload = weights_resp.json()
        items = weights_payload.get("items", [])
        if not items:
            print("Пустой список весов.")
        return items


def _with_retries(
    session: requests.Session,
    method: str,
    url: str,
    *,
    headers: dict,
    params: dict | None = None,
) -> Response:
    for attempt in range(1, RETRIES + 1):
        try:
            resp = session.request(
                method,
                url,
                headers=headers,
                params=params,
                timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
            )
            resp.raise_for_status()
            return resp
        except (requests.ConnectionError, requests.Timeout) as exc:
            if attempt == RETRIES:
                raise
            wait = BACKOFF * attempt
            print(f"{exc.__class__.__name__}: retry {attempt}/{RETRIES} in {wait}s")
            time.sleep(wait)


def _print_progress(progress: dict, elapsed: int) -> None:
    state = progress.get("state")
    stages = progress.get("stages") or []
    active = next((stage for stage in stages if stage.get("status") == "running"), None)
    if active is None:
        active = next((stage for stage in stages if stage.get("status") == "pending"), None)
    last_log = (progress.get("logs") or [])[-1] if progress.get("logs") else None
    message = (active or {}).get("message") or (last_log or {}).get("message") or ""
    stage_label = (active or {}).get("label") or "-"
    print(f"{elapsed:4d}s | state={state:<9} | stage={stage_label} | {message}")


def main() -> None:
    items = fetch_latest_weights()
    for asset in items:
        print(f"{asset.get('asset')}: {asset.get('weight')}")
    if items:
        print(f"Всего записей: {len(items)}")


if __name__ == "__main__":
    main()
