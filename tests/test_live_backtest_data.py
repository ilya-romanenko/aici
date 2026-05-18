from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pandas as pd
import pytest

from ai_crypto_index.shared import live_backtest_data


def _settings(runs_root, *, config_path=None):
    payload = {"runs_root": runs_root}
    if config_path is not None:
        payload["config_path"] = config_path
    return SimpleNamespace(**payload)


def test_build_live_backtest_payload_with_live_history(tmp_path, monkeypatch):
    backtest_path = tmp_path / "AICI_classic.csv"
    pd.DataFrame(
        {
            "date": ["2025-12-30", "2025-12-31", "2026-01-01"],
            "log_return": [0.0, 0.01, 0.02],
        }
    ).to_csv(backtest_path, index=False)

    monkeypatch.setitem(
        live_backtest_data.INDEX_VARIANTS,
        "classic",
        {"label": "Classic", "description": "", "path": backtest_path},
    )

    january_first_run = tmp_path / "runs" / "auto-classic-2026-01-01T16-44-38Z"
    january_first_run.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        {
            "date": ["2026-01-01", "2026-01-02"],
            "equity_curve": [1.0, 1.2],
        }
    ).to_csv(january_first_run / "equity_curve.csv", index=False)
    pd.DataFrame({"asset": ["BTC"], "weight": [1.0]}).to_csv(
        january_first_run / "weights.csv",
        index=False,
    )

    january_late_run = tmp_path / "runs" / "auto-classic-2026-01-20T11-00-00Z"
    january_late_run.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        {
            "date": ["2026-01-20", "2026-01-21"],
            "equity_curve": [1.0, 2.0],
        }
    ).to_csv(january_late_run / "equity_curve.csv", index=False)
    pd.DataFrame({"asset": ["BTC"], "weight": [1.0]}).to_csv(
        january_late_run / "weights.csv",
        index=False,
    )

    february_first_run = tmp_path / "runs" / "auto-classic-2026-02-01T09-33-04Z"
    february_first_run.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        {
            "date": ["2026-02-01", "2026-02-02"],
            "equity_curve": [1.0, 1.1],
        }
    ).to_csv(february_first_run / "equity_curve.csv", index=False)
    pd.DataFrame({"asset": ["BTC"], "weight": [1.0]}).to_csv(
        february_first_run / "weights.csv",
        index=False,
    )

    payload = live_backtest_data.build_live_backtest_payload(
        _settings(tmp_path / "runs"),
        strategy_key="classic",
        live_run_prefix="auto-classic",
        today_utc=date(2026, 2, 21),
    )

    assert payload.backtest_window_start == "2025-12-30"
    assert payload.backtest_window_end == "2025-12-31"
    assert payload.has_live_history is True
    assert payload.live_start_date == "2026-01-01"
    assert len(payload.live_series) == 2
    assert payload.live_series[0].date == "2026-01-01"
    assert payload.live_series[0].value == pytest.approx(payload.backtest_series[-1].value)
    assert payload.live_series[1].value == pytest.approx(payload.live_series[0].value * 1.2)
    assert payload.live_series[-1].date == "2026-01-02"
    assert payload.is_live_series_short is True
    assert payload.calculation_basis.frequency == "1d"
    assert payload.calculation_basis.currency == "USD"
    assert payload.calculation_basis.timestamp_policy == "UTC daily close"


