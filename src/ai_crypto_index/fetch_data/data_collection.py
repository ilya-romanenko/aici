import os
import time
from datetime import datetime

import pandas as pd
try:
    import ccxt
except ModuleNotFoundError as exc:  # pragma: no cover - environment dependent
    ccxt = None  # type: ignore[assignment]
    _CCXT_IMPORT_ERROR = exc
else:
    _CCXT_IMPORT_ERROR = None

exchange = None


def _ensure_exchange():
    global exchange
    if exchange is not None:
        return exchange
    if ccxt is None:
        raise ModuleNotFoundError(
            "ccxt is required to download market data. Install a compatible ccxt build."
        ) from _CCXT_IMPORT_ERROR
    exchange = ccxt.binance({"enableRateLimit": True})
    return exchange

def _fetch_daily_ohlcv(symbol_pair, since_ms, end_ms):
    client = _ensure_exchange()
    tf = "1d"
    candles = []
    while since_ms < end_ms:
        try:
            chunk = client.fetch_ohlcv(symbol_pair, tf, since=since_ms, limit=1000)
        except Exception as exc:
            # ← key point: return an empty list rather than crashing the whole script
            if ccxt is not None and isinstance(exc, ccxt.BadSymbol):
                return []
            raise
        candles.extend(chunk)
        if not chunk:
            break
        since_ms = chunk[-1][0] + 24*60*60*1000
        time.sleep(client.rateLimit / 1000)
    return candles

def download_crypto_data_binance(
    cmc_symbol: str,
    start_date: str,
    end_date: str,
    data_folder: str = "data",
    max_missing_days: int = 3,
):
    client = _ensure_exchange()
    base = cmc_symbol.replace("-USD", "")
    symbol_pair = base + "/USDT"

    # --- 1. convert dates for validation --------------------------
    s_dt = datetime.strptime(start_date, "%Y-%m-%d")        # <<< FIX (naive)
    e_dt = datetime.strptime(end_date,   "%Y-%m-%d")        # <<< FIX (naive)

    # Binance requires ms timestamps, so we keep the UTC-suffixed string here
    since_ms = client.parse8601(start_date + "T00:00:00Z")
    end_ms   = client.parse8601(end_date   + "T00:00:00Z")

    # --- 2. download candles ------------------------------------------
    raw = _fetch_daily_ohlcv(symbol_pair, since_ms, end_ms)
    if not raw:
        print(f"[WARNING] Empty data for {symbol_pair}.")
        return None

    df = pd.DataFrame(
        raw, columns=["Date", "Open", "High", "Low", "Close", "Volume"]
    )
    # set as index and immediately strip tzinfo
    df["Date"] = (pd.to_datetime(df["Date"], unit="ms", utc=True).dt.tz_convert(None))
    df = df[["Date","Close"]].set_index("Date").sort_index()

    full_idx = pd.date_range(start=s_dt, end=e_dt, freq="D")
    df = df.reindex(full_idx)

    os.makedirs(data_folder, exist_ok=True)

    csv_path = os.path.join(data_folder, f"{base}.csv")
    df.to_csv(csv_path, float_format="%.8f")
    print(f"[INFO] Saved {base} → {csv_path} (rows: {len(df)})")
    return df

def download_multiple_cryptos(symbols, start_date, end_date, data_folder="data", progress_callback=None):
    total = len(symbols) if symbols is not None else 0
    for idx, sym in enumerate(symbols, start=1):
        if callable(progress_callback):
            try:
                progress_callback(sym, idx, total)
            except Exception:
                # Progress is best-effort and should not break downloads.
                pass
        download_crypto_data_binance(sym, start_date, end_date, data_folder)

if __name__ == "__main__":
    download_crypto_data_binance("USDC-USD", "2021-01-01", "2025-05-21", "data")
