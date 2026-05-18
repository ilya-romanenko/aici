from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
import json
import logging
from pathlib import Path
import re

import numpy as np
import pandas as pd

from ai_crypto_index.shared import run_store
from ai_crypto_index.shared.performance_series_store import (
    iter_performance_series_read_candidates,
    resolve_performance_series_read_path,
)
from ai_crypto_index.shared.performance_snapshot import DEFAULT_STRATEGY_KEY, INDEX_VARIANTS
from ai_crypto_index.shared.settings import ServiceSettings

logger = logging.getLogger("ai_crypto_index.live_series")

DEFAULT_LIVE_RUN_PREFIX = "auto-classic"
LIVE_SERIES_SHORT_THRESHOLD_DAYS = 30
DEFAULT_FEES_INCLUDED = False
DEFAULT_SLIPPAGE_INCLUDED = False
PRICE_FILENAME = "merged_prices.csv"
_LIVE_SERIES_DIR_NAME = "live_series"  # under runs/_performance/
BENCHMARK_SERIES_FILENAMES: dict[str, str] = {
    "btc": "BTC_USD.csv",
    "eth": "ETH_USD.csv",
}
_BACKTEST_STRATEGY_ALIASES: dict[str, str] = {
    "aggressive": "risky",
}


class LiveBacktestDataError(RuntimeError):
    """Raised when live/backtest payload cannot be assembled."""


@dataclass(frozen=True)
class SeriesPoint:
    date: str
    value: float

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class CalculationBasis:
    frequency: str
    currency: str
    timestamp_policy: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class LiveBacktestPayload:
    live_start_date: str | None
    backtest_window_start: str
    backtest_window_end: str
    fees_included: bool
    slippage_included: bool
    has_live_history: bool
    is_live_series_short: bool
    live_series: list[SeriesPoint]
    backtest_series: list[SeriesPoint]
    benchmark_series: list[SeriesPoint]
    live_source: str | None
    backtest_source: str
    calculation_basis: CalculationBasis

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["live_series"] = [item.to_dict() for item in self.live_series]
        payload["backtest_series"] = [item.to_dict() for item in self.backtest_series]
        payload["calculation_basis"] = self.calculation_basis.to_dict()
        return payload


def _resolve_backtest_path(settings: ServiceSettings, strategy_key: str) -> Path:
    config = INDEX_VARIANTS.get(strategy_key)
    if config is None:
        raise LiveBacktestDataError(f"unknown strategy key '{strategy_key}'")

    path_value = config.get("path")
    if isinstance(path_value, Path):
        if not path_value.exists():
            raise LiveBacktestDataError(f"backtest source not found at '{path_value}'")
        return path_value

    filename = str(config.get("filename") or "").strip()
    if not filename:
        raise LiveBacktestDataError(
            f"invalid backtest path for strategy '{strategy_key}'"
        )

    resolved = resolve_performance_series_read_path(
        filename=filename,
        runs_root=settings.runs_root,
    )
    if resolved is not None:
        return resolved

    candidates = ", ".join(
        str(path)
        for path in iter_performance_series_read_candidates(
            filename=filename,
            runs_root=settings.runs_root,
        )
    )
    raise LiveBacktestDataError(
        f"backtest source not found for strategy '{strategy_key}'. checked: {candidates}"
    )


def resolve_live_backtest_strategy_key(strategy_key: str) -> str:
    normalized = str(strategy_key or "").strip().lower()
    if not normalized:
        return DEFAULT_STRATEGY_KEY
    return _BACKTEST_STRATEGY_ALIASES.get(normalized, normalized)


def _normalize_dates(df: pd.DataFrame, *, date_column: str) -> pd.DataFrame:
    normalized = df.copy()
    normalized[date_column] = pd.to_datetime(normalized[date_column], errors="coerce")
    normalized = normalized.dropna(subset=[date_column]).sort_values(date_column)
    normalized[date_column] = normalized[date_column].dt.normalize()
    normalized = normalized.drop_duplicates(subset=[date_column], keep="last")
    return normalized.reset_index(drop=True)


