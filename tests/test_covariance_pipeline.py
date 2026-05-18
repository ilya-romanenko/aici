import os

import numpy as np
import pandas as pd
import pytest

pytestmark = pytest.mark.skip(
    reason="Manual covariance benchmark only; needs local price CSV and is too slow/unstable for CI."
)

# ======================
# Helpers: data & metrics
# ======================

def load_prices_from_env():
    csv_path = os.getenv("AI_CI_PRICES_CSV", "data/merged_prices.csv")
    if not csv_path or not os.path.exists(csv_path):
        pytest.skip("Set AI_CI_PRICES_CSV to a prices CSV (Date,index; columns=assets).")
    df = pd.read_csv(csv_path)

    # Date column
    date_col = "Date" if "Date" in df.columns else "date"
    if date_col not in df.columns:
        pytest.skip("CSV must have a 'Date' column.")

    df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
    if df[date_col].isna().any():
        pytest.skip("Some dates failed to parse. Check the Date column format.")

    df = df.set_index(date_col)
    # IMPORTANT: correct sort (no sortindex)
    df = df.sort_index()

    # Numeric columns only (prices), index remains DatetimeIndex
    df_num = df.select_dtypes(include=[np.number]).dropna(how="all", axis=1)

    # Basic thresholds
    if df_num.shape[1] < 5 or df_num.shape[0] < 300:
        pytest.skip(
            "Need at least 5 assets and ~300 rows for a meaningful test. "
            f"Got shape={df_num.shape}."
        )

    # Guarantee the index is DatetimeIndex
    if not isinstance(df_num.index, pd.DatetimeIndex):
        pytest.skip("Index must be DatetimeIndex after parsing.")
    return df_num


def to_log_returns(df_prices: pd.DataFrame) -> pd.DataFrame:
    return np.log(df_prices / df_prices.shift(1)).dropna(how="any")

def realized_vol(returns: pd.Series) -> float:
    # std over the period (not annualized)
    return float(returns.std(ddof=0))

def sharpe(returns: pd.Series) -> float:
    mu = returns.mean()
    sd = returns.std(ddof=0) + 1e-12
    return float(mu / sd)

def monthly_rebalance_dates(index: pd.DatetimeIndex, day: int = 1) -> list:
    # Cast to DatetimeIndex just in case
    if not isinstance(index, pd.DatetimeIndex):
        index = pd.to_datetime(index, errors="coerce")
        # If still not DatetimeIndex, raise a clear error
        if not isinstance(index, pd.DatetimeIndex):
            raise AssertionError("monthly_rebalance_dates expects a DatetimeIndex.")

    index = index.sort_values()
    months = sorted(set((d.year, d.month) for d in index))
    dates = []
    for y, m in months:
        target = pd.Timestamp(year=y, month=m, day=day)
        month_end = (target + pd.offsets.MonthEnd(1))
        # take the first trading day of the month >= target
        candidates = index[(index >= target) & (index < month_end)]
        if len(candidates) == 0:
            continue
        dates.append(candidates[0])
    return dates[1:]  # skip the first month


# ======================
# Estimators (ρ̂, Σ̂)
# ======================

def ewma_cov(returns: pd.DataFrame, lam: float = 0.97) -> pd.DataFrame:
    R = returns.to_numpy()
    n = R.shape[1]
    S = np.eye(n) * 1e-8
    mu = np.zeros(n)
    for r in R:
        x = r - mu
        S = lam * S + (1 - lam) * np.outer(x, x)
    return pd.DataFrame(S, index=returns.columns, columns=returns.columns)

def corr_from_cov(S: pd.DataFrame) -> pd.DataFrame:
    d = np.sqrt(np.diag(S))
    R = S.values / np.outer(d, d)
    R = np.nan_to_num(R, nan=0.0, posinf=0.0, neginf=0.0)
    R = np.clip(R, -0.99, 0.99)
    return pd.DataFrame(R, index=S.index, columns=S.columns)

def shrink_to_diag(S: pd.DataFrame, gamma: float = 0.1) -> pd.DataFrame:
    # gamma ∈ [0,1]: 0 — no shrinkage, 1 — pure diagonal
    D = np.diag(np.diag(S.values))
    S_shr = (1 - gamma) * S.values + gamma * D
    return pd.DataFrame(S_shr, index=S.index, columns=S.columns)

# ======================
# Risk-parity (simple iterative solver)
# ======================

