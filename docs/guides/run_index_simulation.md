# Гайд: запуск AI Crypto Index на чистом ноутбуке

Документ описывает полный цикл подготовки Windows‑ноутбука «с нуля» к локальному запуску пайплайна симуляции индекса: от установки инструментов до проверки результатов.

## 1. Минимальные требования

- **ОС:** Windows 11/10 (64‑bit) с актуальными обновлениями.
- **Аппаратно:** ≥16 ГБ RAM, ≥25 ГБ свободного места на SSD, стабильный интернет.
- **Аккаунты:** GitHub (для клонирования по HTTPS) и CoinMarketCap (для собственного API‑ключа; в репозитории есть тестовый, но лучше заменить на личный).

## 2. Установка системных инструментов

1. Скачайте [Python 3.10.x 64‑bit](https://www.python.org/downloads/windows/) и при установке отметьте «Add Python to PATH».
2. Установите [Git for Windows](https://git-scm.com/download/win) с опцией «Git from the command line and also from 3rd-party software».
3. Для корректной работы TensorFlow установите [Microsoft Visual C++ Redistributable 2015–2022](https://aka.ms/vs/17/release/vc_redist.x64.exe). Если планируется GPU‑ускорение, дополнительно поставьте CUDA Toolkit и cuDNN, совместимые с TensorFlow 2.18.
4. Один раз разрешите запуск PowerShell‑скриптов (если политика запрещает активацию venv):

   ```powershell
   Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
   ```

## 3. Клонирование репозитория

Откройте «Windows Terminal» (PowerShell) и выполните:

```powershell
cd C:\Work                # либо другая папка для проектов
git clone https://github.com/ilya-romanenko/AI-Powered_Crypto_Index.git AICI
cd AICI
```

> Если доступ по SSH уже настроен, можно использовать `git clone git@github.com:...`.

## 4. Создание и активация виртуального окружения

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip setuptools wheel
```

Каждый раз перед работой активируйте окружение повторно (`.\.venv\Scripts\Activate.ps1`).

## 5. Установка зависимостей

Проект оформлен как пакет `ai-crypto-index`. Для полного набора библиотек выполните:

```powershell
pip install -e ".[dev]"
pip install -r requirements.txt
pip install ccxt tqdm
```

- `pip install -e ".[dev]"` подтянет зависимости из `pyproject.toml` (в том числе FastAPI‑сервис).
- `requirements.txt` добавляет аналитику (`yfinance`, `requests`).
- `ccxt` и `tqdm` используются скачивателем данных и симулятором, но пока не перечислены в `pyproject.toml`.

Проверьте версии:

```powershell
python -c "import tensorflow, pandas, sklearn, ccxt; print(tensorflow.__version__)"
```

## 6. Настройка конфигурации пайплайна

Ключевой файл — `config/pipeline.json`:

```json
{
  "data": {
    "root": "data",
    "min_history_days": 365,
    "allow_internal_gaps": false,
    "include_delisted": false,
    "dropna_all": true
  },
  "market_data": {
    "provider": "binance",
    "top_n": 50,
    "start_date": "2021-01-01",
    "fresh_download": false
  },
  "runs": {
    "root": "runs",
    "expected_files": [
      "weights.csv",
      "perf.json",
      "equity_curve.csv",
      "log.txt"
    ]
  }
}
```

- `data.root` — место, где сохраняются CSV‑файлы с котировками.
- `market_data.fresh_download = true` заставит пайплайн скачать данные заново (при первом запуске это удобно).
- `top_n` управляет числом активов, подаваемых в отбор.

При необходимости замените встроенный API‑ключ CoinMarketCap (`src/ai_crypto_index/fetch_data/data_preprocessing/load_top_n_auto.py`) на свой и сохраните его в переменной окружения; пример:

```powershell
$env:CMC_API_KEY = "ваш-ключ"
```

и отредактируйте модуль, чтобы брать ключ из `os.environ["CMC_API_KEY"]`.

## 7. Первичная загрузка данных и симуляция

### 7.1 Подготовка данных

```powershell
python -m ai_crypto_index.pipelines.main
```

Скрипт:

- считывает `config/pipeline.json`;
- при необходимости скачивает котировки с Binance через `ccxt`;
- обучает модели и сохраняет артефакты в `runs/<timestamp>/`.

По умолчанию используется `fresh_data=True`, поэтому для повторных прогона создайте резервную копию данных или установите `fresh_data=false`.

### 7.2 Длительная симуляция по историческим данным

1. Убедитесь, что после шага 7.1 сформирован `data/merged_prices.csv`.
2. Запустите бэктест:

   ```powershell
   python src\ai_crypto_index\pipelines\backtesting\simulate_index.py `
       --save-dir Results_Backup\fresh_run `
       --strategy conservative
   ```

   У скрипта есть аргументы (см. `simulate_index.py`): `--lookback-days`, `--forecast-horizon`, `--resume`, `--end-date` и т.д. При запуске без флагов используются параметры консервативной стратегии.

### 7.3 Быстрый smoke-тест

```powershell
pytest tests\test_quick_ab_smoke.py -q
```

Этот тест подтверждает, что основная связка пайплайна не ломается после установки.

## 8. Где искать результаты

- `runs/<YYYY-MM-DDThh-mm-ss>/` — веса (`weights.csv`), метрики (`perf.json`), граф (`equity_curve.csv`), лог (`log.txt`).
- `results/` или папка из `--save-dir` — накопленные equity curves и веса из длительной симуляции.
- `results/*.png` — сохранённый график капитала.

## 9. Регулярное обновление

1. Активируйте окружение.
2. Обновите код и зависимости:

   ```powershell
   git pull
   pip install -e ".[dev]"
   pip install -r requirements.txt
   ```

3. Для ежемесячного обновления индекса вызовите:

   ```powershell
   python - <<'PY'
   from ai_crypto_index.pipelines.main import run_monthly_update
   run_monthly_update(
       n_top_coins=100,
       lookback_days=240,
       window_size=30,
       forecast_horizon=30,
       fresh_data=False,
       info_messages=True,
   )
   PY
   ```

4. Заархивируйте новый каталог `runs/<timestamp>/` или выгрузите веса/метрики в рабочие системы.

## 10. Диагностика и типичные проблемы

- **TensorFlow не устанавливается** — убедитесь, что версия Python 3.10 и установлены VC++ Redistributable. Для ноутбуков без GPU используйте `pip install tensorflow-cpu`.
- **Пайплайн не находит CoinMarketCap** — замените API‑ключ и проверьте, что фаерволл не режет HTTPS‑запросы.
- **Недостаточно данных для LSTM** — уменьшите `total_assets` или увеличьте `start_date`, чтобы расширить историю.
- **Выходные csv пустые** — проверьте лог (`runs/.../log.txt`) и `data/merged_prices.csv` на наличие пропусков; при необходимости установите `market_data.allow_internal_gaps = true`.

## 11. Следующие шаги

- Интегрируйте результаты (веса, метрики) в сервис из каталога `src/ai_crypto_index/api`.
- Настройте планировщик (Windows Task Scheduler) для периодического запуска скрипта обновления.
- Подготовьте собственный гайд по обновлению данных на основе наблюдений в проде.

