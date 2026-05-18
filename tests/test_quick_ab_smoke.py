import json
import os
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from ai_crypto_index.utils.environment import prepare_environment


# -------- helpers (micro versions) --------
def to_log_returns(df_prices: pd.DataFrame) -> pd.DataFrame:
    return np.log(df_prices / df_prices.shift(1)).dropna(how="any")

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

def risk_parity_weights(Sigma: pd.DataFrame, min_w=0.0, max_w=0.3, iters=300, tol=1e-8):
    n = Sigma.shape[0]
    w = np.ones(n) / n
    S = Sigma.values
    for _ in range(iters):
        m = S @ w
        port_var = w @ m
        if port_var <= 0:
            break
        target = port_var / n
        rc = w * m
        w_new = w * (target / (rc + 1e-16))
        w_new = np.clip(w_new, min_w, max_w)
        s = w_new.sum()
        w_new = (np.ones(n) / n) if s == 0 else w_new / s
        if np.linalg.norm(w_new - w, 1) < tol:
            w = w_new
            break
        w = w_new
    return pd.Series(w, index=Sigma.index)

def realized_vol(series: pd.Series) -> float:
    return float(series.std(ddof=0))

# -------- OLD vs NEW Σ̂ --------
def build_sigma_old(df_log_train: pd.DataFrame, H: int) -> pd.DataFrame:
    # imitate old logic: cov over |r| forecasts
    abs_tail = df_log_train.iloc[-H:].abs()    # H x N
    return abs_tail.cov()

def build_sigma_new(df_log_train: pd.DataFrame, H: int, lam: float = 0.97) -> pd.DataFrame:
    # D·ρ̂·D, where D comes from proxy σ̂ (rolling std), ρ̂ = EWMA-ρ
    rolling_std = df_log_train.rolling(H).std().iloc[-H:]  # H x N (proxy σ̂_t)
    V = (rolling_std.values ** 2).sum(axis=0)              # N,
    S_ewma = ewma_cov(df_log_train, lam=lam)
    Rho = corr_from_cov(S_ewma)
    D = np.diag(np.sqrt(V + 1e-16))
    Sigma = pd.DataFrame(
        D @ Rho.values @ D,
        index=df_log_train.columns,
        columns=df_log_train.columns,
    )
    Sigma.values[np.diag_indices_from(Sigma)] += 1e-8
    return Sigma

# -------- quick smoke test on a single step --------
def quick_ab_smoke(df_prices: pd.DataFrame,
                   lookback_days=180, horizon=10, top_n=8, lam=0.97):
    df_prices = df_prices.select_dtypes(include=[np.number]).dropna(how="all", axis=1)
    df_log = to_log_returns(df_prices)
    if df_log.shape[0] < lookback_days + horizon + 5 or df_log.shape[1] < top_n:
        raise RuntimeError("Not enough data for the smoke test.")
    # take the most recent data
    train = df_log.iloc[-(lookback_days + horizon):-horizon]
    # limit number of assets (speed and stability)
    cols = train.columns[:top_n]
    train = train[cols].dropna(how="any")
    # Sigma_old / Sigma_new
    Sigma_old = build_sigma_old(train, H=horizon)
    Sigma_new = build_sigma_new(train, H=horizon, lam=lam)
    common = list(set(Sigma_old.columns) & set(Sigma_new.columns))
    Sigma_old = Sigma_old.loc[common, common]
    Sigma_new = Sigma_new.loc[common, common]
    # weights
    w_old = risk_parity_weights(Sigma_old, min_w=0.0, max_w=0.3)
    w_new = risk_parity_weights(Sigma_new, min_w=0.0, max_w=0.3)
    # realized portfolio log-returns over the next window
    future = df_log.iloc[-horizon:][common]
    port_old = (future @ w_old).squeeze()
    port_new = (future @ w_new).squeeze()
    vol_old = realized_vol(port_old)
    vol_new = realized_vol(port_new)
    improvement = (vol_old - vol_new) / (vol_old + 1e-12)
    message = (
        f"[SMOKE] horizon={horizon}d | top_n={top_n} | "
        f"vol_old={vol_old:.6f} | vol_new={vol_new:.6f} | "
        f"Δ={improvement*100:.2f}%"
    )
    print(message)
    return vol_old, vol_new, improvement

CONFIG_PATH = Path("config/pipeline.json")


