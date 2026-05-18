# ai_crypto_index/pipelines/helpers.py
import numpy as np
from scipy.stats import spearmanr
from sklearn.preprocessing import StandardScaler

from ai_crypto_index.forecast.lstm_vol import generate_lstm_forecast


def _smape(y_true, y_pred, eps=1e-12):
    denom = (np.abs(y_true) + np.abs(y_pred) + eps)
    return 100.0 * np.mean(2.0 * np.abs(y_pred - y_true) / denom)

def _mae(y_true, y_pred):
    return float(np.mean(np.abs(y_pred - y_true)))

def _spearman_rank(y_true, y_pred):
    if len(y_true) < 3:
        return np.nan
    rho, _ = spearmanr(y_true, y_pred)
    return float(rho)

def _inverse_to_abs_from_model_outputs(y_pred_scaled, scaler: StandardScaler, eps=1e-8):
    y_pred_log = scaler.inverse_transform(y_pred_scaled.reshape(-1, 1)).flatten()
    return np.expm1(y_pred_log) - eps

def _naive_last_value_from_log_abs(log_abs_test, window_size, eps=1e-8):
    y_true_log  = log_abs_test[window_size:]
    y_naive_log = log_abs_test[window_size-1:-1]
    return np.expm1(y_true_log) - eps, np.expm1(y_naive_log) - eps

def _ewma(x, alpha=0.2):
    if len(x) == 0:
        return 0.0
    s = float(x[0])
    for v in x[1:]:
        s = alpha * float(v) + (1 - alpha) * s
    return s

def benchmark_report_for_asset(
    asset: str,
    log_abs_test: np.ndarray,        # log target on the test set (log1p(|r|+eps)) WITHOUT scaling
    y_pred_scaled: np.ndarray,       # model predictions on X_test (SCALED)
    scaler: StandardScaler,
    window_size: int,
    eps: float = 1e-8
) -> dict:
    y_true_abs, y_naive_abs = _naive_last_value_from_log_abs(log_abs_test, window_size, eps)
    y_pred_abs = _inverse_to_abs_from_model_outputs(y_pred_scaled, scaler, eps)

    n = min(len(y_true_abs), len(y_pred_abs), len(y_naive_abs))
    y_true_abs  = y_true_abs[:n]
    y_pred_abs  = y_pred_abs[:n]
    y_naive_abs = y_naive_abs[:n]

    m_mae_pred    = _mae(y_true_abs, y_pred_abs)
    m_mae_naive   = _mae(y_true_abs, y_naive_abs)
    m_smape_pred  = _smape(y_true_abs, y_pred_abs)
    m_smape_naive = _smape(y_true_abs, y_naive_abs)
    m_spr_pred    = _spearman_rank(y_true_abs, y_pred_abs)

    print(
        f"[BENCH] {asset} | N={n} | "
        f"MAE: pred={m_mae_pred:.6f} vs naive={m_mae_naive:.6f} | "
        f"SMAPE: pred={m_smape_pred:.2f}% vs naive={m_smape_naive:.2f}% | "
        f"Spearman(pred, true)={m_spr_pred:.3f}"
    )

    return {
        "asset": asset,
        "n_samples": int(n),
        "mae_pred":   float(m_mae_pred),
        "mae_naive":  float(m_mae_naive),
        "smape_pred":  float(m_smape_pred),
        "smape_naive": float(m_smape_naive),
        "spearman_pred": float(m_spr_pred),
        # for calibration in gate_and_forecast
        "y_true_abs":  y_true_abs,
        "y_pred_abs":  y_pred_abs,
    }

def gate_and_forecast(
    asset: str,
    series: np.ndarray,              # original log-returns r_t
    model,
    scaler: StandardScaler,
    window_size: int,
    forecast_horizon: int,
    benchmark: dict,
    tol: float = 0.05,               # tightened to 5%
    min_spr: float = 0.2
) -> np.ndarray:
    """
    If LSTM does not outperform the naive baseline (MAE/SMAPE) by tol OR Spearman <= min_spr — fall back to EWMA(|r|).
    Otherwise — LSTM forecast with scale calibration k (from test std) + internal calibration to history.
    """
    mode = benchmark.get("mode", "test")
    if mode == "no_test":
        # no test segment — don't take the risk
        abs_hist = np.abs(series[-(window_size*6):])
        level = _ewma(abs_hist, alpha=0.2)
        print(f"[GATE] {asset}: fallback EWMA(|r|)={level:.6f} (no test segment).")
        return np.full(forecast_horizon, level, dtype=float)

    mae_pred   = benchmark["mae_pred"]
    mae_naive  = benchmark["mae_naive"]
    smape_pred  = benchmark["smape_pred"]
    smape_naive = benchmark["smape_naive"]
    spr        = benchmark["spearman_pred"]

    better_mae   = mae_pred   <= mae_naive  * (1.0 - tol)
    better_smape = smape_pred <= smape_naive * (1.0 - tol)
    good_rank    = (np.isnan(spr) or spr > min_spr)
    use_lstm     = (better_mae or better_smape) and good_rank

    # calibrate k from test std
    y_true_abs = np.asarray(benchmark["y_true_abs"])
    y_pred_abs = np.asarray(benchmark["y_pred_abs"])
    k = None
    if len(y_true_abs) > 2 and len(y_pred_abs) > 2:
        pred_std = float(np.std(y_pred_abs))
        true_std = float(np.std(y_true_abs))
        if pred_std > 0 and true_std > 0:
            k = float(np.clip(true_std / pred_std, 0.5, 2.0))
            message = (
                f"[CALIB] {asset}: scale k={k:.2f} (std_true={true_std:.4f}, "
                f"std_pred={pred_std:.4f})"
            )
            print(message)

    if use_lstm:
        fut = generate_lstm_forecast(
            model=model,
            series=series,
            window_size=window_size,
            forecast_horizon=forecast_horizon,
            scaler=scaler,
            calib_k=k  # <- pass calibration from the test set here
        )
        print(f"[GATE] {asset}: use LSTM forecast.")
        return fut

    # fallback
    abs_hist = np.abs(series[-(window_size*6):])
    level = _ewma(abs_hist, alpha=0.2)
    print(f"[GATE] {asset}: fallback EWMA(|r|)={level:.6f} (LSTM underperforms naive).")
    return np.full(forecast_horizon, level, dtype=float)


