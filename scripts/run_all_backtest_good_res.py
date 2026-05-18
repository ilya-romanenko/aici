import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

import pandas as pd
from ai_crypto_index.pipelines.backtesting.simulate_index import simulate_index_over_time

# ============================================================
# КОНФИГУРАЦИЯ БЕКТЕСТА
# ============================================================

# --- Выбор версии пайплайна ---
# "v2" → main.py    (актуальная версия, с forced-majors, liquidity filter и др.)
# "v1" → main_v1.py (устаревшая версия, без liquidity/universe фильтров)
PIPELINE_VERSION = "v2"

# --- Выбор ядра прогнозирования ---
# "ewma" — EWMA-прогноз волатильности (быстро, без нейросетей, рекомендуется для бектеста)
# "lstm" — LSTM нейросеть (медленно, ~10-30x дольше, точнее)
FORECAST_ENGINE = "ewma"

# --- Список стратегий для запуска ---
# Доступные: "aggressive", "balanced", "conservative"
STRATEGIES = ["aggressive", "balanced", "conservative"]

# --- Данные для бектеста ---
# Папка, в которой хранятся индивидуальные CSV и merged_prices.csv для бектеста
BACKTEST_DATA_DIR = "backtest_data"

# True  — удалить старый merged_prices.csv и перезагрузить данные с нуля
# False — использовать существующий merged_prices.csv (если есть), иначе загрузить автоматически
RELOAD_DATA = False

# Диапазон дат для загрузки данных
DATA_START_DATE = "2024-08-28"
DATA_END_DATE = datetime.now().strftime("%Y-%m-%d")  # сегодня по дефолту

# Количество топ-монет для загрузки (через CoinMarketCap)
TOP_N_COINS = 100

# ============================================================

_ENGINE_TO_ADVANCED = {
    "ewma": False,
    "lstm": True,
}

if FORECAST_ENGINE not in _ENGINE_TO_ADVANCED:
    raise ValueError(f"Unknown FORECAST_ENGINE '{FORECAST_ENGINE}'. Use 'ewma' or 'lstm'.")

advanced_forecast = _ENGINE_TO_ADVANCED[FORECAST_ENGINE]


def _build_merged_prices(data_dir: str, start_date: str, end_date: str, top_n: int) -> pd.DataFrame:
    """Скачать монеты и собрать merged_prices.csv в data_dir."""
    from ai_crypto_index.fetch_data.data_collection import download_multiple_cryptos
    from ai_crypto_index.fetch_data.data_preprocessing.load_and_preprocess import load_and_preprocess_data_fixed
    from ai_crypto_index.fetch_data.data_preprocessing.load_top_n_auto import get_top_n_cryptos_cmc

    os.makedirs(data_dir, exist_ok=True)

    print(f"\n[DATA] Получаем топ-{top_n} монет с CoinMarketCap...")
    symbols = get_top_n_cryptos_cmc(n=top_n)
    if not symbols:
        raise RuntimeError("Не удалось получить список монет с CoinMarketCap.")
    print(f"[DATA] Найдено {len(symbols)} монет: {symbols[:5]}...")

    print(f"[DATA] Скачиваем данные {start_date} → {end_date} в {data_dir}/")
    download_multiple_cryptos(symbols, start_date, end_date, data_folder=data_dir)

    print("[DATA] Собираем merged_prices.csv...")
    merged = load_and_preprocess_data_fixed(
        data_folder=data_dir,
        dropna_all=True,
        min_history_days=180,
        start_date=start_date,
        end_date=end_date,
        include_delisted=True,
        allow_internal_gaps=True,
    )

    merged_path = os.path.join(data_dir, "merged_prices.csv")
    merged.to_csv(merged_path)
    print(f"[DATA] Сохранено: {merged_path} ({merged.shape[0]} дней, {merged.shape[1]} монет)")
    return merged


# --- Загрузка / построение данных ---
merged_path = os.path.join(BACKTEST_DATA_DIR, "merged_prices.csv")

if RELOAD_DATA:
    print("[DATA] RELOAD_DATA=True — перезагружаем данные...")
    if os.path.exists(merged_path):
        os.remove(merged_path)
        print(f"[DATA] Удалён старый {merged_path}")
    df_prices = _build_merged_prices(BACKTEST_DATA_DIR, DATA_START_DATE, DATA_END_DATE, TOP_N_COINS)
elif os.path.exists(merged_path):
    print(f"[DATA] Используем существующий {merged_path}")
    df_prices = pd.read_csv(merged_path, index_col=0, parse_dates=True)
    print(f"[DATA] Загружено: {df_prices.shape[0]} дней, {df_prices.shape[1]} монет")
else:
    print(f"[DATA] {merged_path} не найден — загружаем автоматически...")
    df_prices = _build_merged_prices(BACKTEST_DATA_DIR, DATA_START_DATE, DATA_END_DATE, TOP_N_COINS)

# --- Запуск бектестов ---
run_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

for strategy in STRATEGIES:
    # Имя папки: backtest_{версия_пайплайна}_{стратегия}_{дата_время}
    # Пример: results/backtest_v2_aggressive_20240315_143022
    run_name = f"backtest_{PIPELINE_VERSION}_{strategy}_{run_timestamp}"
    save_dir = f"results/{run_name}"

    print(f"\n{'='*60}")
    print(f"Strategy : {strategy}")
    print(f"Pipeline : {PIPELINE_VERSION}")
    print(f"Engine   : {FORECAST_ENGINE} (advanced_forecast={advanced_forecast})")
    print(f"Data     : {DATA_START_DATE} → {DATA_END_DATE}")
    print(f"Save dir : {save_dir}")
    print(f"{'='*60}")

    equity, metrics, weights, assets = simulate_index_over_time(
        df_prices,
        resume=False,
        save_dir=save_dir,
        log_path="simulation.log",
        strategy=strategy,
        advanced_forecast=advanced_forecast,
        pipeline_version=PIPELINE_VERSION,
    )

    print(f"\n=== {strategy.upper()} [{PIPELINE_VERSION}] Performance ===")
    for k, v in metrics.items():
        print(f"  {k}: {v:.4f}")
