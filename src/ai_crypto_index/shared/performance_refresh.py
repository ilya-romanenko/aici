from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable

import pandas as pd

from ai_crypto_index.pipelines.backtesting.simulate_index import simulate_index_over_time
from ai_crypto_index.shared.performance_series_store import (
    resolve_performance_series_read_path,
    resolve_performance_series_write_path,
)
from ai_crypto_index.shared.performance_snapshot import (
    load_performance_bundle,
)
from ai_crypto_index.shared.settings import ServiceSettings

logger = logging.getLogger("ai_crypto_index.performance")

PERFORMANCE_VARIANTS: dict[str, dict[str, str]] = {
    "classic": {"strategy": "balanced", "filename": "AICI_classic.csv"},
    "conservative": {"strategy": "conservative", "filename": "AICI_conservative.csv"},
    "risky": {"strategy": "aggressive", "filename": "AICI_risky.csv"},
}
BENCHMARK_VARIANTS: dict[str, dict[str, str]] = {
    "btc": {"column": "BTC", "filename": "BTC_USD.csv"},
    "eth": {"column": "ETH", "filename": "ETH_USD.csv"},
}

DEFAULT_LOOKBACK_DAYS = 180
DEFAULT_WINDOW_SIZE = 30
DEFAULT_FORECAST_HORIZON = 30
DEFAULT_RELOAD_BUFFER_DAYS = max(5, DEFAULT_WINDOW_SIZE + 5, DEFAULT_FORECAST_HORIZON)
STATE_DIR_NAME = "_performance"
STATE_FILENAME = "auto_config.json"
PRICE_FILENAME = "merged_prices.csv"


@dataclass
class VariantRefreshResult:
    key: str
    filename: str
    added_rows: int
    last_date: date | None
    latest_date: date | None
    error: str | None = None

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        if self.last_date:
            payload["last_date"] = self.last_date.isoformat()
        if self.latest_date:
            payload["latest_date"] = self.latest_date.isoformat()
        return payload


@dataclass
class BenchmarkRefreshResult:
    key: str
    filename: str
    added_rows: int
    last_date: date | None
    latest_date: date | None
    error: str | None = None

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        if self.last_date:
            payload["last_date"] = self.last_date.isoformat()
        if self.latest_date:
            payload["latest_date"] = self.latest_date.isoformat()
        return payload


@dataclass
class PerformanceRefreshReport:
    variants: list[VariantRefreshResult]
    total_added: int
    latest_date: date | None
    benchmarks: list["BenchmarkRefreshResult"]
    benchmark_total_added: int

    def to_dict(self) -> dict[str, object]:
        return {
            "variants": [item.to_dict() for item in self.variants],
            "total_added": self.total_added,
            "latest_date": self.latest_date.isoformat() if self.latest_date else None,
            "benchmarks": [item.to_dict() for item in self.benchmarks],
            "benchmark_total_added": self.benchmark_total_added,
        }


@dataclass
class AutoRunConfig:
    enabled: bool = True
    next_run_date: date | None = None
    last_run_at: datetime | None = None
    last_run_status: str | None = None
    last_error: str | None = None
    last_summary: dict[str, object] | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "next_run_date": self.next_run_date.isoformat() if self.next_run_date else None,
            "last_run_at": self.last_run_at.isoformat() if self.last_run_at else None,
            "last_run_status": self.last_run_status,
            "last_error": self.last_error,
            "last_summary": self.last_summary or {},
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "AutoRunConfig":
        def _parse_date(value: object | None) -> date | None:
            if not value:
                return None
            try:
                return date.fromisoformat(str(value))
            except (TypeError, ValueError):
                return None

        def _parse_datetime(value: object | None) -> datetime | None:
            if not value:
                return None
            try:
                return datetime.fromisoformat(str(value))
            except (TypeError, ValueError):
                return None

        return cls(
            enabled=bool(payload.get("enabled", True)),
            next_run_date=_parse_date(payload.get("next_run_date")),
            last_run_at=_parse_datetime(payload.get("last_run_at")),
            last_run_status=payload.get("last_run_status") or None,
            last_error=payload.get("last_error") or None,
            last_summary=payload.get("last_summary") if isinstance(payload.get("last_summary"), dict) else None,
        )