def gate_and_mix(
    asset: str,
    series: np.ndarray,              # original log-returns r_t
    model,
    scaler: StandardScaler,
    window_size: int,
    forecast_horizon: int,
    benchmark: dict,
    tol: float = 0.05,               # same tol as before
    min_spr: float = 0.2             # same Spearman threshold
) -> np.ndarray:
    """
    Mixture of LSTM and EWMA: future = alpha * LSTM + (1-alpha) * EWMA.
    If LSTM is worse than the naive baseline (MAE/SMAPE) and/or rank correlation is weak — alpha decreases.
    Scale calibration (k) is derived the same way as in gate_and_forecast.
    """
    # 0) If there is no test segment — use a minimal LSTM share so the model is not completely cut off
    mode = benchmark.get("mode", "test")
    if mode == "no_test":
        abs_hist = np.abs(series[-(window_size*6):])
        level = _ewma(abs_hist, alpha=0.2)
        # give LSTM a small share so it is not completely cut off
        fut_lstm = generate_lstm_forecast(
            model=model,
            series=series,
            window_size=window_size,
            forecast_horizon=forecast_horizon,
            scaler=scaler,
            calib_k=None,
        )
        alpha = 0.20
        fut = alpha * fut_lstm + (1.0 - alpha) * level
        print(f"[MIX] {asset}: no_test → α={alpha:.2f} (mix LSTM/EWMA).")
        return np.full(forecast_horizon, fut, dtype=float) if np.isscalar(fut) else fut

    # 1) Quality metrics
    mae_pred   = float(benchmark["mae_pred"])
    mae_naive  = float(benchmark["mae_naive"])
    smape_pred  = float(benchmark["smape_pred"])
    smape_naive = float(benchmark["smape_naive"])
    spr        = benchmark["spearman_pred"]

    # 2) Calibrate k from std
    y_true_abs = np.asarray(benchmark["y_true_abs"])
    y_pred_abs = np.asarray(benchmark["y_pred_abs"])
    k = None
    if len(y_true_abs) > 2 and len(y_pred_abs) > 2:
        pred_std = float(np.std(y_pred_abs))
        true_std = float(np.std(y_true_abs))
        if pred_std > 0 and true_std > 0:
            k = float(np.clip(true_std / pred_std, 0.5, 2.0))
            print(f"[CALIB] {asset}: k={k:.2f} (std_true={true_std:.4f}, std_pred={pred_std:.4f})")

    # 3) Build both legs of the mixture
    fut_lstm = generate_lstm_forecast(
        model=model,
        series=series,
        window_size=window_size,
        forecast_horizon=forecast_horizon,
        scaler=scaler,
        calib_k=k
    )
    abs_hist = np.abs(series[-(window_size*6):])
    level = _ewma(abs_hist, alpha=0.2)
    fut_ewma = np.full(forecast_horizon, level, dtype=float)

    # 4) Compute alpha (LSTM share)
    # Improvement over naive (0..1, where 1 = strong improvement)
    imp_mae   = max(0.0, (mae_naive  - mae_pred)  / max(mae_naive,  1e-12))
    imp_smape = max(0.0, (smape_naive - smape_pred) / max(smape_naive, 1e-12))
    imp = 0.6 * imp_mae + 0.4 * imp_smape  # weights can be adjusted

    # Rank-correlation factor (0..1). Below min_spr → 0; around 0.6 → ~1
    if spr is None or np.isnan(spr):
        spr_factor = 0.5
    else:
        spr_factor = np.clip((spr - min_spr) / (0.6 - min_spr), 0.0, 1.0)

    # Basic check for "much worse than naive" — in that case we cut alpha sharply
    better_mae   = mae_pred   <= mae_naive  * (1.0 - tol)
    better_smape = smape_pred <= smape_naive * (1.0 - tol)
    spr_ok       = (spr is None) or np.isnan(spr) or (spr > min_spr)

    if (not better_mae and not better_smape) or (not spr_ok):
        alpha = 0.15  # near-fallback, but not 0 — give LSTM a "thin voice"
    else:
        # Smooth scale: from 0.4 to 0.95 depending on quality
        alpha = 0.4 + 0.55 * (imp * spr_factor)
        alpha = float(np.clip(alpha, 0.15, 0.95))

    fut = alpha * fut_lstm + (1.0 - alpha) * fut_ewma
    print(
        f"[MIX] {asset}: α={alpha:.2f} | imp_mae={imp_mae:.2f}, "
        f"imp_smape={imp_smape:.2f}, spr_factor={spr_factor:.2f}"
    )
    return fut