def _load_backtest_frame(path: Path) -> pd.DataFrame:
    raw = pd.read_csv(path)
    if raw.empty:
        raise LiveBacktestDataError(f"backtest source is empty: '{path}'")

    date_column = "date" if "date" in raw.columns else raw.columns[0]
    frame = _normalize_dates(raw, date_column=date_column).rename(columns={date_column: "date"})

    if "log_return" in frame.columns:
        frame["log_return"] = pd.to_numeric(frame["log_return"], errors="coerce")
        frame = frame.dropna(subset=["log_return"]).reset_index(drop=True)
        frame["equity"] = np.exp(frame["log_return"].cumsum())
    elif "equity" in frame.columns:
        frame["equity"] = pd.to_numeric(frame["equity"], errors="coerce")
        frame = frame.dropna(subset=["equity"]).reset_index(drop=True)
    elif "equity_curve" in frame.columns:
        frame["equity"] = pd.to_numeric(frame["equity_curve"], errors="coerce")
        frame = frame.dropna(subset=["equity"]).reset_index(drop=True)
    else:
        raise LiveBacktestDataError(f"backtest file '{path}' lacks log_return/equity columns")

    if frame.empty:
        raise LiveBacktestDataError(f"backtest source has no valid rows: '{path}'")

    base = float(frame["equity"].iloc[0])
    if not np.isfinite(base) or base == 0.0:
        raise LiveBacktestDataError("backtest equity base is invalid")
    frame["equity"] = frame["equity"] / base
    return frame[["date", "equity"]]


def _load_live_frame(path: Path) -> pd.DataFrame:
    raw = pd.read_csv(path)
    if raw.empty:
        return pd.DataFrame(columns=["date", "equity"])

    date_column = "date" if "date" in raw.columns else raw.columns[0]
    frame = _normalize_dates(raw, date_column=date_column).rename(columns={date_column: "date"})

    if "equity_curve" in frame.columns:
        frame["equity"] = pd.to_numeric(frame["equity_curve"], errors="coerce")
    elif "equity" in frame.columns:
        frame["equity"] = pd.to_numeric(frame["equity"], errors="coerce")
    elif "log_return" in frame.columns:
        frame["log_return"] = pd.to_numeric(frame["log_return"], errors="coerce")
        frame = frame.dropna(subset=["log_return"]).reset_index(drop=True)
        frame["equity"] = np.exp(frame["log_return"].cumsum())
    else:
        return pd.DataFrame(columns=["date", "equity"])

    frame = frame.dropna(subset=["equity"]).reset_index(drop=True)
    return frame[["date", "equity"]]


def _resolve_run_timestamp(run_dir: Path) -> pd.Timestamp:
    match = re.search(
        r"(\d{4})-(\d{2})-(\d{2})T(\d{2})-(\d{2})-(\d{2})Z",
        run_dir.name,
    )
    if match:
        try:
            return pd.Timestamp(
                int(match.group(1)),
                int(match.group(2)),
                int(match.group(3)),
                int(match.group(4)),
                int(match.group(5)),
                int(match.group(6)),
            )
        except ValueError:
            pass
    return pd.Timestamp(run_dir.stat().st_mtime, unit="s")


def _resolve_run_month(run_dir: Path) -> str:
    ts = _resolve_run_timestamp(run_dir)
    return f"{ts.year:04d}-{ts.month:02d}"


def _resolve_run_month_from_equity_curve(run_dir: Path) -> str | None:
    equity_path = run_dir / run_store.EQUITY_CURVE_ARTIFACT
    if not equity_path.exists() or equity_path.stat().st_size <= 0:
        return None
    frame = _load_live_frame(equity_path)
    if frame.empty:
        return None
    month_ts = frame["date"].iloc[-1]
    if not isinstance(month_ts, pd.Timestamp):
        month_ts = pd.to_datetime(month_ts, errors="coerce")
    if not isinstance(month_ts, pd.Timestamp) or pd.isna(month_ts):
        return None
    return f"{month_ts.year:04d}-{month_ts.month:02d}"


def _resolve_run_month_candidates(run_dir: Path) -> list[str]:
    candidates: list[str] = []
    primary_month = _resolve_run_month(run_dir)
    if primary_month:
        candidates.append(primary_month)
    equity_month = _resolve_run_month_from_equity_curve(run_dir)
    if equity_month and equity_month not in candidates:
        candidates.append(equity_month)
    return candidates


def _resolve_price_history_path(settings: ServiceSettings) -> Path:
    config_dir = settings.config_path.resolve().parent
    repo_root = config_dir.parent if config_dir.name.lower() == "config" else config_dir
    return repo_root / "data" / PRICE_FILENAME


