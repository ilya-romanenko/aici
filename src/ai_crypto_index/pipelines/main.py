import json
import os
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable
from threading import Event

import numpy as np
import pandas as pd

from ai_crypto_index.features.feature_engineering import compute_log_returns
from ai_crypto_index.fetch_data.data_preprocessing.load_and_preprocess import (
    load_and_preprocess_data_fixed,
)

# Imports of our modules
from ai_crypto_index.forecast.evaluation import evaluate_lstm
from ai_crypto_index.forecast.lstm_vol import train_lstm_for_asset
from ai_crypto_index.optimization.balanced_select import select_assets_balanced
from ai_crypto_index.optimization.clustering import hierarchical_clustering_by_corr
from ai_crypto_index.optimization.evaluate_portfolio_performance import (
    evaluate_portfolio_performance,
)
from ai_crypto_index.optimization.opt_amount_clusters import find_optimal_clusters
from ai_crypto_index.optimization.optimization import (
    project_to_bounded_simplex,
    risk_parity_weights,
)
from ai_crypto_index.pipelines.helpers import benchmark_report_for_asset, gate_and_mix
from ai_crypto_index.risk.covariance import build_sigma_from_lstm
from ai_crypto_index.shared import daily_snapshot
from ai_crypto_index.utils.data_filters import filter_assets_by_history
from ai_crypto_index.utils.environment import prepare_default_run_directory, prepare_environment
from ai_crypto_index.utils.execution_realism import apply_execution_realism_filters

EARLIEST_DATA_DATE = date(2015, 1, 1)
RUN_START_DATE_MIN = date(2016, 1, 1)
RUN_START_DATE_MAX_LOOKBACK_DAYS = 120
RUN_N_TOP_COINS_MIN = 30
RUN_N_TOP_COINS_MAX = 300
RUN_LOOKBACK_DAYS_MIN = 90
RUN_LOOKBACK_DAYS_MAX = 720
RUN_WINDOW_SIZE_MIN = 14
RUN_WINDOW_SIZE_MAX = 120
RUN_FORECAST_HORIZON_MIN = 7
RUN_FORECAST_HORIZON_MAX = 60
RUN_TOTAL_ASSETS_MIN = 5
RUN_TOTAL_ASSETS_MAX = 30
RUN_RISK_MIN_WEIGHT_MIN = 0.005
RUN_RISK_MIN_WEIGHT_MAX = 0.08
RUN_RISK_MAX_WEIGHT_MIN = 0.12
RUN_RISK_MAX_WEIGHT_MAX = 0.45
RUN_WEIGHT_CAP_MIN = 0.08
RUN_WEIGHT_CAP_MAX = 0.30
RUN_VOL_FLOOR_RATIO_MIN = 0.25
RUN_VOL_FLOOR_RATIO_MAX = 0.70
RUN_GATING_TOLERANCE_MAX = 0.10


def _parse_start_date(value: str | date | datetime) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return datetime.strptime(value, "%Y-%m-%d").date()
    raise ValueError("start_date must be a YYYY-MM-DD string.")


