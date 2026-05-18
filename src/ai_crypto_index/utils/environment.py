from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from ai_crypto_index.fetch_data.data_collection import download_multiple_cryptos
from ai_crypto_index.fetch_data.data_preprocessing.load_and_preprocess import (
    load_and_preprocess_data_fixed,
)
from ai_crypto_index.fetch_data.data_preprocessing.load_top_n_auto import get_top_n_cryptos_cmc

CONFIG_PATH = Path("config/pipeline.json")
DEFAULT_RUNS_ROOT = Path("runs")
DEFAULT_DATA_ROOT = Path("data")


def _load_config(config_path: Path) -> dict[str, Any]:
    with config_path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _ensure_config_file(config_path: Path = CONFIG_PATH) -> dict[str, Any]:
    if not config_path.exists():
        message = f"Config file not found at {config_path}. Run setup to generate it."
        raise FileNotFoundError(message)
    return _load_config(config_path)


def _ensure_data_sources(
    config: dict[str, Any],
    *,
    top_n: int | None = None,
    preload_data: bool = True,
) -> dict[str, Any]:
    data_cfg = config.get("data", {})
    market_cfg = config.get("market_data", {})

    data_root = Path(data_cfg.get("root", DEFAULT_DATA_ROOT))
    data_root.mkdir(parents=True, exist_ok=True)

    merged_path = data_root / "merged_prices.csv"
    existing_csv = list(data_root.glob("*.csv"))
    top_n_cfg = int(market_cfg.get("top_n", 10))
    if top_n is not None:
        try:
            effective_top_n = int(top_n)
        except (TypeError, ValueError) as exc:
            raise ValueError("top_n override must be a positive integer") from exc
        if effective_top_n <= 0:
            raise ValueError("top_n override must be a positive integer")
    else:
        effective_top_n = top_n_cfg

    min_files = max(1, int(effective_top_n * 0.5))
    should_refresh = bool(market_cfg.get("fresh_download", False))

    actions: list[str] = []
    if not preload_data:
        actions.append("preload_skipped")
        return {
            "data_root": str(data_root),
            "merged_path": str(merged_path),
            "actions": actions,
            "effective_top_n": effective_top_n,
        }

    if should_refresh or len(existing_csv) < min_files:
        start_date = market_cfg.get("start_date")
        if not start_date:
            one_year_ago = datetime.now().date().replace(day=1)
            start_date = one_year_ago.replace(year=one_year_ago.year - 1).strftime("%Y-%m-%d")
        end_date = datetime.now().strftime("%Y-%m-%d")

        symbols = get_top_n_cryptos_cmc(n=effective_top_n)
        if not symbols:
            raise RuntimeError("Unable to fetch symbol list from CoinMarketCap")

        download_multiple_cryptos(symbols, start_date, end_date, data_folder=str(data_root))
        actions.append("downloaded_market_data")
    else:
        actions.append("market_data_already_present")
    merged = load_and_preprocess_data_fixed(
        data_folder=str(data_root),
        dropna_all=data_cfg.get("dropna_all", True),
        min_history_days=int(data_cfg.get("min_history_days", 365)),
        include_delisted=bool(data_cfg.get("include_delisted", False)),
        allow_internal_gaps=bool(data_cfg.get("allow_internal_gaps", False)),
    )
    if merged.empty:
        raise RuntimeError("Merged market data is empty after preprocessing")

    merged.to_csv(merged_path)
    actions.append("merged_prices_ready")

    return {
        "data_root": str(data_root),
        "merged_path": str(merged_path),
        "actions": actions,
        "effective_top_n": effective_top_n,
    }


def _ensure_runs_structure(config: dict[str, Any]) -> dict[str, Any]:
    runs_cfg = config.get("runs", {})
    runs_root = Path(runs_cfg.get("root", DEFAULT_RUNS_ROOT))
    runs_root.mkdir(parents=True, exist_ok=True)

    template_run = runs_root / "sample_run"
    template_run.mkdir(exist_ok=True)

    expected_files = runs_cfg.get("expected_files", [])
    created = []
    for name in expected_files:
        file_path = template_run / name
        if not file_path.exists():
            if name.endswith(".json"):
                file_path.write_text("{}", encoding="utf-8")
            else:
                file_path.touch()
            created.append(name)

    return {
        "runs_root": str(runs_root),
        "sample_run": str(template_run),
        "created_files": created,
    }


def prepare_environment(
    config_path: Path | None = None,
    *,
    top_n: int | None = None,
    preload_data: bool = True,
) -> dict[str, Any]:
    """Ensure market data, configuration, and run storage scaffolding exist."""
    path = Path(config_path) if config_path else CONFIG_PATH
    config = _ensure_config_file(path)

    data_info = _ensure_data_sources(config, top_n=top_n, preload_data=preload_data)
    runs_info = _ensure_runs_structure(config)

    return {
        "config": str(path),
        "data": data_info,
        "runs": runs_info,
    }


def prepare_default_run_directory(
    run_id: str | None = None,
    config_path: Path | None = None,
) -> Path:
    """Create a run directory populated with expected files."""
    path = Path(config_path) if config_path else CONFIG_PATH
    config = _ensure_config_file(path)
    runs_cfg = config.get("runs", {})
    runs_root = Path(runs_cfg.get("root", DEFAULT_RUNS_ROOT))
    runs_root.mkdir(parents=True, exist_ok=True)

    expected_files = runs_cfg.get("expected_files", [])
    identifier = run_id or datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    run_dir = runs_root / identifier
    run_dir.mkdir(exist_ok=True)

    for name in expected_files:
        file_path = run_dir / name
        if not file_path.exists():
            if name.endswith(".json"):
                file_path.write_text("{}", encoding="utf-8")
            else:
                file_path.touch()

    return run_dir


if __name__ == "__main__":
    summary = prepare_environment()
    print(json.dumps(summary, indent=2))