def _load_price_history(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"price history not found at '{path}'")
    frame = pd.read_csv(path, index_col=0, parse_dates=True)
    if frame.empty:
        raise ValueError(f"price history '{path}' is empty")
    frame.index = pd.to_datetime(frame.index, errors="coerce")
    frame = frame[~frame.index.isna()]
    if frame.empty:
        raise ValueError(f"price history '{path}' has no valid datetime index")
    frame = frame.sort_index()
    return frame


def _month_bounds(month_key: str) -> tuple[pd.Timestamp, pd.Timestamp] | None:
    match = re.match(r"^(\d{4})-(\d{2})$", str(month_key or "").strip())
    if not match:
        return None
    year = int(match.group(1))
    month = int(match.group(2))
    if month < 1 or month > 12:
        return None
    start_ts = pd.Timestamp(year=year, month=month, day=1)
    end_ts = (start_ts + pd.offsets.MonthEnd(1)).normalize()
    return start_ts, end_ts


def _load_run_weights_series(run_dir: Path) -> pd.Series:
    try:
        rows = run_store.load_weights(run_dir)
    except Exception:
        return pd.Series(dtype=float)

    if not rows:
        return pd.Series(dtype=float)

    weights: dict[str, float] = {}
    for row in rows:
        asset = str(row.get("asset") or "").strip()
        weight_raw = row.get("weight", 0.0)
        try:
            weight = float(weight_raw)
        except (TypeError, ValueError):
            continue
        if not asset or not np.isfinite(weight) or weight <= 0.0:
            continue
        weights[asset] = weights.get(asset, 0.0) + weight
    if not weights:
        return pd.Series(dtype=float)

    series = pd.Series(weights, dtype=float)
    total = float(series.sum())
    if not np.isfinite(total) or total <= 0.0:
        return pd.Series(dtype=float)
    return series / total


def _build_month_frame_from_weights(
    *,
    month_key: str,
    run_dir: Path,
    df_prices: pd.DataFrame,
    start_equity: float,
) -> pd.DataFrame:
    bounds = _month_bounds(month_key)
    if bounds is None:
        return pd.DataFrame(columns=["date", "equity"])
    month_start_ts, month_end_ts = bounds

    weights = _load_run_weights_series(run_dir)
    if weights.empty:
        return pd.DataFrame(columns=["date", "equity"])

    assets = [asset for asset in weights.index if asset in df_prices.columns]
    if not assets:
        return pd.DataFrame(columns=["date", "equity"])

    prices = df_prices.loc[df_prices.index <= month_end_ts, assets].copy()
    if prices.empty:
        return pd.DataFrame(columns=["date", "equity"])
    prices = prices.sort_index()

    returns = np.log(prices / prices.shift(1))
    returns = returns.loc[(returns.index >= month_start_ts) & (returns.index <= month_end_ts)]
    if returns.empty:
        return pd.DataFrame(columns=["date", "equity"])

    portfolio_dates: list[pd.Timestamp] = []
    portfolio_log_returns: list[float] = []
    for ts, row in returns.iterrows():
        valid = row.replace([np.inf, -np.inf], np.nan).dropna()
        if valid.empty:
            continue
        available_weights = weights.reindex(valid.index).dropna()
        if available_weights.empty:
            continue
        normalizer = float(available_weights.sum())
        if not np.isfinite(normalizer) or normalizer <= 0.0:
            continue
        normalized_weights = available_weights / normalizer
        daily_log_return = float(np.dot(valid.loc[normalized_weights.index].values, normalized_weights.values))
        if not np.isfinite(daily_log_return):
            continue
        portfolio_dates.append(pd.Timestamp(ts))
        portfolio_log_returns.append(daily_log_return)

    if not portfolio_dates:
        return pd.DataFrame(columns=["date", "equity"])

    equity = start_equity * np.exp(np.cumsum(np.array(portfolio_log_returns, dtype=float)))
    frame = pd.DataFrame({"date": portfolio_dates, "equity": equity})
    frame = frame.sort_values("date").drop_duplicates(subset=["date"], keep="last")
    return frame.reset_index(drop=True)


