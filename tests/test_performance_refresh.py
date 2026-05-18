from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from ai_crypto_index.shared import performance_refresh
from ai_crypto_index.shared.performance_series_store import resolve_performance_series_write_path


def _settings(runs_root: Path) -> SimpleNamespace:
    return SimpleNamespace(runs_root=runs_root)


def _seed_variant_series(path: Path) -> None:
    pd.DataFrame(
        {
            "date": ["2026-01-30", "2026-01-31"],
            "log_return": [0.01, -0.02],
        }
    ).to_csv(path, index=False)


def _build_price_frame(start: str, end: str) -> pd.DataFrame:
    index = pd.date_range(start=start, end=end, freq="D")
    return pd.DataFrame(
        {
            "BTC": [100.0 + i * 0.1 for i in range(len(index))],
            "ETH": [200.0 + i * 0.2 for i in range(len(index))],
        },
        index=index,
    )


def test_run_variant_uses_extended_reload_window_for_warmup(tmp_path, monkeypatch):
    runs_root = tmp_path / "runs"
    runs_root.mkdir(parents=True, exist_ok=True)
    series_path = resolve_performance_series_write_path(
        runs_root=runs_root,
        filename="AICI_classic.csv",
    )
    _seed_variant_series(series_path)

    prices = _build_price_frame("2025-01-01", "2026-02-17")
    captured: dict[str, date] = {}

    def _fake_simulate_index_over_time(*, df_prices, **kwargs):
        captured["start"] = df_prices.index.min().date()
        captured["end"] = df_prices.index.max().date()
        return pd.Series(dtype=float, index=pd.DatetimeIndex([])), {}, [], []

    monkeypatch.setattr(performance_refresh, "simulate_index_over_time", _fake_simulate_index_over_time)

    result = performance_refresh._run_variant(
        _settings(runs_root),
        "classic",
        performance_refresh.PERFORMANCE_VARIANTS["classic"],
        prices,
        scratch_dir=tmp_path / "scratch",
    )

    required_max_start = captured["end"] - timedelta(
        days=performance_refresh.DEFAULT_LOOKBACK_DAYS + performance_refresh.DEFAULT_FORECAST_HORIZON
    )
    assert captured["start"] <= required_max_start
    assert result.added_rows == 0
    assert result.last_date == date(2026, 1, 31)
    assert "existing series preserved" in (result.error or "")


def test_run_variant_simulation_error_preserves_existing_file(tmp_path, monkeypatch):
    runs_root = tmp_path / "runs"
    runs_root.mkdir(parents=True, exist_ok=True)
    series_path = resolve_performance_series_write_path(
        runs_root=runs_root,
        filename="AICI_classic.csv",
    )
    _seed_variant_series(series_path)
    before_content = series_path.read_text(encoding="utf-8")

    def _fake_simulate_index_over_time(**kwargs):
        raise ValueError("insufficient data for rebalancing")

    monkeypatch.setattr(performance_refresh, "simulate_index_over_time", _fake_simulate_index_over_time)

    result = performance_refresh._run_variant(
        _settings(runs_root),
        "classic",
        performance_refresh.PERFORMANCE_VARIANTS["classic"],
        _build_price_frame("2025-01-01", "2026-02-02"),
        scratch_dir=tmp_path / "scratch",
    )

    assert result.added_rows == 0
    assert result.last_date == date(2026, 1, 31)
    assert result.latest_date == date(2026, 1, 31)
    assert "simulation skipped" in (result.error or "")
    assert series_path.read_text(encoding="utf-8") == before_content


def test_run_variant_seeds_runs_series_from_fallback_when_no_new_rows(tmp_path, monkeypatch):
    runs_root = tmp_path / "runs"
    runs_root.mkdir(parents=True, exist_ok=True)

    fallback_path = tmp_path / "fallback" / "AICI_classic.csv"
    fallback_path.parent.mkdir(parents=True, exist_ok=True)
    _seed_variant_series(fallback_path)

    def _fake_resolve_performance_series_read_path(*, filename, runs_root=None):
        return fallback_path

    def _fake_simulate_index_over_time(*, df_prices, **kwargs):
        return pd.Series(dtype=float, index=pd.DatetimeIndex([])), {}, [], []

    monkeypatch.setattr(
        performance_refresh,
        "resolve_performance_series_read_path",
        _fake_resolve_performance_series_read_path,
    )
    monkeypatch.setattr(performance_refresh, "simulate_index_over_time", _fake_simulate_index_over_time)

    result = performance_refresh._run_variant(
        _settings(runs_root),
        "classic",
        performance_refresh.PERFORMANCE_VARIANTS["classic"],
        _build_price_frame("2025-01-01", "2026-02-02"),
        scratch_dir=tmp_path / "scratch",
    )

    seeded_path = resolve_performance_series_write_path(
        runs_root=runs_root,
        filename="AICI_classic.csv",
    )
    assert seeded_path.exists()
    assert result.added_rows == 0


def test_update_next_run_after_success_moves_due_date_to_future(tmp_path):
    runs_root = tmp_path / "runs"
    settings = _settings(runs_root)
    config = performance_refresh.AutoRunConfig(enabled=True, next_run_date=date(2000, 2, 1))
    report = performance_refresh.PerformanceRefreshReport(
        variants=[],
        total_added=0,
        latest_date=date(2000, 1, 15),
        benchmarks=[],
        benchmark_total_added=0,
    )

    updated = performance_refresh.update_next_run_after_success(settings, config, report)

    assert updated.next_run_date is not None
    assert updated.next_run_date > date.today()
    assert updated.next_run_date.day == 1