def _first_day_next_month(base: date) -> date:
    if base.month == 12:
        return date(base.year + 1, 1, 1)
    return date(base.year, base.month + 1, 1)


def _resolve_price_path(settings: ServiceSettings) -> Path:
    config_dir = settings.config_path.resolve().parent
    repo_root = config_dir.parent if config_dir.name.lower() == "config" else config_dir
    candidate = repo_root / "data" / PRICE_FILENAME
    return candidate


def _state_dir(settings: ServiceSettings) -> Path:
    target = settings.runs_root / STATE_DIR_NAME
    target.mkdir(parents=True, exist_ok=True)
    return target


def _state_path(settings: ServiceSettings) -> Path:
    return _state_dir(settings) / STATE_FILENAME


def load_auto_config(settings: ServiceSettings, *, latest_date: date | None = None) -> AutoRunConfig:
    path = _state_path(settings)
    if not path.exists():
        config = AutoRunConfig(enabled=True, next_run_date=_initial_next_run_date(latest_date))
        persist_auto_config(settings, config)
        return config
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("invalid config format")
        config = AutoRunConfig.from_dict(raw)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Falling back to default auto-run config: %s", exc)
        config = AutoRunConfig(enabled=True, next_run_date=_initial_next_run_date(latest_date))
        persist_auto_config(settings, config)
    return config


def persist_auto_config(settings: ServiceSettings, config: AutoRunConfig) -> None:
    path = _state_path(settings)
    path.write_text(json.dumps(config.to_dict(), indent=2), encoding="utf-8")


def _load_equity_curve(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=["date", "log_return"])
    df = pd.read_csv(path)
    if "log_return" not in df.columns:
        return pd.DataFrame(columns=["date", "log_return"])
    date_col = "date" if "date" in df.columns else df.columns[0]
    df = df.rename(columns={date_col: "date"})
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["log_return"] = pd.to_numeric(df["log_return"], errors="coerce")
    df = df.dropna(subset=["date", "log_return"]).sort_values("date")
    return df.reset_index(drop=True)


def _load_benchmark_frame(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=["date", "Close"])
    df = pd.read_csv(path)
    date_col = "date" if "date" in df.columns else df.columns[0]
    value_col = "Close" if "Close" in df.columns else None
    for candidate in ("adj_close", "Adj Close", "close", "Close"):
        if candidate in df.columns:
            value_col = candidate
            break
    if value_col is None:
        remaining = [col for col in df.columns if col != date_col]
        value_col = remaining[0] if remaining else None
    if value_col is None:
        return pd.DataFrame(columns=["date", "Close"])
    df = df.rename(columns={date_col: "date", value_col: "Close"})
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["Close"] = pd.to_numeric(df["Close"], errors="coerce")
    df = df.dropna(subset=["date", "Close"]).sort_values("date")
    return df.reset_index(drop=True)


def _load_price_history(price_path: Path) -> pd.DataFrame:
    if not price_path.exists():
        raise FileNotFoundError(f"price history not found at '{price_path}'")
    df = pd.read_csv(price_path, index_col=0, parse_dates=True)
    if df.empty:
        raise ValueError(f"price history '{price_path}' is empty")
    df = df.sort_index()
    return df


def _initial_next_run_date(latest_date: date | None) -> date:
    today = date.today()
    if latest_date is None:
        return today
    if latest_date < today:
        return today
    return _first_day_next_month(today)


def _next_run_date_from_refresh(latest_date: date | None) -> date:
    today = date.today()
    reference = latest_date or today
    if reference < today:
        reference = today
    return _first_day_next_month(reference)


def _variant_reload_start(
    *,
    last_known_date: date | None,
    source_latest_date: date,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    forecast_horizon: int = DEFAULT_FORECAST_HORIZON,
    reload_buffer_days: int = DEFAULT_RELOAD_BUFFER_DAYS,
) -> pd.Timestamp | None:
    if last_known_date is None:
        return None
    required_span_days = lookback_days + forecast_horizon + reload_buffer_days
    overlap_start = last_known_date - timedelta(days=required_span_days)
    minimum_start_for_latest = source_latest_date - timedelta(days=lookback_days + forecast_horizon)
    # Use the older boundary so monthly rebalance windows have enough history and forward horizon.
    selected_start = min(overlap_start, minimum_start_for_latest)
    return pd.Timestamp(selected_start)


