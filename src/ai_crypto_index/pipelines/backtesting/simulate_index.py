import logging
import os

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from tqdm import tqdm

from ai_crypto_index.optimization.evaluate_portfolio_performance import (
    evaluate_portfolio_performance,
)
from ai_crypto_index.pipelines.backtesting.helpers import (
    arith_returns_with_daily_renorm,
    filter_assets_for_horizon,
    get_last_processed_date_from_log,
)
from ai_crypto_index.pipelines.main import compute_monthly_weights_for_df

STRATEGY_PRESETS = {
    "balanced": {
        "total_assets": 10,
        "risk_min_weight": 0.03,
        "risk_max_weight": 0.25,
        "weight_cap": 0.15,
        "vol_floor_ratio": 0.4,
        "gating_tolerance": 0.02,
    },
    "conservative": {
        "total_assets": 12,
        "risk_min_weight": 0.02,
        "risk_max_weight": 0.18,
        "weight_cap": 0.12,
        "vol_floor_ratio": 0.5,
        "gating_tolerance": 0.015,
    },
    "aggressive": {
        "total_assets": 8,
        "risk_min_weight": 0.01,
        "risk_max_weight": 0.35,
        "weight_cap": 0.25,
        "vol_floor_ratio": 0.3,
        "gating_tolerance": 0.03,
    },
}