def test_build_live_backtest_payload_keeps_previous_month_when_month_is_closed(
    tmp_path,
    monkeypatch,
):
    backtest_path = tmp_path / "AICI_classic.csv"
    pd.DataFrame(
        {
            "date": ["2025-12-30", "2025-12-31", "2026-01-01"],
            "log_return": [0.0, 0.01, 0.02],
        }
    ).to_csv(backtest_path, index=False)

    monkeypatch.setitem(
        live_backtest_data.INDEX_VARIANTS,
        "classic",
        {"label": "Classic", "description": "", "path": backtest_path},
    )

    january_first_run = tmp_path / "runs" / "auto-classic-2026-01-01T16-44-38Z"
    january_first_run.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        {
            "date": ["2026-01-01", "2026-01-02"],
            "equity_curve": [1.0, 1.2],
        }
    ).to_csv(january_first_run / "equity_curve.csv", index=False)
    pd.DataFrame({"asset": ["BTC"], "weight": [1.0]}).to_csv(
        january_first_run / "weights.csv",
        index=False,
    )

    february_first_run = tmp_path / "runs" / "auto-classic-2026-02-01T09-33-04Z"
    february_first_run.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        {
            "date": ["2026-02-01", "2026-02-02"],
            "equity_curve": [1.0, 1.1],
        }
    ).to_csv(february_first_run / "equity_curve.csv", index=False)
    pd.DataFrame({"asset": ["BTC"], "weight": [1.0]}).to_csv(
        february_first_run / "weights.csv",
        index=False,
    )

    payload = live_backtest_data.build_live_backtest_payload(
        _settings(tmp_path / "runs"),
        strategy_key="classic",
        live_run_prefix="auto-classic",
        today_utc=date(2026, 3, 1),
    )

    assert payload.live_start_date == "2026-01-01"
    assert len(payload.live_series) == 4
    assert payload.live_series[-1].date == "2026-02-02"


def test_build_live_backtest_payload_without_live_history(tmp_path, monkeypatch):
    backtest_path = tmp_path / "AICI_classic.csv"
    pd.DataFrame(
        {
            "date": ["2026-01-01", "2026-01-02"],
            "log_return": [0.0, 0.01],
        }
    ).to_csv(backtest_path, index=False)

    monkeypatch.setitem(
        live_backtest_data.INDEX_VARIANTS,
        "classic",
        {"label": "Classic", "description": "", "path": backtest_path},
    )

    runs_root = tmp_path / "runs"
    runs_root.mkdir(parents=True, exist_ok=True)

    payload = live_backtest_data.build_live_backtest_payload(
        _settings(runs_root),
        strategy_key="classic",
        live_run_prefix="auto-classic",
    )

    assert payload.backtest_window_start == "2026-01-01"
    assert payload.backtest_window_end == "2026-01-02"
    assert payload.has_live_history is False
    assert payload.live_start_date is None
    assert payload.live_series == []


def test_build_live_backtest_payload_accepts_aggressive_alias(tmp_path, monkeypatch):
    backtest_path = tmp_path / "AICI_risky.csv"
    pd.DataFrame(
        {
            "date": ["2026-01-01", "2026-01-02"],
            "log_return": [0.0, 0.01],
        }
    ).to_csv(backtest_path, index=False)

    monkeypatch.setitem(
        live_backtest_data.INDEX_VARIANTS,
        "risky",
        {"label": "Aggressive", "description": "", "path": backtest_path},
    )

    runs_root = tmp_path / "runs"
    runs_root.mkdir(parents=True, exist_ok=True)

    payload = live_backtest_data.build_live_backtest_payload(
        _settings(runs_root),
        strategy_key="aggressive",
        live_run_prefix="auto-aggressive",
    )

    assert payload.backtest_window_start == "2026-01-01"
    assert payload.backtest_window_end == "2026-01-02"
    assert payload.has_live_history is False


def test_build_live_backtest_payload_does_not_fallback_to_other_prefix_runs(
    tmp_path,
    monkeypatch,
):
    backtest_path = tmp_path / "AICI_conservative.csv"
    pd.DataFrame(
        {
            "date": ["2026-01-01", "2026-01-02"],
            "log_return": [0.0, 0.01],
        }
    ).to_csv(backtest_path, index=False)

    monkeypatch.setitem(
        live_backtest_data.INDEX_VARIANTS,
        "conservative",
        {"label": "Conservative", "description": "", "path": backtest_path},
    )

    classic_run = tmp_path / "runs" / "auto-classic-2026-02-01T09-33-04Z"
    classic_run.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        {
            "date": ["2026-02-01", "2026-02-02"],
            "equity_curve": [1.0, 1.2],
        }
    ).to_csv(classic_run / "equity_curve.csv", index=False)
    pd.DataFrame({"asset": ["BTC"], "weight": [1.0]}).to_csv(
        classic_run / "weights.csv",
        index=False,
    )

    payload = live_backtest_data.build_live_backtest_payload(
        _settings(tmp_path / "runs"),
        strategy_key="conservative",
        live_run_prefix="auto-conservative",
    )

    assert payload.backtest_window_start == "2026-01-01"
    assert payload.backtest_window_end == "2026-01-02"
    assert payload.has_live_history is False
    assert payload.live_start_date is None
    assert payload.live_series == []