def _build_month_frame_from_equity_curve(
    *,
    run_dir: Path,
    month_key: str,
    start_equity: float,
) -> pd.DataFrame:
    equity_path = run_dir / run_store.EQUITY_CURVE_ARTIFACT
    if not equity_path.exists() or equity_path.stat().st_size <= 0:
        return pd.DataFrame(columns=["date", "equity"])

    frame = _load_live_frame(equity_path)
    if frame.empty:
        return pd.DataFrame(columns=["date", "equity"])

    month_frame = frame.loc[frame["date"].dt.strftime("%Y-%m") == month_key].copy()
    if month_frame.empty:
        return pd.DataFrame(columns=["date", "equity"])

    base = float(month_frame["equity"].iloc[0])
    if not np.isfinite(base) or base == 0.0:
        return pd.DataFrame(columns=["date", "equity"])

    month_frame["equity"] = (month_frame["equity"] / base) * start_equity
    month_frame = month_frame[["date", "equity"]]
    month_frame = month_frame.sort_values("date").drop_duplicates(subset=["date"], keep="last")
    return month_frame.reset_index(drop=True)


def _load_benchmark_price_series(path: Path) -> pd.Series:
    if not path.exists() or path.stat().st_size <= 0:
        return pd.Series(dtype=float)
    raw = pd.read_csv(path)
    if raw.empty:
        return pd.Series(dtype=float)

    date_column = "date" if "date" in raw.columns else raw.columns[0]
    value_column = None
    for candidate in ("Close", "close", "Adj Close", "adj_close", "value"):
        if candidate in raw.columns:
            value_column = candidate
            break
    if value_column is None:
        remaining = [column for column in raw.columns if column != date_column]
        value_column = remaining[0] if remaining else None
    if value_column is None:
        return pd.Series(dtype=float)

    frame = raw.rename(columns={date_column: "date", value_column: "price"})
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.normalize()
    frame["price"] = pd.to_numeric(frame["price"], errors="coerce")
    frame = frame.dropna(subset=["date", "price"]).sort_values("date")
    if frame.empty:
        return pd.Series(dtype=float)
    series = frame.set_index("date")["price"].astype(float)
    series = series[~series.index.duplicated(keep="last")]
    return series


def _build_continuous_series(
    *,
    backtest_points: list[SeriesPoint],
    live_points: list[SeriesPoint],
) -> list[SeriesPoint]:
    by_date: dict[str, float] = {}
    ordered_dates: list[str] = []
    for point in backtest_points:
        if point.date not in by_date:
            ordered_dates.append(point.date)
        by_date[point.date] = float(point.value)
    for point in live_points:
        if point.date not in by_date:
            ordered_dates.append(point.date)
        by_date[point.date] = float(point.value)

    ordered_dates.sort()
    merged: list[SeriesPoint] = []
    for iso_date in ordered_dates:
        value = by_date.get(iso_date)
        if value is None or not np.isfinite(value):
            continue
        merged.append(SeriesPoint(date=iso_date, value=float(value)))
    return merged