def _has_local_market_data() -> bool:
    if not CONFIG_PATH.exists():
        return False
    try:
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    data_root = Path(config.get("data", {}).get("root", "data"))
    if not data_root.exists():
        return False
    csv_files = list(data_root.glob('*.csv'))
    if not csv_files:
        return False
    top_n = config.get("market_data", {}).get("top_n", 0)
    try:
        top_n = int(top_n)
    except (TypeError, ValueError):
        top_n = 0
    min_files = max(1, int(top_n * 0.5)) if top_n else 1
    return len(csv_files) >= min_files


@pytest.mark.skipif(
    not _has_local_market_data(),
    reason="Local CSV data not available for environment preparation smoke test",
)
def test_prepare_environment_creates_expected_artifacts():
    summary = prepare_environment()
    assert CONFIG_PATH.exists()

    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    runs_cfg = config.get("runs", {})
    expected_files = runs_cfg.get("expected_files", [])

    sample_dir = Path(summary['runs']['sample_run'])
    assert sample_dir.is_dir()
    for name in expected_files:
        expected_file = sample_dir / name
        assert expected_file.exists(), f"Missing placeholder {name}"

    merged_path = Path(summary['data']['merged_path'])
    assert merged_path.exists()
    assert merged_path.stat().st_size > 0

    merged_df = pd.read_csv(merged_path, index_col=0)
    assert not merged_df.empty




def test_prepare_environment_respects_top_n_override(monkeypatch, tmp_path):
    import ai_crypto_index.utils.environment as env

    data_root = tmp_path / "data"
    runs_root = tmp_path / "runs"

    config = {
        "data": {
            "root": str(data_root),
            "min_history_days": 365,
            "allow_internal_gaps": False,
            "include_delisted": False,
            "dropna_all": True,
        },
        "market_data": {
            "provider": "stub",
            "top_n": 50,
            "start_date": "2021-01-01",
            "fresh_download": False,
        },
        "runs": {
            "root": str(runs_root),
            "expected_files": [],
        },
        "auth": {
            "database_url": f"sqlite+aiosqlite:///{(runs_root / 'auth.db').as_posix()}",
            "jwt_secret_key": "test-secret",
            "jwt_algorithm": "HS256",
            "access_token_ttl_seconds": 3600,
            "refresh_token_ttl_seconds": 86400,
            "email_token_ttl_seconds": 86400,
            "password_reset_ttl_seconds": 3600,
            "session_cookie_name": "test_session",
            "session_cookie_secure": False,
            "session_cookie_domain": None,
            "public_app_url": "https://app.test",
            "expose_tokens_in_responses": True,
            "echo_sql": False,
        },
    }
    config_path = tmp_path / "pipeline.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")

    requested: dict[str, int] = {}

    def fake_get_top_n(n: int) -> list[str]:
        requested["n"] = n
        return [f"COIN{i}" for i in range(n)]

    downloads: dict[str, object] = {}

    def fake_download(symbols, start_date, end_date, data_folder):
        downloads["symbols"] = list(symbols)
        downloads["start_date"] = start_date
        downloads["end_date"] = end_date
        downloads["data_folder"] = data_folder

    def fake_load_and_preprocess_data_fixed(*_, **__):
        cols = downloads.get("symbols") or [f"COIN{i}" for i in range(requested.get("n", 1))]
        return pd.DataFrame({symbol: [1.0] for symbol in cols})

    monkeypatch.setattr(env, "get_top_n_cryptos_cmc", fake_get_top_n)
    monkeypatch.setattr(env, "download_multiple_cryptos", fake_download)
    monkeypatch.setattr(env, "load_and_preprocess_data_fixed", fake_load_and_preprocess_data_fixed)

    summary = env.prepare_environment(config_path=config_path, top_n=3)

    assert requested["n"] == 3
    assert downloads["symbols"] == [f"COIN{i}" for i in range(3)]
    assert summary["data"]["effective_top_n"] == 3
    assert summary["data"]["data_root"] == str(data_root)