def risk_parity_weights(Sigma: pd.DataFrame, min_w=0.0, max_w=1.0, iters=500, tol=1e-8):
    n = Sigma.shape[0]
    w = np.ones(n) / n
    S = Sigma.values
    for _ in range(iters):
        m = S @ w                    # marginal risk
        port_var = w @ m
        if port_var <= 0:
            break
        target = port_var / n
        rc = w * m
        # multiplicative update
        w_new = w * (target / (rc + 1e-16))
        # clip & renorm
        w_new = np.clip(w_new, min_w, max_w)
        s = w_new.sum()
        w_new = (np.ones(n) / n) if s == 0 else (w_new / s)
        if np.linalg.norm(w_new - w, ord=1) < tol:
            w = w_new
            break
        w = w_new
    return pd.Series(w, index=Sigma.index)

# ======================
# OLD vs NEW covariance builds for the test
# ======================

def build_sigma_old(df_log_train: pd.DataFrame, H: int = 21) -> pd.DataFrame:
    """
    OLD: covariance over |r| on the last H days (imitation of the old approach).
    This is NOT a covariance of returns, but kept as a baseline for comparison.
    """
    abs_tail = df_log_train.iloc[-H:].abs()
    Sigma_old = abs_tail.cov()
    return Sigma_old

def _sigma_last(df_log_train: pd.DataFrame, H: int, sigma_mode: str) -> pd.Series:
    if sigma_mode == "rolling":
        L = H
        return df_log_train.rolling(L).std().iloc[-1]
    elif sigma_mode == "ewm":
        L = 2 * H
        alpha = 2 / (L + 1)
        return df_log_train.ewm(alpha=alpha, adjust=False).std().iloc[-1]
    else:
        raise ValueError(f"Unknown sigma_mode={sigma_mode}")

def _rho_matrix(
    df_log_train: pd.DataFrame,
    corr_mode: str,
    lam: float,
    gamma: float,
) -> pd.DataFrame:
    if corr_mode == "ewma":
        S = ewma_cov(df_log_train, lam=lam)
        return corr_from_cov(S)
    elif corr_mode == "shrink":
        S = df_log_train.cov()
        S_shr = shrink_to_diag(S, gamma=gamma)
        return corr_from_cov(S_shr)
    else:
        raise ValueError(f"Unknown corr_mode={corr_mode}")

def build_sigma_new(df_log_train: pd.DataFrame,
                    H: int = 21,
                    sigma_mode: str = "rolling",   # or "ewm"
                    corr_mode: str = "ewma",       # or "shrink"
                    lam: float = 0.97,
                    gamma: float = 0.1) -> pd.DataFrame:
    """
    Sigma = D · rho · D, where D = diag( sqrt(V) ), V_i = H * sigma_last_i^2
      sigma_last: rolling(H) or ewm(2H)
      rho: EWMA(lambda) or shrinked sample (gamma)
    """
    sigma_last = _sigma_last(df_log_train, H=H, sigma_mode=sigma_mode)       # (N,)
    V = H * (sigma_last.values ** 2)                                         # (N,)
    Rho = _rho_matrix(df_log_train, corr_mode=corr_mode, lam=lam, gamma=gamma)

    D = np.diag(np.sqrt(np.maximum(V, 1e-16)))
    Sigma_new = pd.DataFrame(D @ Rho.values @ D,
                             index=df_log_train.columns,
                             columns=df_log_train.columns)
    # small diagonal regularization
    Sigma_new.values[np.diag_indices_from(Sigma_new)] += 1e-8
    return Sigma_new

# ======================
# Backtest
# ======================

def hold_period_returns(
    df_log_all: pd.DataFrame,
    start: pd.Timestamp,
    end: pd.Timestamp,
    w: pd.Series,
) -> pd.Series:
    rets = df_log_all.loc[(df_log_all.index > start) & (df_log_all.index <= end)]
    port_rets = rets @ w.reindex(rets.columns).fillna(0.0)
    return port_rets

def _evaluate_pipeline(df_log: pd.DataFrame,
                       lookback: int,
                       horizon: int,
                       min_w: float,
                       max_w: float,
                       sigma_mode: str,
                       corr_mode: str,
                       lam: float,
                       gamma: float):
    dates = monthly_rebalance_dates(df_log.index)
    vols, sharpes = [], []
    for i in range(2, len(dates) - 1):
        t0, t1 = dates[i], dates[i + 1]
        train = df_log.loc[:t0].dropna(how="any")
        if len(train) < (lookback + horizon):
            continue
        train = train.iloc[-lookback:]
        train = train.dropna(how="any", axis=1)
        if train.shape[1] < 5:
            continue

        Sigma = build_sigma_new(train,
                                H=horizon,
                                sigma_mode=sigma_mode,
                                corr_mode=corr_mode,
                                lam=lam,
                                gamma=gamma)
        w = risk_parity_weights(Sigma, min_w=min_w, max_w=max_w)
        port = hold_period_returns(df_log[Sigma.columns], t0, t1, w)
        if len(port) >= 5:
            vols.append(realized_vol(port))
            sharpes.append(sharpe(port))
    return vols, sharpes