def _build_benchmark_series_for_dates(
    settings: ServiceSettings,
    *,
    target_dates: list[str],
) -> list[SeriesPoint]:
    normalized_dates: list[pd.Timestamp] = []
    for raw_date in target_dates:
        ts = pd.to_datetime(raw_date, errors="coerce")
        if pd.isna(ts):
            continue
        normalized_dates.append(pd.Timestamp(ts).normalize())
    if not normalized_dates:
        return []

    btc_path = resolve_performance_series_read_path(
        filename=BENCHMARK_SERIES_FILENAMES["btc"],
        runs_root=settings.runs_root,
    )
    eth_path = resolve_performance_series_read_path(
        filename=BENCHMARK_SERIES_FILENAMES["eth"],
        runs_root=settings.runs_root,
    )
    if btc_path is None or eth_path is None:
        return []

    btc_prices = _load_benchmark_price_series(btc_path)
    eth_prices = _load_benchmark_price_series(eth_path)
    if btc_prices.empty or eth_prices.empty:
        return []

    target_index = pd.DatetimeIndex(normalized_dates)
    btc_aligned = btc_prices.reindex(target_index, method="ffill").bfill()
    eth_aligned = eth_prices.reindex(target_index, method="ffill").bfill()

    valid_mask = (~btc_aligned.isna()) & (~eth_aligned.isna())
    if not valid_mask.any():
        return []
    if not valid_mask.all():
        target_index = target_index[valid_mask]
        btc_aligned = btc_aligned[valid_mask]
        eth_aligned = eth_aligned[valid_mask]

    portfolio_dates: list[pd.Timestamp] = []
    portfolio_values: list[float] = []
    current_value = 1.0
    units_btc = 0.0
    units_eth = 0.0
    last_period: pd.Period | None = None

    for ts, btc_price, eth_price in zip(target_index, btc_aligned.values, eth_aligned.values):
        price_btc = float(btc_price)
        price_eth = float(eth_price)
        if not np.isfinite(price_btc) or not np.isfinite(price_eth):
            continue
        if price_btc <= 0.0 or price_eth <= 0.0:
            continue

        period = ts.to_period("M")
        if last_period is None or period != last_period:
            units_btc = 0.5 * current_value / price_btc
            units_eth = 0.5 * current_value / price_eth

        current_value = units_btc * price_btc + units_eth * price_eth
        if not np.isfinite(current_value) or current_value <= 0.0:
            continue

        portfolio_dates.append(pd.Timestamp(ts))
        portfolio_values.append(float(current_value))
        last_period = period

    if not portfolio_values:
        return []

    normalized_equity = np.array(portfolio_values, dtype=float)
    normalized_equity = normalized_equity / normalized_equity[0]
    return [
        SeriesPoint(date=ts.date().isoformat(), value=float(value))
        for ts, value in zip(portfolio_dates, normalized_equity)
    ]


def _load_first_live_run_per_month_series(
    settings: ServiceSettings,
    *,
    live_run_prefix: str,
    max_completed_month: str | None = None,
) -> tuple[pd.DataFrame, list[str]]:
    runs = list(run_store.iter_completed_runs(settings, prefix=live_run_prefix))
    if not runs:
        return pd.DataFrame(columns=["date", "equity"]), []

    try:
        df_prices = _load_price_history(_resolve_price_history_path(settings))
    except Exception:
        df_prices = pd.DataFrame()

    selected_months: set[str] = set()
    run_ids: list[str] = []
    month_frames: list[pd.DataFrame] = []
    cumulative_equity = 1.0

    for run_dir in sorted(runs, key=_resolve_run_timestamp):
        selected_month_key: str | None = None
        selected_month_frame = pd.DataFrame(columns=["date", "equity"])
        month_candidates = _resolve_run_month_candidates(run_dir)

        for candidate_index, month_key in enumerate(month_candidates):
            if max_completed_month and month_key > max_completed_month:
                continue
            if month_key in selected_months:
                continue

            prefer_equity_curve = candidate_index > 0
            if prefer_equity_curve:
                month_frame = _build_month_frame_from_equity_curve(
                    run_dir=run_dir,
                    month_key=month_key,
                    start_equity=cumulative_equity,
                )
                if month_frame.empty:
                    month_frame = _build_month_frame_from_weights(
                        month_key=month_key,
                        run_dir=run_dir,
                        df_prices=df_prices,
                        start_equity=cumulative_equity,
                    )
            else:
                month_frame = _build_month_frame_from_weights(
                    month_key=month_key,
                    run_dir=run_dir,
                    df_prices=df_prices,
                    start_equity=cumulative_equity,
                )
                if month_frame.empty:
                    month_frame = _build_month_frame_from_equity_curve(
                        run_dir=run_dir,
                        month_key=month_key,
                        start_equity=cumulative_equity,
                    )

            if month_frame.empty:
                continue

            selected_month_key = month_key
            selected_month_frame = month_frame
            break

        if selected_month_key is None or selected_month_frame.empty:
            continue
        cumulative_equity = float(selected_month_frame["equity"].iloc[-1])

        selected_months.add(selected_month_key)
        run_ids.append(run_dir.name)
        month_frames.append(selected_month_frame[["date", "equity"]])

    if not month_frames:
        return pd.DataFrame(columns=["date", "equity"]), []

    combined = pd.concat(month_frames, ignore_index=True)
    combined = combined.sort_values("date").drop_duplicates(subset=["date"], keep="last")
    combined = combined.reset_index(drop=True)
    return combined, run_ids