def test_run_monthly_update_persists_artifacts(monkeypatch, tmp_path):
    import sys
    import types

    if "tensorflow" not in sys.modules:
        tf_stub = types.ModuleType("tensorflow")
        keras_stub = types.ModuleType("tensorflow.keras")
        models_stub = types.ModuleType("tensorflow.keras.models")
        layers_stub = types.ModuleType("tensorflow.keras.layers")
        optimizers_stub = types.ModuleType("tensorflow.keras.optimizers")
        callbacks_stub = types.ModuleType("tensorflow.keras.callbacks")
        losses_stub = types.ModuleType("tensorflow.keras.losses")

        class _DummyLayer:
            def __init__(self, *args, **kwargs):
                pass

            def __call__(self, *args, **kwargs):
                return self

        class _DummyModel:
            def __init__(self, *args, **kwargs):
                pass

            def compile(self, *args, **kwargs):
                return None

            def fit(self, *args, **kwargs):
                return None

            def predict(self, *args, **kwargs):
                return np.zeros((1, 1))

        class _DummyCallback:
            def __init__(self, *args, **kwargs):
                pass

        def _loss_factory(*args, **kwargs):
            class _DummyLoss:
                def __call__(self, *args, **kwargs):
                    return 0.0

            return _DummyLoss()

        losses_stub.MeanSquaredError = _loss_factory
        losses_stub.Huber = _loss_factory

        layers_stub.Input = _DummyLayer
        layers_stub.LSTM = _DummyLayer
        layers_stub.Dense = _DummyLayer
        layers_stub.Dropout = _DummyLayer

        models_stub.Model = _DummyModel
        optimizers_stub.Adam = lambda *args, **kwargs: None
        callbacks_stub.EarlyStopping = _DummyCallback

        tf_stub.square = lambda x: x
        tf_stub.math = types.SimpleNamespace(reduce_std=lambda value: 0.0)
        keras_stub.models = models_stub
        keras_stub.layers = layers_stub
        keras_stub.optimizers = optimizers_stub
        keras_stub.callbacks = callbacks_stub
        keras_stub.losses = losses_stub
        tf_stub.keras = keras_stub

        sys.modules["tensorflow"] = tf_stub
        sys.modules["tensorflow.keras"] = keras_stub
        sys.modules["tensorflow.keras.models"] = models_stub
        sys.modules["tensorflow.keras.layers"] = layers_stub
        sys.modules["tensorflow.keras.optimizers"] = optimizers_stub
        sys.modules["tensorflow.keras.callbacks"] = callbacks_stub
        sys.modules["tensorflow.keras.losses"] = losses_stub

    from ai_crypto_index.pipelines import main as pipeline_main

    runs_root = tmp_path / "runs"
    data_root = tmp_path / "data"
    data_root.mkdir(parents=True, exist_ok=True)

    config = {
        "data": {
            "root": str(data_root),
            "min_history_days": 365,
            "allow_internal_gaps": False,
            "include_delisted": False,
            "dropna_all": True,
        },
        "market_data": {
            "provider": "stub",
            "top_n": 10,
            "start_date": "2022-01-01",
            "fresh_download": False,
        },
        "runs": {
            "root": str(runs_root),
            "expected_files": [
                "weights.csv",
                "perf.json",
                "equity_curve.csv",
                "log.txt",
            ],
        },
        "auth": {
            "database_url": f"sqlite+aiosqlite:///{(runs_root / 'auth.db').as_posix()}",
            "jwt_secret_key": "test-secret",
            "jwt_algorithm": "HS256",
            "access_token_ttl_seconds": 3600,
            "refresh_token_ttl_seconds": 86400,
            "email_token_ttl_seconds": 86400,
            "password_reset_ttl_seconds": 3600,
            "session_cookie_name": "test_session",
            "session_cookie_secure": False,
            "session_cookie_domain": None,
            "public_app_url": "https://app.test",
            "expose_tokens_in_responses": True,
            "echo_sql": False,
        },
    }
    config_path = tmp_path / "pipeline.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")

    fake_env_summary = {
        "config": str(config_path),
        "data": {"data_root": str(data_root)},
        "runs": {"runs_root": str(runs_root)},
    }

    def _fake_prepare_environment(config_path=None, **_):
        return fake_env_summary

    monkeypatch.setattr(pipeline_main, "prepare_environment", _fake_prepare_environment)

    dates = pd.date_range("2024-01-01", periods=10, freq="D")
    sample_prices = pd.DataFrame(
        {"BTC": np.linspace(100, 110, 10), "ETH": np.linspace(80, 88, 10)},
        index=dates,
    )

    def fake_loader(*args, **kwargs):
        return sample_prices.copy()

    monkeypatch.setattr(pipeline_main, "load_and_preprocess_data_fixed", fake_loader)

    df_log_hist = pd.DataFrame(
        {"BTC": [0.01, -0.005, 0.007], "ETH": [0.02, -0.01, 0.005]},
        index=pd.date_range("2024-05-01", periods=3, freq="D"),
    )
    weights = {"BTC": 0.6, "ETH": 0.4}
    perf = {
        "AnnualReturn(%)": 12.34,
        "AnnualVolatility(%)": 5.67,
        "SharpeRatio": 2.18,
        "MaxDrawdown(%)": -3.21,
    }

    def fake_compute(*args, **kwargs):
        return weights, perf, df_log_hist

    monkeypatch.setattr(pipeline_main, "compute_monthly_weights_for_df", fake_compute)

    result_weights, result_perf = pipeline_main.run_monthly_update(run_id="unit-test-run")

    assert result_weights == weights
    assert result_perf == perf

    run_dir = runs_root / "unit-test-run"
    weights_path = run_dir / "weights.csv"
    perf_path = run_dir / "perf.json"
    equity_path = run_dir / "equity_curve.csv"
    log_path = run_dir / "log.txt"

    assert weights_path.exists()
    assert perf_path.exists()
    assert equity_path.exists()
    assert log_path.exists()

    stored_weights = pd.read_csv(weights_path, index_col="asset")["weight"].to_dict()
    for asset, value in weights.items():
        assert stored_weights[asset] == pytest.approx(value)

    stored_perf = json.loads(perf_path.read_text(encoding="utf-8"))
    assert stored_perf == perf

    stored_equity = pd.read_csv(equity_path, index_col=0)["equity_curve"].values
    expected_equity = np.exp((df_log_hist * pd.Series(weights)).sum(axis=1).cumsum()).values
    assert np.allclose(stored_equity, expected_equity)

    first_log = log_path.read_text(encoding="utf-8")
    assert first_log.strip()

    first_weights_content = weights_path.read_text(encoding="utf-8")
    pipeline_main.run_monthly_update(run_id="unit-test-run")
    second_weights_content = weights_path.read_text(encoding="utf-8")
    second_log = log_path.read_text(encoding="utf-8")

    assert second_weights_content == first_weights_content
    assert len(second_log) > len(first_log)


