# scr/forecast/lstm_vol.py
from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

import numpy as np
from sklearn.preprocessing import StandardScaler

if TYPE_CHECKING:  # pragma: no cover
    import tensorflow as tf  # type: ignore
    from tensorflow import keras  # type: ignore
    from tensorflow.keras.models import Model  # type: ignore


def _lazy_tf() -> tuple[tf.Module, keras.Module]:
    try:
        tf_module = importlib.import_module("tensorflow")
    except ModuleNotFoundError as exc:  # pragma: no cover - explicit error path
        raise RuntimeError(
            "TensorFlow is required to run the forecasting pipeline. "
            "Install tensorflow==2.18.0 in the pipeline environment."
        ) from exc
    return tf_module, tf_module.keras

def make_lstm_dataset(series: np.ndarray, window_size: int = 30, forecast_horizon: int = 1):
    X, y = [], []
    if len(series) < window_size + forecast_horizon:
        return np.array([]), np.array([])
    for i in range(len(series) - window_size - forecast_horizon + 1):
        X.append(series[i: i + window_size])
        y.append(series[i + window_size + forecast_horizon - 1])
    X = np.array(X).reshape(-1, window_size, 1)
    y = np.array(y)
    return X, y

def loss_with_spread(y_true, y_pred):
    tf_module, keras = _lazy_tf()
    mse = keras.losses.MeanSquaredError()(y_true, y_pred)
    spread_penalty = 0.1 * tf_module.square(1.0 - tf_module.math.reduce_std(y_pred))
    return mse + spread_penalty

def build_lstm_model(window_size: int, loss_fn) -> Model:
    _, keras = _lazy_tf()
    inputs = keras.layers.Input(shape=(window_size, 1))
    x = keras.layers.LSTM(32, return_sequences=True)(inputs)
    x = keras.layers.Dropout(0.2)(x)
    x = keras.layers.LSTM(16)(x)
    x = keras.layers.Dropout(0.2)(x)
    outputs = keras.layers.Dense(1)(x)
    model = keras.Model(inputs=inputs, outputs=outputs)
    model.compile(optimizer=keras.optimizers.Adam(1e-3), loss=loss_fn)
    return model

def train_lstm_for_asset(
    df_log_returns,
    asset_name: str,
    window_size=30,
    epochs=30,
    forecast_horizon=1,
):
    series = df_log_returns[asset_name].dropna().values

    eps = 1e-8
    target_raw = np.log1p(np.abs(series) + eps)

    min_test_len = 60 
    if len(target_raw) < window_size + forecast_horizon + 50:
        return None, None, None, None, None  # ← 5 objects

    split_idx = int(len(target_raw) * 0.8)
    if len(target_raw) - split_idx < max(min_test_len, window_size + forecast_horizon + 5):
        split_idx = max(
            len(target_raw) - max(min_test_len, window_size + forecast_horizon + 5),
            window_size + 5,
        )
    if split_idx <= window_size + 5:
        print("[WARN] not enough history for train/test split")
        return None, None, None, None, None
    
    train_raw, test_raw = target_raw[:split_idx], target_raw[split_idx:]

    scaler = StandardScaler().fit(train_raw.reshape(-1, 1))
    train_vals = scaler.transform(train_raw.reshape(-1, 1)).flatten()
    test_vals = scaler.transform(test_raw.reshape(-1, 1)).flatten()

    X_train, y_train = make_lstm_dataset(train_vals, window_size, 1)
    X_test, y_test = make_lstm_dataset(test_vals, window_size, 1)

    if X_train.size == 0:
        print("[WARN] not enough samples")
        return None, None, None, None, None

    _, keras = _lazy_tf()
    model = build_lstm_model(window_size, loss_fn=keras.losses.Huber(delta=1.0))
    es = keras.callbacks.EarlyStopping(monitor="loss", patience=5, restore_best_weights=True)
    model.fit(X_train, y_train, epochs=epochs, batch_size=16, verbose=1, callbacks=[es])

    # Return nothing extra here — the prediction will be made in the main pipeline
    return model, X_test, y_test, scaler, test_raw

def generate_lstm_forecast(
    model,
    series,
    window_size=30,
    forecast_horizon=30,
    scaler=None,
    calib_k: float | None = None,
):
    eps = 1e-8
    ws = min(window_size, len(series))
    input_seq = np.log1p(np.abs(series[-ws:]) + eps)
    if scaler is not None:
        input_seq = scaler.transform(input_seq.reshape(-1, 1)).flatten()

    raw_preds = []
    for _ in range(forecast_horizon):
        X_input = np.array(input_seq[-ws:]).reshape(1, ws, 1)
        next_pred = float(model.predict(X_input, verbose=0)[0, 0])
        raw_preds.append(next_pred)
        input_seq = np.append(input_seq, next_pred)

    raw_arr = np.array(raw_preds).reshape(-1, 1)
    future_log = (
        scaler.inverse_transform(raw_arr).flatten()
        if scaler is not None
        else raw_arr.flatten()
    )
    future = np.expm1(future_log) - eps

    # simple spread calibration against recent history
    pred_std = float(np.std(future))
    hist_std = float(np.std(np.abs(series[-(window_size * 6) :])))
    scale = np.clip(hist_std / max(pred_std, 1e-12), 0.5, 5.0)
    future *= scale

    if calib_k is not None:
        future *= float(np.clip(calib_k, 0.5, 2.0))

    hist_std_r = float(np.std(series))
    hist_std_abs = float(np.std(np.abs(series)))
    pred_std_abs = float(np.std(future))
    message = (
        f"σ(|r|)_pred={pred_std_abs:.6f} | "
        f"σ(|r|)_hist={hist_std_abs:.6f} | "
        f"σ(r)_hist={hist_std_r:.6f}"
    )
    print(message)

    return future