def simulate_index_over_time(
    df_prices,
    lookback_days=180,       # Instead of a fixed "all available data", we take a 180-day window
    window_size=30,          # LSTM window size
    forecast_horizon=30,     # How many days ahead to forecast and how long to hold weights
    retrain_every='ME',       # Rebalancing frequency (e.g. 'ME' = monthly)
    save_dir="results",
    log_path="simulation.log",
    resume=False, 
    min_days_ratio=0.7,          # asset is accepted if it has >=70% valid days in the horizon
    penalize_missing=True,       # penalize weights for missing data
    penalty_power=1.0,
    strategy=None,
    strategy_overrides=None,
    end_date=None,
):
    """
    Main function for simulating index updates in rolling-window mode:
      - df_prices — DataFrame with prices, where the index contains dates and columns are assets.
      - lookback_days — how many past days to use for clustering and training.
      - window_size — LSTM window size (number of history points per training epoch).
      - forecast_horizon — how many days to hold weights after each rebalancing.
    """
    # === Logging setup ===
    os.makedirs(save_dir, exist_ok=True)
    logging.basicConfig(
        filename=os.path.join(save_dir, log_path),
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    logging.info("Simulation started")

    df_prices = df_prices.sort_index()
    if df_prices.empty:
        raise ValueError("df_prices must contain at least one row for simulation.")
    strategy_kwargs = {}
    strategy_key = None
    if strategy:
        strategy_key = str(strategy).lower()
        if strategy_key not in STRATEGY_PRESETS:
            available = ", ".join(sorted(STRATEGY_PRESETS))
            raise ValueError(f"Unknown strategy '{strategy}'. Available presets: {available}.")
        strategy_kwargs.update(STRATEGY_PRESETS[strategy_key])
    if strategy_overrides:
        if not isinstance(strategy_overrides, dict):
            raise TypeError(
                "strategy_overrides must be a dict of keyword arguments for "
                "compute_monthly_weights_for_df."
            )
        strategy_kwargs.update(strategy_overrides)
    reserved_keys = {
        "df_prices",
        "lookback_days",
        "window_size",
        "forecast_horizon",
        "info_messages",
        "visualization",
    }
    conflict_keys = reserved_keys.intersection(strategy_kwargs.keys())
    if conflict_keys:
        conflicts = ", ".join(sorted(conflict_keys))
        raise ValueError(f"strategy parameters override reserved arguments: {conflicts}.")
    if strategy_key:
        logging.info(f"Strategy preset '{strategy_key}' active with parameters: {strategy_kwargs}")
    elif strategy_kwargs:
        logging.info(f"Custom strategy overrides applied: {strategy_kwargs}")

    if end_date is not None:
        end_ts = pd.to_datetime(end_date)
        if pd.isna(end_ts):
            raise ValueError("end_date must be convertible to a valid pandas Timestamp.")
        min_available = df_prices.index.min()
        max_available = df_prices.index.max()
        if end_ts < min_available:
            message = (
                "Requested end_date "
                f"{end_ts.date()} precedes available history starting "
                f"{min_available.date()}."
            )
            raise ValueError(message)
        if end_ts > max_available:
            logging.warning(
                (
                    "Requested end_date %s exceeds available data (%s); "
                    "clipping to dataset maximum."
                ),
                end_ts.date(),
                max_available.date(),
            )
            end_ts = max_available
        if end_ts < max_available:
            logging.info(f"Truncating price history to end_date={end_ts.date()}.")
        df_prices = df_prices.loc[:end_ts]
        if df_prices.empty:
            raise ValueError("No price data left after applying the requested end_date.")

    effective_end_ts = df_prices.index.max()
    logging.info(f"Effective simulation end date: {effective_end_ts.date()}")

    for p in ["checkpoint_equity_curve.csv", "checkpoint_weights.csv"]:
        f = os.path.join(save_dir, p)
        if os.path.exists(f) and os.path.getsize(f) == 0:
            os.remove(f)
            logging.warning(f"Removed empty checkpoint file: {p}")

    equity_curve = []      # Daily portfolio log-returns will be accumulated here
    equity_dates = []      # Dates corresponding to those returns
    index_weights = []     # Saved weights for each rebalancing
    selected_assets_log = []  # Which assets were selected at each rebalancing

    # Resume: load checkpoint equity curve
    if resume:
        ckpt_path = os.path.join(save_dir, "checkpoint_equity_curve.csv")
        if os.path.exists(ckpt_path):
            ckpt_series = pd.read_csv(ckpt_path, index_col=0, parse_dates=True).squeeze()
            equity_curve = ckpt_series.values.tolist()
            equity_dates = ckpt_series.index.tolist()
            logging.info(f"Loaded {len(equity_curve)} log-returns from checkpoint.")

        weights_ckpt_path = os.path.join(save_dir, "checkpoint_weights.csv")
        if os.path.exists(weights_ckpt_path):
            weights_df = pd.read_csv(weights_ckpt_path, index_col=0)
            index_weights = weights_df.to_dict(orient='records')
            selected_assets_log = [
                (d, list(w.keys()))
                for d, w in zip(weights_df.index, index_weights)
            ]
            logging.info(f"Loaded {len(index_weights)} previous weights from checkpoint.")

    # --- log-returns ---
    df_log = np.log(df_prices / df_prices.shift(1))
    df_log = df_log.replace([np.inf, -np.inf], np.nan)
    df_log = df_log.dropna(how="all")  # keep rows that have at least one valid value

    if df_log.empty:
        raise ValueError(
            "df_log is empty after dropna(how='all'). Check df_prices: "
            "possibly all rows are fully NaN (no overlapping dates)."
        )

    # --- rebalancing boundaries ---
    start_date = df_log.index.min() + pd.Timedelta(days=lookback_days)
    end_date   = df_log.index.max() - pd.Timedelta(days=forecast_horizon)

    if start_date >= end_date:
        raise ValueError(
            f"Not enough data: start={start_date.date()} >= end={end_date.date()}. "
            "Reduce lookback_days/forecast_horizon or extend the price period."
        )

    raw_rebalancing_dates = pd.date_range(start=start_date, end=end_date, freq=retrain_every)
    rebalancing_dates = raw_rebalancing_dates + pd.Timedelta(days=1)

    if not resume:
        logging.info(f"Total rebalancing dates: {len(rebalancing_dates)}")

    if resume:
        last_date = get_last_processed_date_from_log(os.path.join(save_dir, log_path))
        if last_date:
            rebalancing_dates = [d for d in rebalancing_dates if d > last_date]
            logging.info(f"Resuming simulation from {last_date + pd.Timedelta(days=1)}")

    for rebalance_date in tqdm(rebalancing_dates, desc="Rebalancing Progress"):
        try:
            logging.info(f"[{rebalance_date.date()}] Rebalancing started")

            # Take a rolling window of the last lookback_days
            start_lookback = rebalance_date - pd.Timedelta(days=lookback_days)
            past_window = df_log.loc[start_lookback:rebalance_date]
            if past_window.dropna(how="all").shape[0] < max(60, window_size + 5):
                logging.warning(f"[{rebalance_date.date()}] Not enough usable rows in past_window.")
                continue

            # --- BEGIN: unified pipeline call from main ---

            # IMPORTANT: the helper expects PRICES, not log-returns.
            # Therefore we take past_prices from df_prices.
            start_lookback = rebalance_date - pd.Timedelta(days=lookback_days)
            past_prices = df_prices.loc[start_lookback:rebalance_date]

            # Obtain weights using exactly the same pipeline as in main.py
            weights_dict, _, _ = compute_monthly_weights_for_df(
                df_prices=past_prices,
                lookback_days=lookback_days,
                window_size=window_size,
                forecast_horizon=forecast_horizon,
                info_messages=False,
                visualization=False,
                **strategy_kwargs,
            )

            # Convert to a weight array and fix the column order
            selected_cols = list(weights_dict.keys())
            weights = np.array([weights_dict[c] for c in selected_cols], dtype=float)

            # Horizon eligibility is determined from historical data only.
            # Use only historical observations available at rebalance time.
            horizon_filter_window = (
                df_log.loc[:rebalance_date]
                .reindex(columns=selected_cols)
                .tail(forecast_horizon)
            )
            if horizon_filter_window.dropna(how="all").empty:
                logging.warning(
                    "[%s] Historical horizon filter window is empty (all-NaN).",
                    rebalance_date.date(),
                )
                continue

            selected_cols_filt, weights_filt, rpt = filter_assets_for_horizon(
                selected_cols=selected_cols,
                weights=weights,
                next_window=horizon_filter_window,
                min_days_ratio=min_days_ratio,
                penalize_missing=penalize_missing,
                penalty_power=penalty_power
            )

            if not selected_cols_filt or weights_filt is None:
                reason = rpt.get("reason", "unknown")
                logging.warning(
                    "[%s] No assets after horizon filter. Reason: %s",
                    rebalance_date.date(),
                    reason,
                )
                if "dropped" in rpt:
                    logging.info(
                        "[%s] Dropped: %s",
                        rebalance_date.date(),
                        rpt["dropped"],
                    )
                continue

            # Log drops and final composition
            if rpt.get("dropped"):
                logging.info(
                    "[%s] Dropped (insufficient data): %s",
                    rebalance_date.date(),
                    sorted(rpt["dropped"]),
                )

            logging.info(
                "[%s] Kept: %s | min_days_ratio=%s, penalize=%s, power=%s",
                rebalance_date.date(),
                selected_cols_filt,
                min_days_ratio,
                penalize_missing,
                penalty_power,
            )

            # ------ SMART PENALTY LOGGING ------
            # Show the report only if penalties were actually applied and there were gaps
            if penalize_missing and penalty_power > 0:
                # penalty candidates — those whose ratio < 1.0
                penalized_assets = [
                    c
                    for c in selected_cols_filt
                    if rpt["ratios"].get(c, 1.0) < 0.9999
                ]

                if penalized_assets:
                    # normalise raw weights for a fair comparison with penalized_weights
                    raw_sum = sum(rpt["raw_weights"][c] for c in selected_cols_filt)
                    if raw_sum > 0:
                        # collect only assets whose weight actually changed (noticeable delta)
                        lines = []
                        for c in penalized_assets:
                            w_raw_norm = rpt["raw_weights"][c] / raw_sum
                            w_pen = rpt["penalized_weights"][c]
                            delta = w_pen - w_raw_norm
                            if abs(delta) > 1e-6:  # show only "real" changes
                                lines.append(
                                    f"{c}: ratio={rpt['ratios'][c]:.2f} | "
                                    f"w_raw→norm={w_raw_norm:.4f} → "
                                    f"w_pen={w_pen:.4f} (Δ={delta:+.4f})"
                                )

                        if lines:  # if any entries remain after filtering
                            logging.info(f"[{rebalance_date.date()}] Penalty report:")
                            for line in lines:
                                logging.info("  • " + line)


            next_window = df_log.loc[
                rebalance_date + pd.Timedelta(days=1):
                rebalance_date + pd.Timedelta(days=forecast_horizon)
            ]
            if next_window.dropna(how="all").empty:
                logging.warning(f"[{rebalance_date.date()}] Future window is empty (all-NaN).")
                continue

            daily_portfolio_log = arith_returns_with_daily_renorm(
                next_period_log=next_window,
                weights_vec=weights_filt,
                cols=selected_cols_filt
            )

            if daily_portfolio_log.empty:
                logging.warning(
                    "[%s] No valid daily returns after per-day renorm.",
                    rebalance_date.date(),
                )
                continue

            equity_curve.extend(daily_portfolio_log.values)
            equity_dates.extend(daily_portfolio_log.index)

            index_weights.append(dict(zip(selected_cols_filt, weights_filt)))
            selected_assets_log.append((rebalance_date.strftime("%Y-%m-%d"), selected_cols_filt))

            # --- END: unified pipeline call from main ---
        
            # Append to checkpoint_equity_curve.csv without overwriting
            eq_path = os.path.join(save_dir, "checkpoint_equity_curve.csv")
            if os.path.exists(eq_path) and os.path.getsize(eq_path) > 0:
                try:
                    existing_series = pd.read_csv(
                        eq_path,
                        index_col=0,
                        parse_dates=True,
                    ).squeeze("columns")
                    # if not a Series — convert it
                    if not isinstance(existing_series, pd.Series):
                        existing_series = existing_series.iloc[:, 0]
                except pd.errors.EmptyDataError:
                    existing_series = None
            else:
                existing_series = None

            new_series = pd.Series(
                daily_portfolio_log.values,
                index=daily_portfolio_log.index,
                name="log_return",
            )

            if existing_series is not None and not existing_series.empty:
                combined_series = pd.concat([existing_series, new_series])
                combined_series = combined_series[
                    ~combined_series.index.duplicated(keep="last")
                ].sort_index()
            else:
                combined_series = new_series

            combined_series.to_csv(eq_path, header=True)

            # Append to checkpoint_weights.csv without overwriting
            weights_path = os.path.join(save_dir, "checkpoint_weights.csv")
            weights_df_new = pd.DataFrame(
                index=[rebalance_date.strftime("%Y-%m-%d")],
                data=[dict(zip(selected_cols_filt, weights_filt))],
            )

            if os.path.exists(weights_path) and os.path.getsize(weights_path) > 0:
                try:
                    weights_df_existing = pd.read_csv(weights_path, index_col=0)
                except pd.errors.EmptyDataError:
                    weights_df_existing = None
            else:
                weights_df_existing = None

            if weights_df_existing is not None and not weights_df_existing.empty:
                combined_weights_df = pd.concat([weights_df_existing, weights_df_new])
                combined_weights_df = combined_weights_df[
                    ~combined_weights_df.index.duplicated(keep="last")
                ].sort_index()
            else:
                combined_weights_df = weights_df_new

            combined_weights_df.to_csv(weights_path)

        except Exception as e:
            logging.exception(f"Error during rebalancing on {rebalance_date.date()}: {e}")
            continue


    # === Final results ===
    equity_series = pd.Series(equity_curve, index=equity_dates).sort_index()
    capital = np.exp(equity_series.cumsum())

    # Save to CSV
    equity_series.to_csv(
        os.path.join(save_dir, "equity_curve.csv"), header=["log_return"]
    )
    weights_df = pd.DataFrame(
        index=[date for date, _ in selected_assets_log],
        data=[w for w in index_weights]
    )
    weights_df.to_csv(os.path.join(save_dir, "monthly_weights.csv"))

    # Plot the capital curve
    plt.figure(figsize=(12, 6))
    plt.plot(capital, label="Equity Curve")
    plt.title("AI Crypto Index Equity Curve")
    plt.xlabel("Date")
    plt.ylabel("Capital")
    plt.grid(True)
    plt.legend()
    plt.tight_layout()
    plt.savefig(os.path.join(save_dir, "equity_curve_plot.png"))
    plt.close()

    # Evaluate the final index
    metrics = evaluate_portfolio_performance(pd.DataFrame({"Index": equity_series}), [1.0])
    logging.info("Simulation completed successfully.")
    return equity_series, metrics, index_weights, selected_assets_log

if __name__ == '__main__':

    df_prices = pd.read_csv("data/merged_prices.csv", index_col=0, parse_dates=True)
    equity, metrics, weights, assets = simulate_index_over_time(
        df_prices,
        resume=False,
        end_date="2025-09-01",
        strategy="conservative",
    )

    print("=== Final Performance ===")
    for k, v in metrics.items():
        print(f"{k}: {v:.2f}")