def _validate_run_parameters(
    *,
    n_top_coins: int | None,
    start_date: str | date | datetime | None,
    lookback_days: int,
    window_size: int,
    forecast_horizon: int,
    total_assets: int,
    risk_min_weight: float,
    risk_max_weight: float,
    weight_cap: float,
    vol_floor_ratio: float,
    gating_tolerance: float,
) -> None:
    if n_top_coins is not None:
        if n_top_coins < RUN_N_TOP_COINS_MIN or n_top_coins > RUN_N_TOP_COINS_MAX:
            raise ValueError("n_top_coins must be between 30 and 300.")
        if total_assets > n_top_coins:
            raise ValueError("total_assets must be less or equal to n_top_coins.")
    if lookback_days < RUN_LOOKBACK_DAYS_MIN or lookback_days > RUN_LOOKBACK_DAYS_MAX:
        raise ValueError("lookback_days must be between 90 and 720.")
    if window_size < RUN_WINDOW_SIZE_MIN or window_size > RUN_WINDOW_SIZE_MAX:
        raise ValueError("window_size must be between 14 and 120.")
    if forecast_horizon < RUN_FORECAST_HORIZON_MIN or forecast_horizon > RUN_FORECAST_HORIZON_MAX:
        raise ValueError("forecast_horizon must be between 7 and 60.")
    if total_assets < RUN_TOTAL_ASSETS_MIN or total_assets > RUN_TOTAL_ASSETS_MAX:
        raise ValueError("total_assets must be between 5 and 30.")
    if risk_min_weight < RUN_RISK_MIN_WEIGHT_MIN:
        raise ValueError("risk_min_weight must be at least 0.005.")
    if risk_max_weight < RUN_RISK_MAX_WEIGHT_MIN or risk_max_weight > RUN_RISK_MAX_WEIGHT_MAX:
        raise ValueError("risk_max_weight must be between 0.12 and 0.45.")
    if risk_max_weight < risk_min_weight:
        raise ValueError("risk_max_weight must be greater or equal to risk_min_weight.")
    if risk_min_weight * total_assets > 1.0 + 1e-9:
        raise ValueError("risk_min_weight too high for the requested asset count (sum would exceed 1).")
    risk_min_weight_max = min(RUN_RISK_MIN_WEIGHT_MAX, 1.0 / total_assets)
    if risk_min_weight > risk_min_weight_max + 1e-9:
        raise ValueError("risk_min_weight exceeds the allowed maximum for total_assets.")
    weight_cap_min = max(RUN_WEIGHT_CAP_MIN, 1.0 / total_assets)
    if weight_cap < weight_cap_min - 1e-9 or weight_cap > RUN_WEIGHT_CAP_MAX + 1e-9:
        raise ValueError("weight_cap must stay within [max(0.08, 1 / total_assets), 0.30].")
    if vol_floor_ratio < RUN_VOL_FLOOR_RATIO_MIN or vol_floor_ratio > RUN_VOL_FLOOR_RATIO_MAX:
        raise ValueError("vol_floor_ratio must be between 0.25 and 0.70.")
    if gating_tolerance < 0.0 or gating_tolerance > RUN_GATING_TOLERANCE_MAX:
        raise ValueError("gating_tolerance must be 0.10 or less.")
    if lookback_days < window_size:
        raise ValueError("lookback_days must be greater or equal to window_size.")
    if forecast_horizon > window_size:
        raise ValueError("forecast_horizon must be less or equal to window_size.")
    if forecast_horizon > lookback_days:
        raise ValueError("forecast_horizon must be less or equal to lookback_days.")
    if start_date:
        parsed = _parse_start_date(start_date)
        today = date.today()
        if parsed > today:
            raise ValueError("start_date cannot be in the future.")
        if parsed < RUN_START_DATE_MIN:
            raise ValueError("start_date cannot be earlier than 2016-01-01.")
        latest_allowed = today - timedelta(days=max(lookback_days, RUN_START_DATE_MAX_LOOKBACK_DAYS))
        if parsed > latest_allowed:
            raise ValueError("start_date is too recent for the requested history window.")
        window_start = parsed - timedelta(days=lookback_days)
        if window_start < EARLIEST_DATA_DATE:
            raise ValueError("lookback_days window exceeds available history from 2015-01-01.")


