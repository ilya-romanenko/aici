import os

import numpy as np
import pandas as pd


def filter_assets_for_horizon(
    selected_cols,
    weights,
    next_window: pd.DataFrame,
    min_days_ratio: float = 0.7,          # >=70% valid days — admission threshold
    penalize_missing: bool = True,        # whether to penalize weights for gaps in the horizon
    penalty_power: float = 1.0            # penalty exponent (1.0 = linear, >1 stronger)
):
    """
    Selects assets with a sufficient number of valid days in the horizon and
    applies a penalty for missing data to weights before normalisation.

    Returns:
        kept_cols: list of assets admitted to the horizon
        weights_norm: np.array of normalised weights for kept_cols
        report: dict with details for logs (valid-day ratios, penalty, dropped assets)
    """
    if next_window.empty:
        return [], None, {"reason": "empty_next_window"}

    total_days = len(next_window.index)
    if total_days == 0:
        return [], None, {"reason": "zero_days"}

    col2ratio = {}
    kept_cols = []
    dropped_cols = []
    kept_raw_weights = []

    # 1) compute valid-day ratios and filter by threshold
    for c in selected_cols:
        if c not in next_window.columns:
            dropped_cols.append(c)
            continue
        valid_days = int(next_window[c].notna().sum())
        ratio = valid_days / total_days
        col2ratio[c] = ratio
        if valid_days > 0 and ratio >= min_days_ratio:
            kept_cols.append(c)
            kept_raw_weights.append(weights[selected_cols.index(c)])
        else:
            dropped_cols.append(c)

    if not kept_cols:
        return [], None, {
            "reason": "no_assets_after_filter",
            "dropped": dropped_cols,
            "ratios": col2ratio
        }

    kept_raw_weights = np.array(kept_raw_weights, dtype=float)

    # 2) penalty for missing data: reduce weight proportionally to the presence ratio
    if penalize_missing:
        penalties = np.array([col2ratio[c]**penalty_power for c in kept_cols], dtype=float)
        adjusted = kept_raw_weights * penalties
    else:
        adjusted = kept_raw_weights

    # 3) final normalisation
    s = adjusted.sum()
    if s <= 0 or not np.isfinite(s):
        return [], None, {
            "reason": "non_positive_sum_after_penalty",
            "kept": kept_cols,
            "raw_sum": float(kept_raw_weights.sum())
        }
    weights_norm = adjusted / s

    report = {
        "ratios": col2ratio,
        "kept": kept_cols,
        "dropped": dropped_cols,
        "raw_weights": {c: float(kept_raw_weights[i]) for i, c in enumerate(kept_cols)},
        "penalized_weights": {c: float(weights_norm[i]) for i, c in enumerate(kept_cols)},
        "sum_before": float(kept_raw_weights.sum()),
        "sum_after": float(weights_norm.sum()),
        "penalize_missing": penalize_missing,
        "penalty_power": penalty_power,
        "min_days_ratio": min_days_ratio,
        "total_days": total_days
    }
    return kept_cols, weights_norm, report


def arith_returns_with_daily_renorm(
    next_period_log: pd.DataFrame,
    weights_vec: np.ndarray,
    cols: list[str],
):
    """
    Daily return calculation with DAILY re-normalisation of weights
    across the assets that are not NaN on a given day.
    """
    ar = np.exp(next_period_log[cols]) - 1.0
    w = pd.Series(weights_vec, index=cols)

    def combine_row(row):
        mask = row.notna()
        if not mask.any():
            return np.nan
        w_sub = w[mask]
        w_sub = w_sub / w_sub.sum()
        return float((row[mask] * w_sub).sum())

    daily = ar.apply(combine_row, axis=1)
    daily = daily.clip(lower=-0.999999)
    return np.log1p(daily).dropna()


def get_last_processed_date_from_log(log_path):
    if not os.path.exists(log_path):
        return None
    with open(log_path) as f:
        lines = f.readlines()
    rebalance_lines = [line for line in lines if "Rebalancing started" in line]
    if not rebalance_lines:
        return None
    last_line = rebalance_lines[-1]
    date_str = last_line.split("[")[2].split("]")[0]
    return pd.to_datetime(date_str)
