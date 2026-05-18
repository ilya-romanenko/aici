from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from math import ceil, sqrt
from pathlib import Path

import numpy as np
import pandas as pd
from ai_crypto_index.shared.performance_series_store import (
    iter_performance_series_read_candidates,
    resolve_performance_series_read_path,
)

ANNUALIZATION_FACTOR = 365
RISK_FREE_RATE = 0.05
CHART_WIDTH = 640
CHART_HEIGHT = 360
CHART_PADDING_LEFT = 20.0
CHART_PADDING_RIGHT = 20.0
CHART_PADDING_TOP = 20.0
CHART_PADDING_BOTTOM = 40.0
MAX_PATH_POINTS = 420

INDEX_VARIANTS: dict[str, dict[str, object]] = {
    "classic": {
        "label": "Classic",
        "description": "Core multi-factor blend targeting balanced growth.",
        "filename": "AICI_classic.csv",
    },
    "conservative": {
        "label": "Conservative",
        "description": "Lower volatility sleeve with defensive tilts.",
        "filename": "AICI_conservative.csv",
    },
    "risky": {
        "label": "Aggressive",
        "description": "High-beta allocation maximising upside capture.",
        "filename": "AICI_risky.csv",
    },
}
DEFAULT_STRATEGY_KEY = "classic"
BENCHMARK_FILES: dict[str, str] = {
    "btc": "BTC_USD.csv",
    "eth": "ETH_USD.csv",
}


@dataclass(frozen=True)
class SeriesPaths:
    line_path: str
    fill_path: str | None
    marker_x: float
    marker_y: float
    points: list[SeriesPoint]


@dataclass(frozen=True)
class SeriesPoint:
    x: float
    y: float
    value: float
    value_text: str
    date: str
    date_label: str


@dataclass(frozen=True)
class AxisTick:
    coordinate: float
    label: str
    value: float | str


@dataclass(frozen=True)
class ChartAxes:
    x_ticks: list[AxisTick]
    y_ticks: list[AxisTick]


@dataclass(frozen=True)
class LegendItem:
    css_modifier: str
    label: str
    delta_text: str


@dataclass(frozen=True)
class MetricCard:
    label: str
    badge_text: str
    badge_modifier: str
    value_text: str
    delta_text: str
    delta_modifier: str
    note: str


@dataclass(frozen=True)
class PerformanceSnapshot:
    strategy_key: str
    strategy_label: str
    strategy_description: str
    chart_period_label: str
    chart_caption: str
    chart_paths: dict[str, SeriesPaths]
    chart_axes: ChartAxes
    legend: list[LegendItem]
    metric_cards: list[MetricCard]


@dataclass(frozen=True)
class PerformanceBundle:
    default_key: str
    snapshots: dict[str, PerformanceSnapshot]


class PerformanceSnapshotError(RuntimeError):
    pass


def _resolve_index_series_path(
    strategy_key: str,
    config: dict[str, object],
    *,
    runs_root: Path | None,
) -> Path:
    path_value = config.get("path")
    if isinstance(path_value, Path):
        if not path_value.exists():
            raise PerformanceSnapshotError(
                f"index performance file not found at '{path_value}'"
            )
        return path_value

    filename = str(config.get("filename") or "").strip()
    if not filename:
        raise PerformanceSnapshotError(f"filename is not configured for '{strategy_key}'")

    resolved = resolve_performance_series_read_path(
        filename=filename,
        runs_root=runs_root,
    )
    if resolved is None:
        candidates = ", ".join(
            str(path)
            for path in iter_performance_series_read_candidates(
                filename=filename,
                runs_root=runs_root,
            )
        )
        raise PerformanceSnapshotError(
            f"index performance file not found for '{strategy_key}'. "
            f"checked: {candidates}"
        )
    return resolved


def _resolve_benchmark_path(key: str, *, runs_root: Path | None) -> Path:
    filename = BENCHMARK_FILES.get(key)
    if not filename:
        raise PerformanceSnapshotError(f"benchmark filename is not configured for '{key}'")
    resolved = resolve_performance_series_read_path(
        filename=filename,
        runs_root=runs_root,
    )
    if resolved is None:
        candidates = ", ".join(
            str(path)
            for path in iter_performance_series_read_candidates(
                filename=filename,
                runs_root=runs_root,
            )
        )
        raise PerformanceSnapshotError(
            f"benchmark file not found for '{key}'. checked: {candidates}"
        )
    return resolved


