"""
Full backtest regeneration for all 3 AICI strategies (classic/conservative/risky).

- LSTM (advanced_forecast) DISABLED for speed
- Backtest effective start: ~2021-01-01 (data loaded from 2020-07-01 for 180-day lookback window)
- Output: runs/_performance/series/AICI_classic.csv, AICI_conservative.csv, AICI_risky.csv
          + BTC_USD.csv, ETH_USD.csv benchmarks

Step 1: Fetches top-200 CMC symbols (wider net for 5-year historical coverage)
Step 2: Downloads daily OHLCV from Binance for each asset (2020-07-01 .. today)
Step 3: Merges into a single price DataFrame via load_and_preprocess_data_fixed
Step 4: Runs simulate_index_over_time for each strategy variant (EWMA, no LSTM)
"""

import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ai_crypto_index.fetch_data.data_collection import download_multiple_cryptos  # noqa: E402
from ai_crypto_index.fetch_data.data_preprocessing.load_top_n_auto import get_top_n_cryptos_cmc  # noqa: E402
from ai_crypto_index.fetch_data.data_preprocessing.load_and_preprocess import (  # noqa: E402
    load_and_preprocess_data_fixed,
)
from ai_crypto_index.pipelines.backtesting.simulate_index import (  # noqa: E402
    simulate_index_over_time,
    STRATEGY_PRESETS,
)

# ========================== CONFIGURATION ==================================

N_TOP_COINS = 200               # wider net — covers historical top-100 rotation
DATA_START = "2020-07-01"       # 6 months before 2021-01-01 (lookback window)
END_DATE = datetime.now().strftime("%Y-%m-%d")

LOOKBACK_DAYS = 180
WINDOW_SIZE = 30
FORECAST_HORIZON = 30

RUNS_ROOT = PROJECT_ROOT / "runs"
SERIES_DIR = RUNS_ROOT / "_performance" / "series"
SCRATCH_DIR = RUNS_ROOT / "_performance" / "_backtest_scratch"

# Separate folder so we don't clobber production data/
DOWNLOAD_DIR = PROJECT_ROOT / "data" / "_backtest_history"

# Mapping: label -> (strategy preset name, output filename)
VARIANTS = {
    "classic":      ("balanced",      "AICI_classic.csv"),
    "conservative": ("conservative",  "AICI_conservative.csv"),
    "risky":        ("aggressive",    "AICI_risky.csv"),
}

# ==========================================================================


def fetch_and_download() -> Path:
    """Fetch top-N symbols from CMC and download daily prices from Binance."""
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

    print(f"  Fetching top {N_TOP_COINS} symbols from CoinMarketCap...")
    symbols = get_top_n_cryptos_cmc(n=N_TOP_COINS)
    if not symbols:
        raise RuntimeError("Failed to fetch symbol list from CoinMarketCap")
    print(f"  Got {len(symbols)} symbols (stablecoins excluded)")

    print(f"  Downloading {len(symbols)} assets from Binance [{DATA_START} .. {END_DATE}]...")
    download_multiple_cryptos(
        symbols,
        start_date=DATA_START,
        end_date=END_DATE,
        data_folder=str(DOWNLOAD_DIR),
    )
    return DOWNLOAD_DIR


def build_merged_prices(data_folder: Path) -> pd.DataFrame:
    """Merge individual CSVs into a single prices DataFrame."""
    df = load_and_preprocess_data_fixed(
        data_folder=str(data_folder),
        dropna_all=True,
        min_history_days=180,
        include_delisted=True,
        allow_internal_gaps=True,
        start_date=DATA_START,
        end_date=END_DATE,
    )
    df.index = pd.to_datetime(df.index)
    df = df.sort_index()
    print(f"  Merged prices: {df.shape[0]} rows x {df.shape[1]} assets  "
          f"[{df.index.min().date()} .. {df.index.max().date()}]")
    return df


def save_equity_csv(equity_series: pd.Series, dest: Path) -> None:
    """Save equity series as date,log_return CSV (same format as performance_refresh)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame({
        "date": pd.to_datetime(equity_series.index).date,
        "log_return": equity_series.values,
    })
    df = df.dropna(subset=["log_return"])
    df = df.drop_duplicates(subset=["date"]).sort_values("date").reset_index(drop=True)
    df.to_csv(dest, index=False, date_format="%Y-%m-%d")
    print(f"  -> Saved {len(df)} rows to {dest}")


def save_benchmark(df_prices: pd.DataFrame, column: str, filename: str) -> None:
    """Save a single-asset benchmark series."""
    if column not in df_prices.columns:
        print(f"  [WARN] Benchmark column '{column}' not found — skipping {filename}")
        return
    series = pd.to_numeric(df_prices[column], errors="coerce").dropna()
    dest = SERIES_DIR / filename
    dest.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame({"date": series.index, "Close": series.values})
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df = df.drop_duplicates(subset=["date"]).sort_values("date").reset_index(drop=True)
    df.to_csv(dest, index=False, date_format="%Y-%m-%d")
    print(f"  -> Benchmark {filename}: {len(df)} rows saved")


def run_variant(label: str, strategy_key: str, filename: str, df_prices: pd.DataFrame) -> None:
    print(f"\n{'='*60}")
    print(f"  Strategy: {label} (preset={strategy_key})")
    print(f"  Params:   {STRATEGY_PRESETS[strategy_key]}")
    print(f"  Output:   {SERIES_DIR / filename}")
    print(f"{'='*60}")

    scratch = SCRATCH_DIR / label
    scratch.mkdir(parents=True, exist_ok=True)

    equity_series, metrics, weights_log, assets_log = simulate_index_over_time(
        df_prices=df_prices.copy(),
        lookback_days=LOOKBACK_DAYS,
        window_size=WINDOW_SIZE,
        forecast_horizon=FORECAST_HORIZON,
        strategy=strategy_key,
        # Disable LSTM — use EWMA only for speed
        strategy_overrides={"advanced_forecast": False},
        save_dir=str(scratch),
    )

    if equity_series is None or (hasattr(equity_series, "empty") and equity_series.empty):
        print(f"  [ERROR] No equity series returned for {label}!")
        return

    print(f"  Metrics: {metrics}")
    save_equity_csv(equity_series, SERIES_DIR / filename)


def main() -> None:
    print("=" * 60)
    print("  AICI Full Backtest — all 3 strategies (EWMA, no LSTM)")
    print(f"  Top coins: {N_TOP_COINS} (CMC)")
    print(f"  Data range:  {DATA_START} .. {END_DATE}")
    print(f"  Lookback: {LOOKBACK_DAYS}d | Window: {WINDOW_SIZE}d | Horizon: {FORECAST_HORIZON}d")
    print("  Effective backtest start: ~2021-01-01")
    print("=" * 60)

    # 1) Fetch symbols from CMC + download from Binance
    print("\n[1/4] Downloading price data...")
    data_folder = fetch_and_download()

    # 2) Merge into single DataFrame
    print("\n[2/4] Merging price histories...")
    df_prices = build_merged_prices(data_folder)

    # 3) Run all 3 strategy variants
    print("\n[3/4] Running backtests...")
    for label, (strategy_key, filename) in VARIANTS.items():
        run_variant(label, strategy_key, filename, df_prices)

    # 4) Save benchmarks
    print("\n[4/4] Saving benchmarks...")
    save_benchmark(df_prices, "BTC", "BTC_USD.csv")
    save_benchmark(df_prices, "ETH", "ETH_USD.csv")

    print("\n" + "=" * 60)
    print("  DONE. Results in:", SERIES_DIR)
    print("=" * 60)


if __name__ == "__main__":
    main()