def run_monthly_update(
    n_top_coins: int = 100,
    start_date: str | None = None,
    lookback_days: int = 180,
    window_size: int = 30,
    forecast_horizon: int = 30,
    advanced_forecast: bool = True,
    fresh_data: bool = False,
    info_messages: bool = False,
    visualization: bool = False,
    run_id: str | None = None,
    total_assets: int = 10,
    clustering_metric: str = "sharpe",
    risk_min_weight: float = 0.03,
    risk_max_weight: float = 0.25,
    weight_cap: float = 0.15,
    vol_floor_ratio: float = 0.4,
    gating_tolerance: float = 0.02,
    progress_callback: Callable[[str | None, str | None, str | None], None] | None = None,
    cancel_event: Event | None = None,
    config_path: str | Path | None = None,
):
    """Run the monthly pipeline, persist artifacts, and return weights and performance.

    The new parameters total_assets, clustering_metric, risk_min_weight, risk_max_weight,
    weight_cap, vol_floor_ratio, gating_tolerance control the risk profile, but with
    default values they reproduce the previous logic.
    """
    log_entries: list[str] = []
    run_dir: Path | None = None

    def log_line(message: str, *, level: str = "INFO", respect_info: bool = False) -> None:
        stamped = f"{datetime.now().isoformat()} [{level}] {message}"
        log_entries.append(stamped)
        if not respect_info or info_messages:
            print(message)

    def flush_logs() -> None:
        nonlocal run_dir
        if run_dir is None or not log_entries:
            return
        log_path = run_dir / "log.txt"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write("\n".join(log_entries) + "\n")
        log_entries.clear()

    def log_progress(message: str) -> None:
        if progress_callback is None:
            return
        try:
            progress_callback(None, status=None, message=message)
        except Exception:
            pass

    run_started_at = time.monotonic()
    mode_label = "advanced" if advanced_forecast else "basic"
    log_line(
        f"[INFO] forecast_mode={mode_label} advanced_forecast={advanced_forecast}",
        respect_info=True,
    )
    log_progress(f"Forecast mode: {mode_label} (advanced_forecast={advanced_forecast})")

    active_stage = "prep"

    def report_progress(stage: str, status: str = "running", message: str | None = None) -> None:
        nonlocal active_stage
        active_stage = stage
        if progress_callback is None:
            return
        try:
            progress_callback(stage, status=status, message=message)
        except Exception:
            # Progress tracking is best-effort and should not break the pipeline.
            pass

    def ensure_not_cancelled() -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("pipeline_cancelled")

    log_line("[INFO] === Starting monthly index update ===", respect_info=True)

    try:
        ensure_not_cancelled()
        report_progress("prep", "running", "Preparing environment and configuration")
        start_date_provided = start_date is not None and str(start_date).strip() != ""
        if not start_date or not isinstance(start_date, str) or start_date.strip() == "":
            one_year_ago = (datetime.now() - pd.DateOffset(years=1)).date()
            start_date = one_year_ago.strftime("%Y-%m-%d")
            log_line(f"[INFO] start_date not provided -> using {start_date}", respect_info=True)

        _validate_run_parameters(
            n_top_coins=n_top_coins,
            start_date=start_date if start_date_provided else None,
            lookback_days=lookback_days,
            window_size=window_size,
            forecast_horizon=forecast_horizon,
            total_assets=total_assets,
            risk_min_weight=risk_min_weight,
            risk_max_weight=risk_max_weight,
            weight_cap=weight_cap,
            vol_floor_ratio=vol_floor_ratio,
            gating_tolerance=gating_tolerance,
        )

        end_date = datetime.now().strftime("%Y-%m-%d")
        if fresh_data:
            log_line(
                "[INFO] fresh_data flag ignored; data source selection is automatic",
                respect_info=True,
            )

        env_summary = prepare_environment(
            config_path=Path(config_path) if config_path is not None else None,
            top_n=n_top_coins,
            preload_data=False,
        )
        config_path = Path(env_summary["config"])
        try:
            config_payload = json.loads(config_path.read_text(encoding="utf-8"))
        except Exception:
            config_payload = {}
        data_cfg = config_payload.get("data", {})
        execution_cfg = config_payload.get("execution_realism", {})
        run_dir = prepare_default_run_directory(run_id=run_id, config_path=config_path)
        log_line(f"[INFO] run_id resolved to {run_dir.name}", respect_info=True)
        log_line(f"[INFO] Run artifacts will be written to {run_dir}", respect_info=True)
        report_progress("prep", "done", f"Run directory ready ({run_dir.name})")

        data_folder = str(env_summary["data"]["data_root"])
        report_progress("download", "running", "Downloading market assets")
        ensure_not_cancelled()
        try:
            snapshot_selection = daily_snapshot.select_snapshot_dataframe(
                config_path=config_path,
                runs_root=Path(env_summary["runs"]["runs_root"]),
                n_top_coins=n_top_coins,
            )
            merged_df = snapshot_selection.dataframe
            merged_df.index = pd.to_datetime(merged_df.index)
            merged_df = merged_df.loc[start_date:]
            merged_df = merged_df.loc[:end_date]
            snapshot_meta = snapshot_selection.meta
            snapshot_stamp = getattr(snapshot_meta, "snapshot_date", None)
            snapshot_info = snapshot_stamp.isoformat() if snapshot_stamp else "unknown"
            log_line(
                "[INFO] data_source="
                f"{snapshot_selection.source} snapshot={snapshot_info} "
                f"path={snapshot_meta.local_path}",
                respect_info=True,
            )
        except daily_snapshot.DailySnapshotError as exc:
            log_line(
                f"[WARN] {exc}; falling back to merged CSV preprocessing",
                level="WARNING",
                respect_info=True,
            )
            merged_df = load_and_preprocess_data_fixed(
                data_folder=data_folder,
                dropna_all=bool(data_cfg.get("dropna_all", True)),
                min_history_days=int(data_cfg.get("min_history_days", 365)),
                include_delisted=bool(data_cfg.get("include_delisted", False)),
                allow_internal_gaps=bool(data_cfg.get("allow_internal_gaps", False)),
                start_date=start_date,
                end_date=end_date,
            )
            merged_df.index = pd.to_datetime(merged_df.index)

        if merged_df is None or merged_df.empty:
            log_line("[ERROR] Merged DataFrame is empty. Aborting.", level="ERROR")
            return

        as_of_date = pd.Timestamp(merged_df.index.max()).date()
        merged_df, excluded_assets, execution_context = apply_execution_realism_filters(
            merged_df,
            execution_cfg=execution_cfg,
            as_of_date=as_of_date,
            default_tail_grace_days=int(data_cfg.get("tail_grace_days", 3)),
        )
        log_line(
            "[INFO] execution_realism "
            f"enabled={execution_context['enabled']} "
            f"target_venue={execution_context['target_venue']} "
            f"target_region={execution_context['target_region']} "
            f"as_of={execution_context['as_of_date']}",
            respect_info=True,
        )
        if excluded_assets:
            for excluded in excluded_assets:
                log_line(
                    "[INFO] universe_exclude "
                    f"asset={excluded['asset']} "
                    f"reasons={excluded['reasons']} "
                    f"details={excluded['details']}",
                    respect_info=True,
                )
        log_line(
            "[INFO] execution_realism_summary "
            f"excluded={execution_context.get('excluded_count', 0)} "
            f"kept={execution_context.get('kept_count', len(merged_df.columns))}",
            respect_info=True,
        )
        if merged_df.empty:
            raise ValueError("All assets excluded by execution realism filters.")

        merged_path = os.path.join(data_folder, "merged_prices.csv")
        merged_df.to_csv(merged_path)
        log_line(f"[INFO] Merged data saved to {merged_path}", respect_info=True)
        merged_assets = len(merged_df.columns)
        merged_rows = len(merged_df.index)
        report_progress(
            "download",
            "done",
            f"Merged history with {merged_assets} assets across {merged_rows} rows",
        )
        ensure_not_cancelled()

        weights, perf, df_log_hist = compute_monthly_weights_for_df(
            merged_df,
            lookback_days=lookback_days,
            window_size=window_size,
            forecast_horizon=forecast_horizon,
            advanced_forecast=advanced_forecast,
            info_messages=info_messages,
            visualization=visualization,
            total_assets=total_assets,
            clustering_metric=clustering_metric,
            risk_min_weight=risk_min_weight,
            risk_max_weight=risk_max_weight,
            weight_cap=weight_cap,
            vol_floor_ratio=vol_floor_ratio,
            gating_tolerance=gating_tolerance,
            progress_callback=report_progress,
            cancel_event=cancel_event,
        )

        log_line("=== Forecast-based Risk Parity Weights ===")
        for asset, w in weights.items():
            log_line(f"{asset}: {w:.4f}")

        print()
        log_line("=== Portfolio Performance Metrics ===")
        for metric, value in perf.items():
            log_line(f"{metric}: {value:.2f}")

        if run_dir is not None:
            weights_series = pd.Series(weights, dtype=float)
            weights_df = weights_series.rename("weight").to_frame()
            weights_df.index.name = "asset"
            weights_path = run_dir / "weights.csv"
            weights_df.to_csv(weights_path)
            log_line(f"[INFO] weights.csv written to {weights_path}", respect_info=True)

            perf_path = run_dir / "perf.json"
            with perf_path.open("w", encoding="utf-8") as fh:
                json.dump(perf, fh, indent=2, sort_keys=True)
                fh.write("\n")
            log_line(f"[INFO] perf.json written to {perf_path}", respect_info=True)

            equity_curve_path = run_dir / "equity_curve.csv"
            if df_log_hist is not None and not df_log_hist.empty:
                weights_aligned = weights_series.reindex(df_log_hist.columns).fillna(0.0)
                portfolio_log_returns = (df_log_hist * weights_aligned).sum(axis=1)
                equity_curve = np.exp(portfolio_log_returns.cumsum())
                equity_curve_df = equity_curve.to_frame(name="equity_curve")
                if equity_curve_df.index.name is None:
                    equity_curve_df.index.name = "date"
                equity_curve_df.to_csv(equity_curve_path)
            else:
                pd.DataFrame(columns=["equity_curve"]).to_csv(equity_curve_path)
            log_line(f"[INFO] equity_curve.csv written to {equity_curve_path}", respect_info=True)

        log_line("======= Monthly index update finished =======")
        return weights, perf
    except Exception as exc:
        report_progress(active_stage, status="error", message=str(exc))
        log_line(f"Run failed: {exc}", level="ERROR")
        raise
    finally:
        if run_started_at is not None:
            duration_seconds = time.monotonic() - run_started_at
            log_line(
                f"[INFO] run_duration_seconds={duration_seconds:.2f}",
                respect_info=True,
            )
            log_progress(f"Run duration: {duration_seconds:.2f}s")
        flush_logs()

