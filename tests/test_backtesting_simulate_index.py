from __future__ import annotations

import numpy as np
import pandas as pd

from ai_crypto_index.pipelines.backtesting import simulate_index


def _build_prices(start: str, end: str) -> pd.DataFrame:
    index = pd.date_range(start=start, end=end, freq="D")
    trend = np.linspace(0.0, 1.0, num=len(index))
    return pd.DataFrame(
        {
            "BTC": 100.0 + trend * 5.0,
            "ETH": 200.0 + trend * 8.0,
        },
        index=index,
    )


def test_horizon_filter_uses_only_rebalance_date_history(tmp_path, monkeypatch):
    prices = _build_prices("2025-01-01", "2025-09-30")
    state: dict[str, object] = {
        "last_rebalance_date": None,
        "violations": [],
        "filter_calls": 0,
    }

    def _fake_compute_monthly_weights_for_df(*, df_prices, **kwargs):
        state["last_rebalance_date"] = df_prices.index.max()
        return {"BTC": 0.6, "ETH": 0.4}, {}, {}

    def _fake_filter_assets_for_horizon(*, selected_cols, weights, next_window, **kwargs):
        state["filter_calls"] = int(state["filter_calls"]) + 1
        rebalance_date = state["last_rebalance_date"]
        if rebalance_date is None:
            raise AssertionError("rebalance date must be captured before horizon filter call")
        if not next_window.empty and next_window.index.max() > rebalance_date:
            violations = state["violations"]
            assert isinstance(violations, list)
            violations.append((rebalance_date, next_window.index.max()))
        weights_arr = np.asarray(weights, dtype=float)
        weights_arr = weights_arr / weights_arr.sum()
        return list(selected_cols), weights_arr, {
            "ratios": {asset: 1.0 for asset in selected_cols},
            "dropped": [],
            "raw_weights": {asset: float(weights_arr[i]) for i, asset in enumerate(selected_cols)},
            "penalized_weights": {
                asset: float(weights_arr[i]) for i, asset in enumerate(selected_cols)
            },
        }

    def _fake_arith_returns_with_daily_renorm(*, next_period_log, weights_vec, cols):
        del weights_vec, cols
        if next_period_log.empty:
            return pd.Series(dtype=float)
        return pd.Series(np.zeros(len(next_period_log), dtype=float), index=next_period_log.index)

    monkeypatch.setattr(
        simulate_index,
        "compute_monthly_weights_for_df",
        _fake_compute_monthly_weights_for_df,
    )
    monkeypatch.setattr(
        simulate_index,
        "filter_assets_for_horizon",
        _fake_filter_assets_for_horizon,
    )
    monkeypatch.setattr(
        simulate_index,
        "arith_returns_with_daily_renorm",
        _fake_arith_returns_with_daily_renorm,
    )

    equity, _metrics, _weights, _assets = simulate_index.simulate_index_over_time(
        df_prices=prices,
        lookback_days=90,
        window_size=30,
        forecast_horizon=30,
        retrain_every="ME",
        save_dir=str(tmp_path),
        log_path="simulation.log",
        penalize_missing=False,
        strategy_overrides={},
    )

    assert int(state["filter_calls"]) > 0
    assert state["violations"] == []
    assert not equity.empty