def test_new_covariance_pipeline_parameter_sweep_vs_old():
    df_prices = load_prices_from_env()
    df_log = to_log_returns(df_prices)

    # backtest parameters
    lookback = 180
    horizon = 21
    min_w, max_w = 0.0, 0.2
    tolerance = 0.02  # 2% tolerance

    # OLD baseline
    dates = monthly_rebalance_dates(df_log.index)
    vols_old, sharpes_old = [], []
    for i in range(2, len(dates) - 1):
        t0, t1 = dates[i], dates[i + 1]
        train = df_log.loc[:t0].dropna(how="any")
        if len(train) < (lookback + horizon):
            continue
        train = train.iloc[-lookback:]
        train = train.dropna(how="any", axis=1)
        if train.shape[1] < 5:
            continue

        Sigma_old = build_sigma_old(train, H=horizon)
        cols = Sigma_old.columns
        w_old = risk_parity_weights(Sigma_old, min_w=min_w, max_w=max_w)
        port_old = hold_period_returns(df_log[cols], t0, t1, w_old)
        if len(port_old) >= 5:
            vols_old.append(realized_vol(port_old))
            sharpes_old.append(sharpe(port_old))

    assert len(vols_old) >= 4, "Not enough periods collected for OLD baseline."
    mean_vol_old = float(np.mean(vols_old))
    mean_sh_old = float(np.mean(sharpes_old))

    # NEW grid
    configs = [
        {"sigma_mode": "rolling", "corr_mode": "ewma",   "lam": 0.94, "gamma": 0.1},
        {"sigma_mode": "rolling", "corr_mode": "ewma",   "lam": 0.97, "gamma": 0.1},
        {"sigma_mode": "ewm",     "corr_mode": "ewma",   "lam": 0.94, "gamma": 0.1},
        {"sigma_mode": "ewm",     "corr_mode": "ewma",   "lam": 0.97, "gamma": 0.1},
        {"sigma_mode": "rolling", "corr_mode": "shrink", "lam": 0.97, "gamma": 0.05},
        {"sigma_mode": "rolling", "corr_mode": "shrink", "lam": 0.97, "gamma": 0.1},
        {"sigma_mode": "ewm",     "corr_mode": "shrink", "lam": 0.97, "gamma": 0.05},
        {"sigma_mode": "ewm",     "corr_mode": "shrink", "lam": 0.97, "gamma": 0.1},
    ]

    rows = []
    improved_any = False
    for cfg in configs:
        vols_new, sharpes_new = _evaluate_pipeline(
            df_log=df_log,
            lookback=lookback,
            horizon=horizon,
            min_w=min_w,
            max_w=max_w,
            sigma_mode=cfg["sigma_mode"],
            corr_mode=cfg["corr_mode"],
            lam=cfg["lam"],
            gamma=cfg["gamma"],
        )
        if len(vols_new) < 4:
            rows.append((cfg, None, None, "SKIPPED"))
            continue
        mean_vol_new = float(np.mean(vols_new))
        mean_sh_new = float(np.mean(sharpes_new))
        impr_vol = (mean_vol_old - mean_vol_new) / (mean_vol_old + 1e-12)
        impr_sh = (mean_sh_new - mean_sh_old) / (abs(mean_sh_old) + 1e-12)

        rows.append(
            (
                cfg,
                mean_vol_new,
                mean_sh_new,
                f"Δvol={impr_vol*100:.2f}%, ΔSharpe={impr_sh*100:.2f}%",
            )
        )

        if mean_vol_new <= mean_vol_old * (1 + tolerance):
            improved_any = True

    # Print report
    print("\n=== OLD baseline ===")
    print(f"OLD mean realized vol: {mean_vol_old:.6f} | OLD mean Sharpe: {mean_sh_old:.6f}")
    print("\n=== NEW configurations ===")
    for cfg, mv, ms, note in rows:
        label = (
            f"sigma={cfg['sigma_mode']}, corr={cfg['corr_mode']}, "
            f"lam={cfg['lam']}, gamma={cfg['gamma']}"
        )
        if mv is None:
            print(f"{label:<55} -> {note}")
        else:
            print(f"{label:<55} -> vol={mv:.6f}, Sharpe={ms:.6f} | {note}")

    assert improved_any, (
        "No NEW configuration matched or beat OLD on realized vol within tolerance."
    )