def compute_monthly_weights_for_df(
    df_prices: pd.DataFrame,
    lookback_days: int = 180,
    window_size: int = 30,
    forecast_horizon: int = 30,
    advanced_forecast: bool = True,
    info_messages: bool = False,
    visualization: bool = False,
    total_assets: int = 10,
    clustering_metric: str = "sharpe",
    risk_min_weight: float = 0.03,
    risk_max_weight: float = 0.25,
    weight_cap: float = 0.15,
    vol_floor_ratio: float = 0.4,
    gating_tolerance: float = 0.02,
    progress_callback: Callable[[str | None, str | None, str | None], None] | None = None,
    cancel_event: Event | None = None,
) -> tuple[dict, dict, pd.DataFrame]:
    """
    Pure core of the monthly update:
    - NO disk reads or writes
    - Accepts an already-prepared df_prices (index=Date, columns=tickers)
    - Returns:
        weights (dict: ticker -> weight),
        perf (portfolio metrics),
        df_log_hist (
            historical log-returns for the selected assets, so that weights
            can be applied consistently
        )

    Parameters total_assets, clustering_metric, risk_min_weight, risk_max_weight,
    weight_cap, vol_floor_ratio, and gating_tolerance allow varying the risk
    profile without changing behaviour at default values.
    """
    # 1) limit to lookback window
    def report_progress(stage: str, status: str = "running", message: str | None = None) -> None:
        if progress_callback is None:
            return
        try:
            progress_callback(stage, status=status, message=message)
        except Exception:
            pass

    def ensure_not_cancelled() -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("pipeline_cancelled")

    ensure_not_cancelled()

    def ensure_not_cancelled() -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("pipeline_cancelled")

    # 1) validate run parameters
    _validate_run_parameters(
        n_top_coins=None,
        start_date=None,
        lookback_days=lookback_days,
        window_size=window_size,
        forecast_horizon=forecast_horizon,
        total_assets=total_assets,
        risk_min_weight=risk_min_weight,
        risk_max_weight=risk_max_weight,
        weight_cap=weight_cap,
        vol_floor_ratio=vol_floor_ratio,
        gating_tolerance=gating_tolerance,
    )

    cutoff_date = df_prices.index.max() - pd.Timedelta(days=lookback_days)
    df_prices_window = df_prices.loc[cutoff_date:]
    df_log = compute_log_returns(df_prices_window)

    # - minimum required number of observations per asset in the lookback window
    min_obs = max(30, int(0.7 * lookback_days))   # e.g. >=70% of days and at least one month

    # keep only assets with a sufficient number of data points
    valid_counts = df_log.count()
    kept_cols = valid_counts[valid_counts >= min_obs].index.tolist()
    df_log = df_log[kept_cols]

    # safety guards
    if df_log.empty or df_log.shape[1] < 2:
        # Return an explicit marker so the backtest skips this rebalancing window.
        if info_messages:
            print(
                "[WARN] Not enough assets with data for clustering. "
                "Skipping this rebalance window."
            )
        return {}, {}, pd.DataFrame(index=df_prices_window.index)

    ensure_not_cancelled()
    report_progress("cluster", "running", "Clustering assets and filtering history")

    # 2) find optimal cluster count and cluster assets
    X = df_log.T.values  # features (log-returns)
    n_assets = X.shape[0]  # number of assets
    max_k = min(10, n_assets - 1)
    k_found = find_optimal_clusters(df_log, max_k=max_k, linkage='complete')
    k_final = min(k_found, 5)

    # Hierarchical clustering
    cluster_dict = hierarchical_clustering_by_corr(
        df_log,
        method="complete",
        max_clusters=k_final,
        show_dendrogram=visualization,
    )
    if info_messages:
        print("[INFO] Clustering result:", cluster_dict)

    ensure_not_cancelled()
    selected_assets = select_assets_balanced(
        cluster_dict=cluster_dict,
        df_log=df_log,
        total_assets=total_assets,
        metric=clustering_metric,
        corr_threshold=0.9,
    )
    if info_messages:
        print("[INFO] Selected assets after refined cluster selection:", selected_assets)

    report_progress(
        "cluster",
        "done",
        f"Selected {len(selected_assets)} assets for training",
    )

    def ewma_level(values: np.ndarray, alpha: float = 0.2) -> float:
        if len(values) == 0:
            return 0.0
        level = float(values[0])
        for v in values[1:]:
            level = alpha * float(v) + (1.0 - alpha) * level
        return level

    if advanced_forecast:
        # 3) filter by history length for LSTM
        min_len = window_size + forecast_horizon
        assets_ok = filter_assets_by_history(df_log, selected_assets, min_len=min_len)
        if len(assets_ok) < 3:
            raise ValueError("Too few assets with sufficient history for LSTM.")

        ensure_not_cancelled()
        total_train = len(assets_ok)
        report_progress(
            "train",
            "running",
            f"Training forecasts for {total_train} assets (100% remaining)",
        )

        # 4) train LSTM models and build forecasts
        if info_messages:
            print("[INFO] Training LSTM models...")
        lstm_forecasts = {}
        trained_assets = []
        for idx, asset in enumerate(assets_ok, start=1):
            series = df_log[asset].dropna()
            ensure_not_cancelled()
            remaining_pct = max(0.0, (total_train - idx + 1) / max(1, total_train) * 100)
            report_progress(
                "train",
                "running",
                f"Training {asset} ({idx}/{total_train}) - {remaining_pct:.0f}% remaining",
            )
            model, X_test, y_test, scaler, test_raw = train_lstm_for_asset(
                pd.DataFrame({asset: series}),
                asset_name=asset,
                window_size=window_size,
                epochs=10,
            )
            if model is None:
                print(f"[WARN] Skip {asset}: insufficient samples for LSTM.")
                continue
            has_test = (X_test is not None) and (getattr(X_test, "size", 0) > 0) \
                    and (y_test is not None) and (getattr(y_test, "size", 0) > 0)

            if has_test:
                _ = evaluate_lstm(model, X_test, y_test, plot=visualization)
                y_pred_scaled = model.predict(X_test, verbose=0).flatten()
                report = benchmark_report_for_asset(
                    asset, log_abs_test=test_raw, y_pred_scaled=y_pred_scaled,
                    scaler=scaler, window_size=window_size
                )
            else:
                # no test window — skip benchmark and do not gate the prediction
                report = {
                    "asset": asset,
                    "mode": "no_test",
                    "n_samples": 0,
                    "mae_pred":   float("inf"),
                    "mae_naive":  float("inf"),
                    "smape_pred":  float("inf"),
                    "smape_naive": float("inf"),
                    "spearman_pred": None,
                }
                print(f"[BENCH] {asset} | N=0 | no_test → skip gating")

            future_returns = gate_and_mix(
                asset=asset,
                series=series.values,
                model=model,
                scaler=scaler,
                window_size=window_size,
                forecast_horizon=forecast_horizon,
                benchmark=report,
                tol=gating_tolerance,
            )

            lstm_forecasts[asset] = future_returns
            trained_assets.append(asset)
            remaining_after = max(0.0, (total_train - idx) / max(1, total_train) * 100)
            report_progress(
                "train",
                "running",
                f"Finished {asset} ({idx}/{total_train}) - {remaining_after:.0f}% remaining",
            )

        if len(trained_assets) < 3:
            raise ValueError("Not enough assets with successful LSTM forecasts.")

        ensure_not_cancelled()
        report_progress(
            "train",
            "done",
            f"Forecasts ready for {len(trained_assets)} assets",
        )

        df_forecasts = pd.DataFrame(lstm_forecasts).dropna(axis=1)
        assets_forecasted = list(df_forecasts.columns)

        df_log_hist = df_log[assets_forecasted].dropna(how="any")
        if df_log_hist.shape[0] < 60:
            print("[WARN] Short intersection for correlation estimation (rows<60).")

    else:
        ensure_not_cancelled()
        total_assets_for_ewma = len(selected_assets)
        report_progress(
            "train",
            "running",
            f"Building EWMA forecasts for {total_assets_for_ewma} assets (100% remaining)",
        )
        ewma_forecasts = {}
        for idx, asset in enumerate(selected_assets, start=1):
            series = df_log[asset].dropna().values
            if len(series) == 0:
                if info_messages:
                    print(f"[WARN] Skip {asset}: no history for EWMA.")
                continue
            abs_hist = np.abs(series[-(window_size * 6):])
            level = ewma_level(abs_hist, alpha=0.2)
            ewma_forecasts[asset] = np.full(forecast_horizon, level, dtype=float)
            remaining_after = max(
                0.0,
                (total_assets_for_ewma - idx) / max(1, total_assets_for_ewma) * 100,
            )
            report_progress(
                "train",
                "running",
                f"Prepared {asset} ({idx}/{total_assets_for_ewma}) - {remaining_after:.0f}% remaining",
            )

        if len(ewma_forecasts) < 3:
            raise ValueError("Not enough assets with data for EWMA forecasts.")

        ensure_not_cancelled()
        report_progress(
            "train",
            "done",
            f"EWMA forecasts ready for {len(ewma_forecasts)} assets",
        )

        df_forecasts = pd.DataFrame(ewma_forecasts).dropna(axis=1)
        assets_forecasted = list(df_forecasts.columns)
        df_log_hist = df_log[assets_forecasted].dropna(how="any")
        if df_log_hist.shape[0] < 60:
            print("[WARN] Short intersection for correlation estimation (rows<60).")
    use_stat = "median"  # or "mean"
    vol_hat = {}
    for a in assets_forecasted:
        fut = df_forecasts[a].values
        v = float(np.median(fut)) if use_stat == "median" else float(np.mean(fut))
        vol_hat[a] = max(v, 1e-12)

    # Guard against excessively low forecasts:
    # vol_floor_ratio sets the minimum fraction of historical σ(|r|).
    hist_window = window_size * 6
    for a in assets_forecasted:
        hist_abs = np.abs(df_log[a].dropna().values[-hist_window:])
        hist_sigma = float(np.std(hist_abs)) if len(hist_abs) > 0 else vol_hat[a]
        floor_val = vol_floor_ratio * hist_sigma
        if vol_hat[a] < floor_val:
            print(
                f"[VOL-FLOOR] {a}: {vol_hat[a]:.6f} -> {floor_val:.6f} "
                f"(hist σ|r|={hist_sigma:.6f})"
            )
            vol_hat[a] = floor_val

    # Apply the floor directly to df_forecasts so downstream code sees the floored values
    for a in assets_forecasted:
        df_forecasts[a] = np.maximum(df_forecasts[a].values, vol_hat[a])


    Sigma_hat = build_sigma_from_lstm(
        df_log_hist=df_log_hist,
        df_forecasts=df_forecasts,
        corr_mode="shrink",
        gamma=0.05,
        lam=0.97,
    )

    # At this point we have the trained LSTM models and their forecasts
    if info_messages:
        if advanced_forecast:
            print(
                "[INFO] === LSTM training completed. Next step: use predictions ",
                "for weight optimization. ==="
            )
        else:
            print(
                "[INFO] === EWMA forecasts ready. Next step: use predictions ",
                "for weight optimization. ==="
            )

    ensure_not_cancelled()
    report_progress("optimize", "running", "Optimizing weights and computing metrics")

    # 5) Risk Parity
    weights_array = risk_parity_weights(
        Sigma_hat,
        min_weight=risk_min_weight,
        max_weight=risk_max_weight,
    )
    if weights_array is None:
        print("Risk‑parity optimisation failed — aborting run.")
        report_progress("optimize", "error", "Risk parity optimisation failed")
        return None

    weights_array = project_to_bounded_simplex(
        weights_array,
        lower_bound=0.0,
        upper_bound=weight_cap,
    )
    weights = dict(zip(Sigma_hat.columns, weights_array))

    ensure_not_cancelled()
    # 6) metrics (on historical df_log_hist, column order aligned with weights_array)
    weights_array_capped = np.array([weights[a] for a in df_log_hist.columns])
    perf = evaluate_portfolio_performance(df_log_hist, weights_array_capped)

    report_progress(
        "optimize",
        "done",
        f"Optimized {len(weights)} assets and calculated metrics",
    )

    return weights, perf, df_log_hist


if __name__ == "__main__":
    run_monthly_update(n_top_coins = 100, start_date="2021-01-01", fresh_data=True)