def _load_index_dataframe(data_path: Path) -> pd.DataFrame:
    if not data_path.exists():
        raise PerformanceSnapshotError(
            f"index performance file not found at '{data_path}'"
        )

    df = pd.read_csv(data_path)
    if "log_return" not in df.columns:
        raise PerformanceSnapshotError("'log_return' column missing in equity curve CSV")

    date_column = "date" if "date" in df.columns else "Unnamed: 0"
    df = df.rename(columns={date_column: "date"})
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date", "log_return"]).sort_values("date")
    df["log_return"] = pd.to_numeric(df["log_return"], errors="coerce")
    df = df.dropna(subset=["log_return"]).reset_index(drop=True)
    if df.empty:
        raise PerformanceSnapshotError("equity curve CSV has no valid rows")

    df["equity"] = np.exp(df["log_return"].cumsum())
    df["equity"] = df["equity"] / df["equity"].iloc[0]
    return df


def _resolve_value_column(df: pd.DataFrame, date_column: str) -> str:
    candidate_order = ["adj_close", "Adj Close", "close", "Close", "value"]
    for column in candidate_order:
        if column in df.columns:
            return column
    remaining = [col for col in df.columns if col != date_column]
    if not remaining:
        raise PerformanceSnapshotError("benchmark CSV has no price column")
    return remaining[0]


def _build_benchmark_series(index_dates: pd.Series, data_path: Path) -> pd.DataFrame:
    if not data_path.exists():
        raise PerformanceSnapshotError(f"benchmark file not found at '{data_path}'")

    raw_df = pd.read_csv(data_path)
    date_column = "date" if "date" in raw_df.columns else raw_df.columns[0]
    price_column = _resolve_value_column(raw_df, date_column)

    df = raw_df.rename(columns={date_column: "date", price_column: "adj_close"})
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["adj_close"] = pd.to_numeric(df["adj_close"], errors="coerce")
    df = df.dropna(subset=["date", "adj_close"]).sort_values("date")
    if df.empty:
        raise PerformanceSnapshotError(f"benchmark CSV '{data_path.name}' has no valid rows")

    series = df.set_index("date")["adj_close"].astype(float)
    target_index = pd.DatetimeIndex(index_dates)
    aligned = series.reindex(target_index, method="ffill")
    aligned = aligned.bfill()
    if aligned.isna().any():
        message = f"benchmark '{data_path.name}' could not align to index dates"
        raise PerformanceSnapshotError(message)

    log_return = np.log(aligned / aligned.shift(1)).fillna(0.0)
    equity = np.exp(log_return.cumsum())
    equity = equity / equity.iloc[0]

    return pd.DataFrame(
        {
            "date": target_index,
            "log_return": log_return.values,
            "equity": equity.values,
        }
    )


def _build_composite_benchmark(
    index_dates: pd.Series,
    *,
    runs_root: Path | None,
) -> pd.DataFrame:
    btc_frame = _build_benchmark_series(
        index_dates,
        _resolve_benchmark_path("btc", runs_root=runs_root),
    )
    eth_frame = _build_benchmark_series(
        index_dates,
        _resolve_benchmark_path("eth", runs_root=runs_root),
    )

    btc_simple = np.expm1(btc_frame["log_return"])
    eth_simple = np.expm1(eth_frame["log_return"])
    composite_simple = 0.5 * (btc_simple + eth_simple)
    composite_log = np.log1p(composite_simple)
    equity = np.exp(pd.Series(composite_log).cumsum())
    equity = equity / equity.iloc[0]

    return pd.DataFrame(
        {
            "date": btc_frame["date"],
            "log_return": composite_log.values,
            "equity": equity.values,
        }
    )


def _load_aligned_price_series(index_dates: pd.Series, data_path: Path) -> pd.Series:
    """
    Load a benchmark price series and align it to the provided index dates.
    """
    if not data_path.exists():
        raise PerformanceSnapshotError(f"benchmark file not found at '{data_path}'")

    raw_df = pd.read_csv(data_path)
    date_column = "date" if "date" in raw_df.columns else raw_df.columns[0]
    price_column = _resolve_value_column(raw_df, date_column)

    df = raw_df.rename(columns={date_column: "date", price_column: "adj_close"})
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["adj_close"] = pd.to_numeric(df["adj_close"], errors="coerce")
    df = df.dropna(subset=["date", "adj_close"]).sort_values("date")
    if df.empty:
        raise PerformanceSnapshotError(f"benchmark CSV '{data_path.name}' has no valid rows")

    series = df.set_index("date")["adj_close"].astype(float)
    target_index = pd.DatetimeIndex(index_dates)
    aligned = series.reindex(target_index, method="ffill").bfill()
    if aligned.isna().any():
        message = f"benchmark '{data_path.name}' could not align to index dates"
        raise PerformanceSnapshotError(message)
    return aligned