def test_run_monthly_update_logs_execution_realism_exclusions(monkeypatch, tmp_path):
    import sys
    import types

    if "tensorflow" not in sys.modules:
        tf_stub = types.ModuleType("tensorflow")
        keras_stub = types.ModuleType("tensorflow.keras")
        models_stub = types.ModuleType("tensorflow.keras.models")
        layers_stub = types.ModuleType("tensorflow.keras.layers")
        optimizers_stub = types.ModuleType("tensorflow.keras.optimizers")
        callbacks_stub = types.ModuleType("tensorflow.keras.callbacks")
        losses_stub = types.ModuleType("tensorflow.keras.losses")

        class _DummyLayer:
            def __init__(self, *args, **kwargs):
                pass

            def __call__(self, *args, **kwargs):
                return self

        class _DummyModel:
            def __init__(self, *args, **kwargs):
                pass

            def compile(self, *args, **kwargs):
                return None

            def fit(self, *args, **kwargs):
                return None

            def predict(self, *args, **kwargs):
                return np.zeros((1, 1))

        class _DummyCallback:
            def __init__(self, *args, **kwargs):
                pass

        def _loss_factory(*args, **kwargs):
            class _DummyLoss:
                def __call__(self, *args, **kwargs):
                    return 0.0

            return _DummyLoss()

        losses_stub.MeanSquaredError = _loss_factory
        losses_stub.Huber = _loss_factory
        layers_stub.Input = _DummyLayer
        layers_stub.LSTM = _DummyLayer
        layers_stub.Dense = _DummyLayer
        layers_stub.Dropout = _DummyLayer
        models_stub.Model = _DummyModel
        optimizers_stub.Adam = lambda *args, **kwargs: None
        callbacks_stub.EarlyStopping = _DummyCallback
        tf_stub.square = lambda x: x
        tf_stub.math = types.SimpleNamespace(reduce_std=lambda value: 0.0)
        keras_stub.models = models_stub
        keras_stub.layers = layers_stub
        keras_stub.optimizers = optimizers_stub
        keras_stub.callbacks = callbacks_stub
        keras_stub.losses = losses_stub
        tf_stub.keras = keras_stub
        sys.modules["tensorflow"] = tf_stub
        sys.modules["tensorflow.keras"] = keras_stub
        sys.modules["tensorflow.keras.models"] = models_stub
        sys.modules["tensorflow.keras.layers"] = layers_stub
        sys.modules["tensorflow.keras.optimizers"] = optimizers_stub
        sys.modules["tensorflow.keras.callbacks"] = callbacks_stub
        sys.modules["tensorflow.keras.losses"] = losses_stub

    from ai_crypto_index.pipelines import main as pipeline_main

    runs_root = tmp_path / "runs"
    data_root = tmp_path / "data"
    data_root.mkdir(parents=True, exist_ok=True)

    config = {
        "data": {
            "root": str(data_root),
            "min_history_days": 30,
            "allow_internal_gaps": False,
            "include_delisted": False,
            "dropna_all": True,
            "tail_grace_days": 3,
        },
        "market_data": {
            "provider": "stub",
            "top_n": 10,
            "start_date": "2022-01-01",
            "fresh_download": False,
        },
        "execution_realism": {
            "enabled": True,
            "target_venue": "binance_spot",
            "target_region": "EEA",
            "require_tradable_on_target_venue": True,
            "require_region_allowed": True,
            "require_not_delisted_at_t": True,
            "region_restrictions": {
                "binance_spot": {
                    "EEA": {
                        "blocked_symbols": ["ETH"],
                    }
                }
            },
        },
        "runs": {
            "root": str(runs_root),
            "expected_files": ["weights.csv", "perf.json", "equity_curve.csv", "log.txt"],
        },
        "auth": {
            "database_url": f"sqlite+aiosqlite:///{(runs_root / 'auth.db').as_posix()}",
            "jwt_secret_key": "test-secret",
            "jwt_algorithm": "HS256",
            "access_token_ttl_seconds": 3600,
            "refresh_token_ttl_seconds": 86400,
            "email_token_ttl_seconds": 86400,
            "password_reset_ttl_seconds": 3600,
            "session_cookie_name": "test_session",
            "session_cookie_secure": False,
            "session_cookie_domain": None,
            "public_app_url": "https://app.test",
            "expose_tokens_in_responses": True,
            "echo_sql": False,
        },
    }
    config_path = tmp_path / "pipeline.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")

    fake_env_summary = {
        "config": str(config_path),
        "data": {"data_root": str(data_root)},
        "runs": {"runs_root": str(runs_root)},
    }

    def _fake_prepare_environment(config_path=None, **_):
        return fake_env_summary

    monkeypatch.setattr(pipeline_main, "prepare_environment", _fake_prepare_environment)

    sample_dates = pd.date_range("2024-01-01", periods=5, freq="D")
    sample_prices = pd.DataFrame(
        {
            "BTC": [100.0, 101.0, 102.0, 103.0, 104.0],
            "ETH": [50.0, 51.0, 52.0, 53.0, 54.0],
        },
        index=sample_dates,
    )
    monkeypatch.setattr(pipeline_main, "load_and_preprocess_data_fixed", lambda *args, **kwargs: sample_prices.copy())

    captured: dict[str, object] = {}

    def fake_compute(df_prices, **kwargs):
        captured["columns"] = list(df_prices.columns)
        df_log_hist = pd.DataFrame(
            {"BTC": [0.01, 0.02, -0.01]},
            index=pd.date_range("2024-05-01", periods=3, freq="D"),
        )
        return {"BTC": 1.0}, {"SharpeRatio": 1.0}, df_log_hist

    monkeypatch.setattr(pipeline_main, "compute_monthly_weights_for_df", fake_compute)

    pipeline_main.run_monthly_update(run_id="execution-realism-log-test")

    assert captured["columns"] == ["BTC"]
    log_path = runs_root / "execution-realism-log-test" / "log.txt"
    log_payload = log_path.read_text(encoding="utf-8")
    assert "execution_realism enabled=True" in log_payload
    assert "asset=ETH" in log_payload
    assert "region_restricted" in log_payload


if __name__ == "__main__":
    # Example run:
    # CSV with a date column as index and asset prices as columns
    csv_path = os.getenv("AI_CI_PRICES_CSV", "data/merged_prices.csv")
    df = pd.read_csv(csv_path, index_col=0, parse_dates=True)
    quick_ab_smoke(df, lookback_days=180, horizon=10, top_n=8)
