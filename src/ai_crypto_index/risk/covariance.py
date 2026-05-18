# scr/risk/covariance.py
import numpy as np
import pandas as pd

from .correlation import corr_from_cov, ewma_cov, shrink_to_diag


def build_sigma_from_lstm(
    df_log_hist: pd.DataFrame,
    df_forecasts: pd.DataFrame,
    corr_mode: str = "shrink",
    gamma: float = 0.05,
    lam: float = 0.97,
) -> pd.DataFrame:
    """
    Parameters:
        df_log_hist: historical log returns aligned to the columns present in
            `df_forecasts`.
        df_forecasts: horizon-by-asset matrix of volatility forecasts (or |r|
            proxies) covering H steps ahead.
    """
    cols = df_forecasts.columns
    hist = df_log_hist[cols].dropna(how="any")

    # 1) Forecast variance over the horizon: V_i = sum_t (σ̂_{i,t})^2
    V = (df_forecasts.values ** 2).sum(axis=0)
    V = np.maximum(V, 1e-16)
    D = np.diag(np.sqrt(V))

    # 2) Estimate ρ̂ from historical data
    if corr_mode == "shrink":
        S_sample = hist.cov()
        S_shr = shrink_to_diag(S_sample, gamma=gamma)
        Rho = corr_from_cov(S_shr)
    elif corr_mode == "ewma":
        S_ewma = ewma_cov(hist, lam=lam)
        Rho = corr_from_cov(S_ewma)
    else:
        raise ValueError("corr_mode must be 'shrink' or 'ewma'")

    # 3) Σ̂ = D·ρ̂·D
    Sigma_hat = pd.DataFrame(D @ Rho.values @ D, index=cols, columns=cols)
    Sigma_hat.values[np.diag_indices_from(Sigma_hat.values)] += 1e-8
    return Sigma_hat