def _build_monthly_rebalanced_composite(
    index_dates: pd.Series,
    *,
    runs_root: Path | None,
) -> pd.DataFrame:
    """
    Build BTC+ETH 50/50 benchmark with monthly rebalancing (start of month).
    """
    btc_prices = _load_aligned_price_series(
        index_dates,
        _resolve_benchmark_path("btc", runs_root=runs_root),
    )
    eth_prices = _load_aligned_price_series(
        index_dates,
        _resolve_benchmark_path("eth", runs_root=runs_root),
    )

    if len(btc_prices) != len(eth_prices):
        raise PerformanceSnapshotError("aligned benchmark series have mismatched lengths")

    portfolio_values: list[float] = []
    log_returns: list[float] = []

    units_btc = 0.0
    units_eth = 0.0
    current_value = 1.0
    last_period = None

    for ts, price_btc, price_eth in zip(pd.DatetimeIndex(index_dates), btc_prices, eth_prices):
        period = ts.to_period("M")
        # Rebalance at the start of each month (including the first point).
        if last_period is None or period != last_period:
            units_btc = 0.5 * current_value / price_btc
            units_eth = 0.5 * current_value / price_eth

        portfolio_value = units_btc * price_btc + units_eth * price_eth
        if portfolio_values:
            log_returns.append(float(np.log(portfolio_value / portfolio_values[-1])))
        else:
            log_returns.append(0.0)
        portfolio_values.append(float(portfolio_value))
        current_value = portfolio_value
        last_period = period

    equity = np.array(portfolio_values, dtype=float)
    equity = equity / equity[0]

    return pd.DataFrame(
        {
            "date": pd.DatetimeIndex(index_dates),
            "log_return": log_returns,
            "equity": equity,
        }
    )


def _downsample(
    series: pd.Series,
    max_points: int = MAX_PATH_POINTS,
) -> tuple[pd.Series, list[int]]:
    total_points = len(series)
    if total_points == 0:
        return series, []

    if total_points <= max_points:
        indices = list(range(total_points))
        return series.reset_index(drop=True), indices

    step = ceil(total_points / max_points)
    indices = list(range(0, total_points, step))
    if indices[-1] != total_points - 1:
        indices.append(total_points - 1)

    sampled = series.iloc[indices].reset_index(drop=True)
    return sampled, indices


def _format_equity_value(value: float) -> str:
    if not np.isfinite(value):
        return "N/A"
    if value >= 10:
        return f"{value:.0f}x"
    if value >= 2:
        return f"{value:.1f}x"
    return f"{value:.2f}x"


def _build_series_points(
    original_series: pd.Series,
    sample_indices: list[int],
    coords: list[tuple[float, float]],
) -> list[SeriesPoint]:
    if not sample_indices or not coords:
        return []

    points: list[SeriesPoint] = []
    for idx, (x_coord, y_coord) in zip(sample_indices, coords):
        value = float(original_series.iloc[idx])
        index_value = original_series.index[idx]
        if isinstance(index_value, pd.Timestamp):
            iso_date = index_value.date().isoformat()
            label = index_value.strftime("%d %b %Y")
        else:
            iso_date = str(index_value)
            label = str(index_value)
        points.append(
            SeriesPoint(
                x=float(x_coord),
                y=float(y_coord),
                value=value,
                value_text=_format_equity_value(value),
                date=iso_date,
                date_label=label,
            )
        )
    return points