def test_build_live_backtest_payload_without_live_history_clips_backtest_to_completed_month(
    tmp_path,
    monkeypatch,
):
    backtest_path = tmp_path / "AICI_conservative.csv"
    pd.DataFrame(
        {
            "date": ["2026-02-27", "2026-02-28", "2026-03-01", "2026-03-02"],
            "log_return": [0.0, 0.01, 0.02, -0.01],
        }
    ).to_csv(backtest_path, index=False)

    monkeypatch.setitem(
        live_backtest_data.INDEX_VARIANTS,
        "conservative",
        {"label": "Conservative", "description": "", "path": backtest_path},
    )

    runs_root = tmp_path / "runs"
    runs_root.mkdir(parents=True, exist_ok=True)

    payload = live_backtest_data.build_live_backtest_payload(
        _settings(runs_root),
        strategy_key="conservative",
        live_run_prefix="auto-conservative",
        today_utc=date(2026, 3, 2),
    )

    assert payload.has_live_history is False
    assert payload.live_series == []
    assert payload.backtest_window_end == "2026-02-28"
    assert payload.backtest_series[-1].date == "2026-02-28"


def test_densify_live_frame_prefers_live_values_on_overlap():
    live_frame = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-02-01", "2026-02-02", "2026-02-03"]),
            "equity": [1.0, 1.1, 1.2],
        }
    )
    backtest_full = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-02-01", "2026-02-02", "2026-02-03", "2026-02-04"]),
            "equity": [1.0, 1.5, 2.0, 2.2],
        }
    )

    dense = live_backtest_data._densify_live_frame_with_backtest(
        live_frame,
        backtest_frame_full=backtest_full,
        coverage_end_ts=pd.Timestamp("2026-02-04"),
    )
    dense = dense.set_index("date")["equity"]

    assert dense.loc[pd.Timestamp("2026-02-01")] == pytest.approx(1.0)
    assert dense.loc[pd.Timestamp("2026-02-02")] == pytest.approx(1.1)
    assert dense.loc[pd.Timestamp("2026-02-03")] == pytest.approx(1.2)
    assert dense.loc[pd.Timestamp("2026-02-04")] == pytest.approx(2.2)


def test_build_live_backtest_payload_resolves_month_from_equity_curve_fallback(
    tmp_path,
    monkeypatch,
):
    backtest_path = tmp_path / "AICI_conservative.csv"
    pd.DataFrame(
        {
            "date": ["2026-01-30", "2026-01-31"],
            "log_return": [0.0, 0.01],
        }
    ).to_csv(backtest_path, index=False)

    monkeypatch.setitem(
        live_backtest_data.INDEX_VARIANTS,
        "conservative",
        {"label": "Conservative", "description": "", "path": backtest_path},
    )

    march_first_run = tmp_path / "runs" / "auto-conservative-2026-03-01T03-21-10Z"
    march_first_run.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        {
            "date": ["2026-02-01", "2026-02-02"],
            "equity_curve": [1.0, 1.1],
        }
    ).to_csv(march_first_run / "equity_curve.csv", index=False)
    pd.DataFrame({"asset": ["BTC"], "weight": [1.0]}).to_csv(
        march_first_run / "weights.csv",
        index=False,
    )

    payload = live_backtest_data.build_live_backtest_payload(
        _settings(tmp_path / "runs"),
        strategy_key="conservative",
        live_run_prefix="auto-conservative",
        today_utc=date(2026, 3, 2),
    )

    assert payload.has_live_history is True
    assert payload.live_start_date == "2026-02-01"
    assert payload.live_series[-1].date == "2026-02-02"
    assert payload.backtest_window_end == "2026-01-31"