def _frame_to_series_points(frame: pd.DataFrame) -> list[SeriesPoint]:
    points: list[SeriesPoint] = []
    for row in frame.itertuples(index=False):
        ts = getattr(row, "date")
        value = float(getattr(row, "equity"))
        if isinstance(ts, pd.Timestamp):
            iso_date = ts.date().isoformat()
        elif isinstance(ts, date):
            iso_date = ts.isoformat()
        else:
            iso_date = str(ts)
        points.append(SeriesPoint(date=iso_date, value=value))
    return points


def _clip_live_frame_to_completed_month(
    frame: pd.DataFrame,
    *,
    today_utc: date,
) -> pd.DataFrame:
    if frame.empty:
        return frame

    cutoff = _resolve_completed_month_cutoff(today_utc)
    clipped = frame.loc[frame["date"] <= cutoff].copy()
    if clipped.empty:
        return pd.DataFrame(columns=["date", "equity"])
    return clipped.reset_index(drop=True)


def _resolve_completed_month_cutoff(today_utc: date) -> pd.Timestamp:
    current_month_start = pd.Timestamp(today_utc.replace(day=1))
    return current_month_start - pd.Timedelta(days=1)


def _densify_live_frame_with_backtest(
    live_frame: pd.DataFrame,
    *,
    backtest_frame_full: pd.DataFrame,
    coverage_end_ts: pd.Timestamp,
) -> pd.DataFrame:
    if live_frame.empty:
        return live_frame

    live_start_ts = live_frame["date"].iloc[0]
    live_end_ts = live_frame["date"].iloc[-1]
    window_end_ts = max(live_end_ts, coverage_end_ts)
    backtest_window = backtest_frame_full.loc[
        (backtest_frame_full["date"] >= live_start_ts)
        & (backtest_frame_full["date"] <= window_end_ts)
    ][["date", "equity"]].copy()

    backtest_window = backtest_window.copy()
    backtest_window["_source_order"] = 0
    live_only = live_frame[["date", "equity"]].copy()
    live_only["_source_order"] = 1
    combined = pd.concat([backtest_window, live_only], ignore_index=True)
    if combined.empty:
        return pd.DataFrame(columns=["date", "equity"])
    combined = combined.sort_values(["date", "_source_order"], kind="mergesort")
    combined = combined.drop_duplicates(subset=["date"], keep="last")
    combined = combined.drop(columns=["_source_order"], errors="ignore")
    return combined.reset_index(drop=True)


def _live_series_dir(settings: ServiceSettings, live_run_prefix: str) -> Path:
    return settings.runs_root / "_performance" / _LIVE_SERIES_DIR_NAME / live_run_prefix


def store_live_run_month(
    settings: ServiceSettings,
    run_dir: Path,
    live_run_prefix: str,
    *,
    df_prices: pd.DataFrame | None = None,
    overwrite: bool = False,
) -> dict[str, object]:
    """Compute the monthly equity series for a live run and persist it.

    Stores two files per month under runs/_performance/live_series/{live_run_prefix}/:
      - {YYYY-MM}.csv        — daily equity curve (normalised to start at 1.0)
      - {YYYY-MM}.meta.json  — run_id, weights, computed_at, etc.

    Returns a dict with fields: stored (bool), month_key, run_id, days, reason.
    """
    month_candidates = _resolve_run_month_candidates(run_dir)
    if not month_candidates:
        return {"stored": False, "reason": "no_month_candidates"}

    month_key = month_candidates[0]

    out_dir = _live_series_dir(settings, live_run_prefix)
    out_dir.mkdir(parents=True, exist_ok=True)

    csv_path = out_dir / f"{month_key}.csv"
    meta_path = out_dir / f"{month_key}.meta.json"

    if not overwrite and csv_path.exists() and meta_path.exists():
        return {"stored": False, "reason": "already_exists", "month_key": month_key}

    if df_prices is None:
        try:
            df_prices = _load_price_history(_resolve_price_history_path(settings))
        except Exception as exc:  # noqa: BLE001
            return {"stored": False, "reason": f"prices_unavailable: {exc}"}

    month_frame = _build_month_frame_from_weights(
        month_key=month_key,
        run_dir=run_dir,
        df_prices=df_prices,
        start_equity=1.0,
    )
    if month_frame.empty:
        return {"stored": False, "reason": "empty_frame", "month_key": month_key}

    out_frame = month_frame.copy()
    out_frame["date"] = out_frame["date"].dt.strftime("%Y-%m-%d")
    out_frame.to_csv(csv_path, index=False)

    weights = _load_run_weights_series(run_dir)
    meta: dict[str, object] = {
        "month": month_key,
        "run_id": run_dir.name,
        "weights": weights.to_dict() if not weights.empty else {},
        "computed_at": datetime.utcnow().isoformat() + "Z",
        "days_in_series": len(month_frame),
    }
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    logger.info(
        "stored_live_month prefix=%s month=%s run=%s days=%d",
        live_run_prefix,
        month_key,
        run_dir.name,
        len(month_frame),
    )
    return {"stored": True, "month_key": month_key, "run_id": run_dir.name, "days": len(month_frame)}