def _build_chart_axes(
    dates: pd.Series,
    y_min: float,
    y_max: float,
    max_x_ticks: int = 5,
    max_y_ticks: int = 5,
) -> ChartAxes:
    x_ticks: list[AxisTick] = []
    y_ticks: list[AxisTick] = []

    total_points = len(dates)
    usable_width = CHART_WIDTH - CHART_PADDING_LEFT - CHART_PADDING_RIGHT
    usable_height = CHART_HEIGHT - CHART_PADDING_TOP - CHART_PADDING_BOTTOM

    if total_points:
        tick_count = min(max_x_ticks, total_points)
        if tick_count <= 1:
            indices = [0]
        else:
            step = (total_points - 1) / (tick_count - 1)
            indices = sorted({round(step * i) for i in range(tick_count)})
            if indices[-1] != total_points - 1:
                indices[-1] = total_points - 1
            indices[0] = 0
        for idx in indices:
            ratio = idx / (total_points - 1) if total_points > 1 else 0.0
            coordinate = CHART_PADDING_LEFT + usable_width * ratio
            timestamp = dates.iloc[idx]
            if isinstance(timestamp, pd.Timestamp):
                label = timestamp.strftime("%b %Y")
                value = timestamp.date().isoformat()
            else:
                label = str(timestamp)
                value = str(timestamp)
            x_ticks.append(
                AxisTick(coordinate=round(coordinate, 2), label=label, value=value)
            )

    if not np.isfinite(y_min) or not np.isfinite(y_max):
        y_min, y_max = 0.0, 1.0
    if np.isclose(y_min, y_max):
        spread = y_min if y_min else 1.0
        y_min -= 0.1 * spread
        y_max += 0.1 * spread

    y_tick_count = max(2, max_y_ticks)
    for value in np.linspace(y_min, y_max, y_tick_count):
        ratio = (value - y_min) / (y_max - y_min if y_max > y_min else 1.0)
        coordinate = CHART_HEIGHT - CHART_PADDING_BOTTOM - usable_height * ratio
        y_ticks.append(
            AxisTick(
                coordinate=round(coordinate, 2),
                label=_format_equity_value(float(value)),
                value=float(value),
            )
        )

    return ChartAxes(x_ticks=x_ticks, y_ticks=y_ticks)


def _project_series(series: pd.Series, y_min: float, y_max: float) -> list[tuple[float, float]]:
    if series.empty:
        return []

    normalized = (series - y_min) / (y_max - y_min if y_max > y_min else 1.0)
    usable_width = CHART_WIDTH - CHART_PADDING_LEFT - CHART_PADDING_RIGHT
    usable_height = CHART_HEIGHT - CHART_PADDING_TOP - CHART_PADDING_BOTTOM

    coords: list[tuple[float, float]] = []
    for idx, value in enumerate(normalized):
        position = idx / (len(normalized) - 1) if len(normalized) > 1 else 0.0
        x = CHART_PADDING_LEFT + usable_width * position
        y = CHART_HEIGHT - CHART_PADDING_BOTTOM - usable_height * value
        coords.append((round(x, 2), round(y, 2)))
    return coords


def _coords_to_path(coords: Iterable[tuple[float, float]]) -> str:
    coord_list = list(coords)
    if not coord_list:
        return ""
    head, *tail = coord_list
    segments = [f"M {head[0]} {head[1]}"]
    segments.extend(f"L {x} {y}" for x, y in tail)
    return " ".join(segments)


def _coords_to_fill_path(coords: Iterable[tuple[float, float]]) -> str:
    coord_list = list(coords)
    if not coord_list:
        return ""
    bottom = CHART_HEIGHT - CHART_PADDING_BOTTOM
    head, *tail = coord_list
    segments = [f"M {head[0]} {head[1]}"]
    segments.extend(f"L {x} {y}" for x, y in tail)
    tail_x, _ = coord_list[-1]
    head_x, _ = head
    segments.append(f"L {tail_x} {bottom}")
    segments.append(f"L {head_x} {bottom}")
    segments.append("Z")
    return " ".join(segments)


def _marker_position(value: float, y_min: float, y_max: float) -> tuple[float, float]:
    usable_height = CHART_HEIGHT - CHART_PADDING_TOP - CHART_PADDING_BOTTOM
    ratio = (value - y_min) / (y_max - y_min if y_max > y_min else 1.0)
    x = CHART_WIDTH - CHART_PADDING_RIGHT
    y = CHART_HEIGHT - CHART_PADDING_BOTTOM - usable_height * ratio
    return round(x, 2), round(y, 2)


def _annualized_metrics(equity: pd.Series, log_returns: pd.Series) -> dict[str, float]:
    total_days = max((equity.index[-1] - equity.index[0]).days, 1)
    years = total_days / ANNUALIZATION_FACTOR
    total_return = equity.iloc[-1] / equity.iloc[0]
    cagr = total_return ** (1 / years) - 1 if years > 0 else float("nan")

    std = float(log_returns.std())
    vol = std * sqrt(ANNUALIZATION_FACTOR)
    sharpe = float("nan")
    if std > 0:
        excess = float(log_returns.mean()) - RISK_FREE_RATE / ANNUALIZATION_FACTOR
        sharpe = (excess / std) * sqrt(ANNUALIZATION_FACTOR)

    running_max = equity.cummax()
    drawdown = equity / running_max - 1
    max_drawdown = float(drawdown.min())

    return {
        "cagr": float(cagr),
        "vol": float(vol),
        "sharpe": float(sharpe),
        "max_drawdown": max_drawdown,
        "total_return": float(total_return - 1),
        "years": float(years),
    }