def test_build_live_backtest_payload_skips_open_month_even_when_price_history_exists(
    tmp_path,
    monkeypatch,
):
    backtest_path = tmp_path / "AICI_conservative.csv"
    pd.DataFrame(
        {
            "date": ["2026-01-30", "2026-01-31"],
            "log_return": [0.0, 0.01],
        }
    ).to_csv(backtest_path, index=False)

    monkeypatch.setitem(
        live_backtest_data.INDEX_VARIANTS,
        "conservative",
        {"label": "Conservative", "description": "", "path": backtest_path},
    )

    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "pipeline.json"
    config_path.write_text("{}", encoding="utf-8")

    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        {"BTC": [100.0, 102.0, 103.0, 104.0]},
        index=pd.to_datetime(["2026-02-28", "2026-03-01", "2026-03-02", "2026-03-03"]),
    ).to_csv(data_dir / "merged_prices.csv")

    march_first_run = tmp_path / "runs" / "auto-conservative-2026-03-01T03-21-10Z"
    march_first_run.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        {
            "date": ["2026-02-01", "2026-02-02"],
            "equity_curve": [1.0, 1.1],
        }
    ).to_csv(march_first_run / "equity_curve.csv", index=False)
    pd.DataFrame({"asset": ["BTC"], "weight": [1.0]}).to_csv(
        march_first_run / "weights.csv",
        index=False,
    )

    payload = live_backtest_data.build_live_backtest_payload(
        _settings(tmp_path / "runs", config_path=config_path),
        strategy_key="conservative",
        live_run_prefix="auto-conservative",
        today_utc=date(2026, 3, 6),
    )

    assert payload.has_live_history is True
    assert payload.live_start_date == "2026-02-01"
    assert payload.live_series[-1].date == "2026-02-02"
    assert payload.backtest_window_end == "2026-01-31"


def test_build_live_backtest_payload_includes_benchmark_series_for_continuous_timeline(
    tmp_path,
    monkeypatch,
):
    backtest_path = tmp_path / "AICI_classic.csv"
    pd.DataFrame(
        {
            "date": ["2026-01-30", "2026-01-31"],
            "log_return": [0.0, 0.01],
        }
    ).to_csv(backtest_path, index=False)

    monkeypatch.setitem(
        live_backtest_data.INDEX_VARIANTS,
        "classic",
        {"label": "Classic", "description": "", "path": backtest_path},
    )

    february_run = tmp_path / "runs" / "auto-classic-2026-02-01T09-33-04Z"
    february_run.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        {
            "date": ["2026-02-01", "2026-02-02"],
            "equity_curve": [1.0, 1.1],
        }
    ).to_csv(february_run / "equity_curve.csv", index=False)
    pd.DataFrame({"asset": ["BTC"], "weight": [1.0]}).to_csv(
        february_run / "weights.csv",
        index=False,
    )

    benchmark_root = tmp_path / "runs" / "_performance" / "series"
    benchmark_root.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        {
            "date": ["2026-01-30", "2026-01-31", "2026-02-01", "2026-02-02"],
            "Close": [100.0, 102.0, 103.0, 104.0],
        }
    ).to_csv(benchmark_root / "BTC_USD.csv", index=False)
    pd.DataFrame(
        {
            "date": ["2026-01-30", "2026-01-31", "2026-02-01", "2026-02-02"],
            "Close": [200.0, 198.0, 199.0, 201.0],
        }
    ).to_csv(benchmark_root / "ETH_USD.csv", index=False)

    payload = live_backtest_data.build_live_backtest_payload(
        _settings(tmp_path / "runs"),
        strategy_key="classic",
        live_run_prefix="auto-classic",
        today_utc=date(2026, 3, 2),
    )

    continuous_dates = [point.date for point in payload.backtest_series] + [
        point.date for point in payload.live_series
    ]
    benchmark_dates = [point.date for point in payload.benchmark_series]
    benchmark_values = [point.value for point in payload.benchmark_series]

    assert benchmark_dates == continuous_dates
    assert benchmark_values[0] == pytest.approx(1.0)
    assert all(value > 0 for value in benchmark_values)