def _append_and_dedupe(existing: pd.DataFrame, new_rows: pd.DataFrame) -> pd.DataFrame:
    combined = pd.concat([existing, new_rows], ignore_index=True)
    combined["date"] = pd.to_datetime(combined["date"], errors="coerce").dt.normalize()
    value_col = "log_return" if "log_return" in combined.columns else "Close"
    combined[value_col] = pd.to_numeric(combined[value_col], errors="coerce")
    combined = combined.dropna(subset=["date", value_col])
    combined = combined.drop_duplicates(subset=["date"], keep="first")
    combined = combined.sort_values("date").reset_index(drop=True)
    return combined


def _persist_results(settings: ServiceSettings, df: pd.DataFrame, filename: str) -> None:
    df_to_write = df.copy()
    df_to_write["date"] = pd.to_datetime(df_to_write["date"])
    target = resolve_performance_series_write_path(
        runs_root=settings.runs_root,
        filename=filename,
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    df_to_write.to_csv(target, index=False, date_format="%Y-%m-%d")


def _seed_persistent_series_if_missing(
    settings: ServiceSettings,
    filename: str,
    existing_df: pd.DataFrame,
) -> bool:
    target = resolve_performance_series_write_path(
        runs_root=settings.runs_root,
        filename=filename,
    )
    if target.exists() or existing_df.empty:
        return False
    _persist_results(settings, existing_df, filename)
    return True


def _run_variant(
    settings: ServiceSettings,
    key: str,
    config: dict[str, str],
    df_prices: pd.DataFrame,
    *,
    scratch_dir: Path,
) -> VariantRefreshResult:
    filename = config["filename"]
    strategy_key = config["strategy"]
    df_prices = df_prices.sort_index()
    if df_prices.empty:
        raise ValueError("price history is empty")
    latest_price_ts = df_prices.index.max()
    latest_price_date = latest_price_ts.date()
    primary_path = resolve_performance_series_read_path(
        filename=filename,
        runs_root=settings.runs_root,
    )
    if primary_path is None:
        primary_path = resolve_performance_series_write_path(
            runs_root=settings.runs_root,
            filename=filename,
        )

    existing = _load_equity_curve(primary_path)
    last_known_date = existing["date"].max().date() if not existing.empty else None

    window_start = _variant_reload_start(
        last_known_date=last_known_date,
        source_latest_date=latest_price_date,
    )
    if window_start is not None:
        df_prices = df_prices.loc[window_start:]

    if df_prices.empty:
        _seed_persistent_series_if_missing(settings, filename, existing)
        return VariantRefreshResult(
            key=key,
            filename=filename,
            added_rows=0,
            last_date=last_known_date,
            latest_date=last_known_date,
            error="no prices left after reload window",
        )

    if latest_price_date <= (last_known_date or date.min):
        message = "no newer prices available"
        _seed_persistent_series_if_missing(settings, filename, existing)
        return VariantRefreshResult(
            key=key,
            filename=filename,
            added_rows=0,
            last_date=last_known_date,
            latest_date=last_known_date,
            error=message,
        )

    try:
        equity_series, _, _, _ = simulate_index_over_time(
            df_prices=df_prices,
            lookback_days=DEFAULT_LOOKBACK_DAYS,
            window_size=DEFAULT_WINDOW_SIZE,
            forecast_horizon=DEFAULT_FORECAST_HORIZON,
            strategy=strategy_key,
            save_dir=str(scratch_dir / key),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Variant simulation skipped for %s: %s", key, exc)
        _seed_persistent_series_if_missing(settings, filename, existing)
        return VariantRefreshResult(
            key=key,
            filename=filename,
            added_rows=0,
            last_date=last_known_date,
            latest_date=last_known_date,
            error=f"simulation skipped: {exc}",
        )

    new_series = equity_series
    if last_known_date and not new_series.empty:
        normalized_index = pd.to_datetime(new_series.index, errors="coerce")
        if normalized_index.isna().all():
            _seed_persistent_series_if_missing(settings, filename, existing)
            return VariantRefreshResult(
                key=key,
                filename=filename,
                added_rows=0,
                last_date=last_known_date,
                latest_date=last_known_date,
                error="simulation returned non-datetime index; existing series preserved",
            )
        new_series = new_series.loc[normalized_index.date > last_known_date]

    if new_series.empty:
        _seed_persistent_series_if_missing(settings, filename, existing)
        return VariantRefreshResult(
            key=key,
            filename=filename,
            added_rows=0,
            last_date=last_known_date,
            latest_date=last_known_date,
            error="no new simulation rows; existing series preserved",
        )

    new_rows = pd.DataFrame(
        {
            "date": pd.to_datetime(new_series.index).date,
            "log_return": new_series.values,
        }
    )
    combined = _append_and_dedupe(existing, new_rows)
    _persist_results(settings, combined, filename)

    latest_ts = combined["date"].max() if not combined.empty else None
    latest_date = latest_ts.date() if isinstance(latest_ts, pd.Timestamp) else latest_ts
    if latest_date is None:
        latest_date = last_known_date
    added_rows = len(new_rows)
    return VariantRefreshResult(
        key=key,
        filename=filename,
        added_rows=added_rows,
        last_date=latest_date,
        latest_date=latest_date,
        error=None,
    )


def _run_benchmark(
    settings: ServiceSettings,
    key: str,
    config: dict[str, str],
    df_prices: pd.DataFrame,
) -> BenchmarkRefreshResult:
    filename = config["filename"]
    column = config["column"]
    primary_path = resolve_performance_series_read_path(
        filename=filename,
        runs_root=settings.runs_root,
    )
    if primary_path is None:
        primary_path = resolve_performance_series_write_path(
            runs_root=settings.runs_root,
            filename=filename,
        )

    existing = _load_benchmark_frame(primary_path)
    last_known_date = existing["date"].max().date() if not existing.empty else None

    if column not in df_prices.columns:
        return BenchmarkRefreshResult(
            key=key,
            filename=filename,
            added_rows=0,
            last_date=last_known_date,
            latest_date=last_known_date,
            error=f"column '{column}' missing in price history",
        )

    df_prices = df_prices.sort_index()
    if last_known_date:
        df_prices = df_prices.loc[df_prices.index.date > last_known_date]

    if df_prices.empty:
        return BenchmarkRefreshResult(
            key=key,
            filename=filename,
            added_rows=0,
            last_date=last_known_date,
            latest_date=last_known_date,
            error="no newer prices available",
        )

    series = pd.to_numeric(df_prices[column], errors="coerce").dropna()
    if series.empty:
        return BenchmarkRefreshResult(
            key=key,
            filename=filename,
            added_rows=0,
            last_date=last_known_date,
            latest_date=last_known_date,
            error="no valid prices",
        )

    new_rows = pd.DataFrame({"date": series.index, "Close": series.values})
    combined = _append_and_dedupe(existing, new_rows)
    _persist_results(settings, combined, filename)

    latest_ts = combined["date"].max() if not combined.empty else None
    latest_date = latest_ts.date() if isinstance(latest_ts, pd.Timestamp) else latest_ts
    if latest_date is None:
        latest_date = last_known_date

    return BenchmarkRefreshResult(
        key=key,
        filename=filename,
        added_rows=len(new_rows),
        last_date=latest_date,
        latest_date=latest_date,
        error=None,
    )


def refresh_performance_data(settings: ServiceSettings, *, price_path: Path | None = None) -> PerformanceRefreshReport:
    path = price_path or _resolve_price_path(settings)
    df_prices = _load_price_history(path)
    scratch_dir = _state_dir(settings) / "runs"
    scratch_dir.mkdir(parents=True, exist_ok=True)

    results: list[VariantRefreshResult] = []
    latest_date: date | None = None
    total_added = 0
    benchmark_results: list[BenchmarkRefreshResult] = []
    benchmark_added = 0

    for key, config in PERFORMANCE_VARIANTS.items():
        try:
            variant_result = _run_variant(
                settings,
                key,
                config,
                df_prices.copy(),
                scratch_dir=scratch_dir,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Variant refresh failed for %s", key)
            variant_result = VariantRefreshResult(
                key=key,
                filename=config["filename"],
                added_rows=0,
                last_date=None,
                latest_date=None,
                error=str(exc),
            )
        results.append(variant_result)
        if variant_result.added_rows > 0:
            total_added += variant_result.added_rows
        if variant_result.latest_date:
            latest_date = max(latest_date, variant_result.latest_date) if latest_date else variant_result.latest_date

    for key, config in BENCHMARK_VARIANTS.items():
        try:
            bench_result = _run_benchmark(settings, key, config, df_prices.copy())
        except Exception as exc:  # noqa: BLE001
            logger.exception("Benchmark refresh failed for %s", key)
            bench_result = BenchmarkRefreshResult(
                key=key,
                filename=config["filename"],
                added_rows=0,
                last_date=None,
                latest_date=None,
                error=str(exc),
            )
        benchmark_results.append(bench_result)
        if bench_result.added_rows > 0:
            benchmark_added += bench_result.added_rows
        if bench_result.latest_date:
            latest_date = max(latest_date, bench_result.latest_date) if latest_date else bench_result.latest_date

    if total_added > 0 or benchmark_added > 0:
        load_performance_bundle.cache_clear()

    return PerformanceRefreshReport(
        variants=results,
        total_added=total_added,
        latest_date=latest_date,
        benchmarks=benchmark_results,
        benchmark_total_added=benchmark_added,
    )


def collect_variant_snapshots(settings: ServiceSettings) -> list[dict[str, object]]:
    snapshots: list[dict[str, object]] = []
    for key, config in PERFORMANCE_VARIANTS.items():
        primary_path = resolve_performance_series_read_path(
            filename=config["filename"],
            runs_root=settings.runs_root,
        )
        if primary_path is None:
            primary_path = resolve_performance_series_write_path(
                runs_root=settings.runs_root,
                filename=config["filename"],
            )
        df = _load_equity_curve(primary_path)
        last_date = df["date"].max().date() if not df.empty else None
        snapshots.append(
            {
                "key": key,
                "filename": config["filename"],
                "strategy": config["strategy"],
                "last_date": last_date.isoformat() if last_date else None,
                "rows": len(df),
            }
        )
    return snapshots


def collect_benchmark_snapshots(settings: ServiceSettings) -> list[dict[str, object]]:
    snapshots: list[dict[str, object]] = []
    for key, config in BENCHMARK_VARIANTS.items():
        primary_path = resolve_performance_series_read_path(
            filename=config["filename"],
            runs_root=settings.runs_root,
        )
        if primary_path is None:
            primary_path = resolve_performance_series_write_path(
                runs_root=settings.runs_root,
                filename=config["filename"],
            )
        df = _load_benchmark_frame(primary_path)
        last_date = df["date"].max().date() if not df.empty else None
        snapshots.append(
            {
                "key": key,
                "filename": config["filename"],
                "column": config["column"],
                "last_date": last_date.isoformat() if last_date else None,
                "rows": len(df),
            }
        )
    return snapshots


def update_next_run_after_success(settings: ServiceSettings, config: AutoRunConfig, report: PerformanceRefreshReport) -> AutoRunConfig:
    next_run = _next_run_date_from_refresh(report.latest_date)
    updated = AutoRunConfig(
        enabled=config.enabled,
        next_run_date=next_run,
        last_run_at=datetime.utcnow(),
        last_run_status="ok",
        last_error=None,
        last_summary=report.to_dict(),
    )
    persist_auto_config(settings, updated)
    return updated


def update_next_run_after_failure(
    settings: ServiceSettings,
    config: AutoRunConfig,
    error_message: str,
) -> AutoRunConfig:
    tomorrow = date.today() + timedelta(days=1)
    if config.next_run_date and config.next_run_date > tomorrow:
        fallback_date = config.next_run_date
    else:
        fallback_date = tomorrow
    updated = AutoRunConfig(
        enabled=config.enabled,
        next_run_date=fallback_date,
        last_run_at=datetime.utcnow(),
        last_run_status="error",
        last_error=error_message,
        last_summary=config.last_summary or {},
    )
    persist_auto_config(settings, updated)
    return updated


def latest_snapshot_date(snapshots: Iterable[dict[str, object]]) -> date | None:
    latest: date | None = None
    for item in snapshots:
        raw_date = item.get("last_date")
        if not raw_date:
            continue
        try:
            parsed = date.fromisoformat(str(raw_date))
        except (TypeError, ValueError):
            continue
        latest = max(latest, parsed) if latest else parsed
    return latest