def list_stored_live_months(
    settings: ServiceSettings,
    live_run_prefix: str,
) -> list[dict[str, object]]:
    """Return metadata for all stored monthly live series for a given run prefix."""
    series_dir = _live_series_dir(settings, live_run_prefix)
    if not series_dir.exists():
        return []

    results: list[dict[str, object]] = []
    for meta_path in sorted(series_dir.glob("????-??.meta.json")):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            meta = {"month": meta_path.stem}
        csv_path = series_dir / f"{meta_path.stem}.csv"
        meta["has_csv"] = csv_path.exists()
        results.append(meta)
    return results


def _load_stored_live_series(
    settings: ServiceSettings,
    live_run_prefix: str,
    *,
    max_completed_month: str | None = None,
) -> tuple[pd.DataFrame, list[str]]:
    """Load pre-stored monthly equity CSVs, chain them into a continuous frame.

    Each CSV is stored with equity starting at 1.0; this function re-chains
    them so equity is continuous across months (same behaviour as the on-the-fly
    computation in _load_first_live_run_per_month_series).
    """
    series_dir = _live_series_dir(settings, live_run_prefix)
    if not series_dir.exists():
        return pd.DataFrame(columns=["date", "equity"]), []

    month_csv_files = sorted(series_dir.glob("????-??.csv"))
    if not month_csv_files:
        return pd.DataFrame(columns=["date", "equity"]), []

    run_ids: list[str] = []
    month_frames: list[pd.DataFrame] = []
    cumulative_equity = 1.0

    for csv_path in month_csv_files:
        month_key = csv_path.stem
        if max_completed_month and month_key > max_completed_month:
            continue

        try:
            frame = pd.read_csv(csv_path)
            frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
            frame["equity"] = pd.to_numeric(frame["equity"], errors="coerce")
            frame = frame.dropna(subset=["date", "equity"]).sort_values("date")
        except Exception:  # noqa: BLE001
            logger.warning("failed_to_load_stored_live_month prefix=%s month=%s", live_run_prefix, month_key)
            continue

        if frame.empty:
            continue

        base = float(frame["equity"].iloc[0])
        if not np.isfinite(base) or base == 0.0:
            continue

        frame = frame.copy()
        frame["equity"] = (frame["equity"] / base) * cumulative_equity
        cumulative_equity = float(frame["equity"].iloc[-1])
        month_frames.append(frame[["date", "equity"]])

        run_id = month_key
        meta_path = series_dir / f"{month_key}.meta.json"
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                run_id = str(meta.get("run_id", month_key))
            except Exception:  # noqa: BLE001
                pass
        run_ids.append(run_id)

    if not month_frames:
        return pd.DataFrame(columns=["date", "equity"]), []

    combined = pd.concat(month_frames, ignore_index=True)
    combined = combined.sort_values("date").drop_duplicates(subset=["date"], keep="last")
    return combined.reset_index(drop=True), run_ids