def _format_delta(value: float, precision: int = 1, suffix: str = "") -> str:
    return f"{value:+.{precision}f}{suffix}" if np.isfinite(value) else "N/A"


def _modifier_for_delta(value: float, positive_is_good: bool = True) -> str:
    if not np.isfinite(value) or abs(value) < 1e-6:
        return "neutral"
    is_positive = value >= 0
    if positive_is_good:
        return "positive" if is_positive else "negative"
    return "negative" if is_positive else "positive"


def _build_chart_paths(
    df_index: pd.DataFrame,
    benchmark_frame: pd.DataFrame | None,
) -> tuple[dict[str, SeriesPaths], ChartAxes]:
    series_map: dict[str, pd.Series] = {
        "index": df_index.set_index("date")["equity"],
    }
    if benchmark_frame is not None:
        series_map["benchmark"] = pd.Series(
            benchmark_frame["equity"].values,
            index=df_index["date"],
        )

    y_values = [series for series in series_map.values() if not series.empty]
    y_min = min(series.min() for series in y_values)
    y_max = max(series.max() for series in y_values)
    chart_axes = _build_chart_axes(df_index["date"], float(y_min), float(y_max))

    paths: dict[str, SeriesPaths] = {}
    for key, series in series_map.items():
        sampled, sample_indices = _downsample(series)
        coords = _project_series(sampled, y_min, y_max)
        line_path = _coords_to_path(coords)
        fill_path = _coords_to_fill_path(coords) if key == "index" else None
        marker_x, marker_y = _marker_position(series.iloc[-1], y_min, y_max)
        points = _build_series_points(series, sample_indices, coords)
        paths[key] = SeriesPaths(
            line_path=line_path,
            fill_path=fill_path,
            marker_x=marker_x,
            marker_y=marker_y,
            points=points,
        )

    return paths, chart_axes


def _build_legend(df_index: pd.DataFrame, benchmark_frame: pd.DataFrame | None) -> list[LegendItem]:
    legend_items = []
    total_return_index = df_index["equity"].iloc[-1] / df_index["equity"].iloc[0] - 1
    legend_items.append(
        LegendItem(
            css_modifier="index",
            label="AI Crypto Index",
            delta_text=f"{total_return_index * 100:.1f}% total return",
        )
    )

    if benchmark_frame is not None and not benchmark_frame.empty:
        total_return = benchmark_frame["equity"].iloc[-1] / benchmark_frame["equity"].iloc[0] - 1
        legend_items.append(
            LegendItem(
                css_modifier="benchmark",
                label="BTC+ETH 50/50",
                delta_text=f"{total_return * 100:.1f}% total return",
            )
        )
    return legend_items


def _build_metric_cards(
    metrics_index: dict[str, float],
    metrics_benchmark: dict[str, float],
) -> list[MetricCard]:
    cards: list[MetricCard] = []

    cagr_vs_baseline = (metrics_index["cagr"] - metrics_benchmark["cagr"]) * 100
    cards.append(
        MetricCard(
            label="CAGR",
            badge_text=f"{metrics_benchmark['cagr'] * 100:.1f}% baseline",
            badge_modifier=_modifier_for_delta(cagr_vs_baseline),
            value_text=f"{metrics_index['cagr'] * 100:.1f}%",
            delta_text=_format_delta(cagr_vs_baseline, suffix=" pts vs 50/50"),
            delta_modifier=_modifier_for_delta(cagr_vs_baseline),
            note=(
                "Compound CAGR across "
                f"{metrics_index['years']:.1f} years versus an equal-weight BTC+ETH mix."
            ),
        )
    )

    vol_gap = (metrics_benchmark["vol"] - metrics_index["vol"]) * 100
    cards.append(
        MetricCard(
            label="Volatility",
            badge_text=f"{metrics_benchmark['vol'] * 100:.1f}% baseline",
            badge_modifier=_modifier_for_delta(vol_gap),
            value_text=f"{metrics_index['vol'] * 100:.1f}%",
            delta_text=_format_delta(vol_gap, suffix=" pts tighter vs 50/50"),
            delta_modifier=_modifier_for_delta(vol_gap),
            note=(
                "Dynamic volatility controls aim to dampen swings relative to "
                "the equal-weight benchmark."
            ),
        )
    )

    sharpe_vs_baseline = metrics_index["sharpe"] - metrics_benchmark["sharpe"]
    cards.append(
        MetricCard(
            label="Sharpe",
            badge_text=f"{metrics_benchmark['sharpe']:.2f} baseline",
            badge_modifier=_modifier_for_delta(sharpe_vs_baseline),
            value_text=f"{metrics_index['sharpe']:.2f}",
            delta_text=_format_delta(sharpe_vs_baseline, precision=2, suffix=" vs 50/50"),
            delta_modifier=_modifier_for_delta(sharpe_vs_baseline),
            note=f"Sharpe calculated using a {RISK_FREE_RATE * 100:.1f}% risk-free reference rate.",
        )
    )

    cushion_vs_baseline = (
        abs(metrics_benchmark["max_drawdown"]) - abs(metrics_index["max_drawdown"])
    ) * 100
    cards.append(
        MetricCard(
            label="Max drawdown",
            badge_text=f"{abs(metrics_benchmark['max_drawdown']) * 100:.1f}% baseline",
            badge_modifier=_modifier_for_delta(cushion_vs_baseline),
            value_text=f"{abs(metrics_index['max_drawdown']) * 100:.1f}%",
            delta_text=_format_delta(cushion_vs_baseline, suffix=" pts cushion vs 50/50"),
            delta_modifier=_modifier_for_delta(cushion_vs_baseline),
            note=(
                "Adaptive hedges reduce drawdowns relative to the "
                "equal-weight BTC+ETH benchmark."
            ),
        )
    )

    return cards


def _build_snapshot_for_strategy(
    strategy_key: str,
    config: dict[str, object],
    *,
    runs_root: Path | None,
) -> PerformanceSnapshot:
    data_path = _resolve_index_series_path(
        strategy_key,
        config,
        runs_root=runs_root,
    )
    label = str(config.get("label", strategy_key.title()))
    description = str(config.get("description", ""))

    index_df = _load_index_dataframe(data_path)
    index_df["equity"] = index_df["equity"].astype(float)

    try:
        benchmark_frame = _build_monthly_rebalanced_composite(
            index_df["date"],
            runs_root=runs_root,
        )
    except Exception as exc:  # noqa: BLE001
        raise PerformanceSnapshotError(f"failed to build BTC·ETH benchmark: {exc}") from exc

    index_metrics = _annualized_metrics(
        index_df.set_index("date")["equity"], index_df["log_return"]
    )
    benchmark_metrics = _annualized_metrics(
        benchmark_frame.set_index("date")["equity"],
        benchmark_frame.set_index("date")["log_return"],
    )

    chart_paths, chart_axes = _build_chart_paths(index_df, benchmark_frame)
    legend_items = _build_legend(index_df, benchmark_frame)
    metric_cards = _build_metric_cards(index_metrics, benchmark_metrics)

    start_date = index_df["date"].iloc[0]
    end_date = index_df["date"].iloc[-1]
    period_label = f"{start_date:%Y}-{end_date:%Y}"
    caption = (
        "Each mode is normalised to 1.0 at its start date. "
        "Live = real history after launch; Backtest = historical simulation before Live since."
    )

    return PerformanceSnapshot(
        strategy_key=strategy_key,
        strategy_label=label,
        strategy_description=description,
        chart_period_label=period_label,
        chart_caption=caption,
        chart_paths=chart_paths,
        chart_axes=chart_axes,
        legend=legend_items,
        metric_cards=metric_cards,
    )


@lru_cache(maxsize=1)
def load_performance_bundle(runs_root: Path | None = None) -> PerformanceBundle:
    snapshots: dict[str, PerformanceSnapshot] = {}
    for key, config in INDEX_VARIANTS.items():
        try:
            snapshots[key] = _build_snapshot_for_strategy(
                key,
                config,
                runs_root=runs_root,
            )
        except PerformanceSnapshotError:
            raise
        except Exception as exc:  # noqa: BLE001 - escalate with context
            message = f"failed to prepare snapshot for '{key}': {exc}"
            raise PerformanceSnapshotError(message) from exc

    if not snapshots:
        raise PerformanceSnapshotError("no performance snapshots available")

    default_key = (
        DEFAULT_STRATEGY_KEY
        if DEFAULT_STRATEGY_KEY in snapshots
        else next(iter(snapshots))
    )
    return PerformanceBundle(default_key=default_key, snapshots=snapshots)