def build_live_backtest_payload(
    settings: ServiceSettings,
    *,
    strategy_key: str = DEFAULT_STRATEGY_KEY,
    live_run_prefix: str = DEFAULT_LIVE_RUN_PREFIX,
    short_series_threshold_days: int = LIVE_SERIES_SHORT_THRESHOLD_DAYS,
    fees_included: bool = DEFAULT_FEES_INCLUDED,
    slippage_included: bool = DEFAULT_SLIPPAGE_INCLUDED,
    today_utc: date | None = None,
) -> LiveBacktestPayload:
    effective_today_utc = today_utc or date.today()
    completed_month_cutoff = _resolve_completed_month_cutoff(effective_today_utc)
    max_completed_month = f"{completed_month_cutoff.year:04d}-{completed_month_cutoff.month:02d}"

    resolved_strategy_key = resolve_live_backtest_strategy_key(strategy_key)
    backtest_path = _resolve_backtest_path(settings, resolved_strategy_key)
    backtest_frame_full = _load_backtest_frame(backtest_path)
    clipped_backtest_full = backtest_frame_full.loc[
        backtest_frame_full["date"] <= completed_month_cutoff
    ].copy()
    if not clipped_backtest_full.empty:
        backtest_frame_full = clipped_backtest_full
    backtest_frame = backtest_frame_full.copy()
    full_backtest_points = _frame_to_series_points(backtest_frame_full)
    if not full_backtest_points:
        raise LiveBacktestDataError("backtest series is empty")

    # Prefer pre-stored monthly series (transparent, auditable); fall back to
    # on-the-fly computation for backward compatibility (e.g. first boot before
    # any run has been stored yet).
    raw_live_frame, run_ids = _load_stored_live_series(
        settings,
        live_run_prefix,
        max_completed_month=max_completed_month,
    )
    if raw_live_frame.empty:
        raw_live_frame, run_ids = _load_first_live_run_per_month_series(
            settings,
            live_run_prefix=live_run_prefix,
            max_completed_month=max_completed_month,
        )

    live_frame = _clip_live_frame_to_completed_month(
        raw_live_frame,
        today_utc=effective_today_utc,
    )
    if not live_frame.empty:
        live_start_ts = live_frame["date"].iloc[0]
        backtest_frame = backtest_frame.loc[backtest_frame["date"] < live_start_ts].copy()

    backtest_points = _frame_to_series_points(backtest_frame)
    backtest_window_start = (
        backtest_points[0].date if backtest_points else full_backtest_points[0].date
    )
    backtest_window_end = (
        backtest_points[-1].date if backtest_points else full_backtest_points[-1].date
    )

    has_live_history = not live_frame.empty
    live_start_date: str | None = None
    live_points: list[SeriesPoint] = []
    live_source: str | None = None
    is_live_series_short = False

    if has_live_history:
        live_base = float(live_frame["equity"].iloc[0])
        if np.isfinite(live_base) and live_base != 0.0:
            continuation_base = (
                float(backtest_frame["equity"].iloc[-1]) if not backtest_frame.empty else 1.0
            )
            live_frame["equity"] = live_frame["equity"] * (continuation_base / live_base)
            live_frame = _densify_live_frame_with_backtest(
                live_frame,
                backtest_frame_full=backtest_frame_full,
                coverage_end_ts=completed_month_cutoff,
            )
        else:
            has_live_history = False
            live_frame = pd.DataFrame(columns=["date", "equity"])

    if has_live_history:
        live_points = _frame_to_series_points(live_frame)
        live_start_date = live_points[0].date
        is_live_series_short = len(live_points) < max(1, short_series_threshold_days)
        if run_ids:
            first_run_id = run_ids[0]
            live_source = f"runs/{first_run_id}/{run_store.EQUITY_CURVE_ARTIFACT}"

    continuous_points = _build_continuous_series(
        backtest_points=backtest_points,
        live_points=live_points,
    )
    benchmark_points = _build_benchmark_series_for_dates(
        settings,
        target_dates=[point.date for point in continuous_points],
    )

    basis = CalculationBasis(
        frequency="1d",
        currency="USD",
        timestamp_policy="UTC daily close",
    )

    return LiveBacktestPayload(
        live_start_date=live_start_date,
        backtest_window_start=backtest_window_start,
        backtest_window_end=backtest_window_end,
        fees_included=fees_included,
        slippage_included=slippage_included,
        has_live_history=has_live_history,
        is_live_series_short=is_live_series_short,
        live_series=live_points,
        backtest_series=backtest_points,
        benchmark_series=benchmark_points,
        live_source=live_source,
        backtest_source=str(backtest_path),
        calculation_basis=basis,
    )


__all__ = [
    "DEFAULT_LIVE_RUN_PREFIX",
    "LIVE_SERIES_SHORT_THRESHOLD_DAYS",
    "LiveBacktestDataError",
    "LiveBacktestPayload",
    "build_live_backtest_payload",
    "list_stored_live_months",
    "resolve_live_backtest_strategy_key",
    "store_live_run_month",
]
