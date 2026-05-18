# Live vs Backtest: продуктовые определения и контракт данных

Документ фиксирует единые термины, формат отображения и контракт данных для блока графика и блока прозрачности на лендинге.

## 1. Единые определения

- `Live` — фактическая история индекса после запуска live-режима на реальных рыночных данных.
- `Backtest` — историческая симуляция индекса до начала `Live` по зафиксированной методологии.
- `Live since` — дата начала `Live`-истории (включительно).
- `Backtest window` — интервал дат симуляции `Backtest` (включительно): от `backtest_window_start` до `backtest_window_end`.

## 2. Формат отображения и прозрачность

- Для `Live since`, `Backtest window start`, `Backtest window end` используется единый формат даты: `YYYY-MM-DD`.
- Таймзона для `Live since`: `UTC`.
- Блок прозрачности у графика показывается всегда (не прячется в FAQ).
- Блок прозрачности всегда явно показывает:
  - `fees included` или `fees excluded`;
  - `slippage included` или `slippage excluded`.

## 3. Контракт данных для фронтенда

### 3.1 Поля верхнего уровня

| Поле | Тип | Обязательное | Описание |
| --- | --- | --- | --- |
| `live_start_date` | `string \| null` | да | Дата старта live в формате `YYYY-MM-DD` (UTC). `null`, если live-история недоступна. |
| `backtest_window_start` | `string` | да | Левая граница backtest-окна, `YYYY-MM-DD`. |
| `backtest_window_end` | `string` | да | Правая граница backtest-окна, `YYYY-MM-DD`. |
| `fees_included` | `boolean` | да | Учитываются ли комиссии в показанной доходности. |
| `slippage_included` | `boolean` | да | Учитывается ли проскальзывание в показанной доходности. |
| `has_live_history` | `boolean` | да | Признак доступности live-истории. |
| `is_live_series_short` | `boolean` | да | Признак короткой live-серии. |
| `live_series` | `array` | да | Точки live-серии (может быть пустым массивом, если `has_live_history=false`). |
| `backtest_series` | `array` | да | Точки backtest-серии. |

### 3.2 Формат точки серии

```json
{
  "date": "YYYY-MM-DD",
  "value": 1.0234
}
```

- `date` — дата точки в UTC (дневной срез).
- `value` — значение equity/NAV для этой даты.

### 3.3 Правила целостности и поведения UI

- Если `has_live_history=true`, то `live_start_date` обязательно заполнено.
- Если `has_live_history=false`, то `live_start_date=null`, `live_series=[]`.
- При наличии live-истории выполняется ограничение: `backtest_window_end < live_start_date`.
- Политика короткой live-серии: если `is_live_series_short=true`, интерфейс показывает данные как есть, без маскировки, сглаживания и скрытия.

### 3.4 Пример payload

```json
{
  "live_start_date": "2026-01-15",
  "backtest_window_start": "2021-01-01",
  "backtest_window_end": "2026-01-14",
  "fees_included": true,
  "slippage_included": false,
  "has_live_history": true,
  "is_live_series_short": true,
  "live_series": [
    { "date": "2026-01-15", "value": 1.0 },
    { "date": "2026-01-16", "value": 1.0042 }
  ],
  "backtest_series": [
    { "date": "2021-01-01", "value": 1.0 },
    { "date": "2026-01-14", "value": 1.3821 }
  ]
}
```

## 4. Источники данных и границы окон (Шаг 3)

- Источник `Backtest`: `src/ai_crypto_index/frontend/static/results_performance/AICI_classic.csv` (поле `log_return`), приводится к equity/NAV с базой `1.0`.
- Источник `Live`: последний валидный auto-run `runs/auto-classic-*/equity_curve.csv` (берём только даты строго позже `backtest_window_end`).
- `live_start_date` определяется как первая доступная дата в `live_series` после фильтрации.
- `backtest_window_start` и `backtest_window_end` определяются по фактически доступным границам исторического файла backtest.
- Единый расчётный базис для сравнения `Live` и `Backtest`:
  - частота: `1d`;
  - валюта: `USD`;
  - timestamp policy: `UTC daily close`.

## 5. Monthly Current Index Composition (Шаг 4)

- Хранилище monthly snapshots: `runs/_index_composition/monthly_snapshots.json`.
- Базовая запись snapshot:
  - `month` (`YYYY-MM`);
  - `asset`;
  - `weight`;
  - `source`.
- Для каждого месяца сохраняется состав из последнего доступного run этого месяца (по `mtime`).
- Текущий состав (`Current index composition`) относится к последнему месяцу в snapshots.
- Разделение snapshots на `live` и `backtest` режимы выполняется относительно `live_start_date`:
  - `month >= live_start_month` → live snapshot;
  - `month < live_start_month` → backtest snapshot.

## 6. End-to-End Behavior (Step 9)

This section fixes the final user-visible scenario from page load to feature interpretation.

### 6.1 Rendering order on landing page

Inside the `Live vs Backtest performance` section, UI is rendered in this strict order:

1. Transparency block:
   - `Live since`
   - `Backtest window`
   - `Fees assumption`
   - `Slippage assumption`
   - `Calculation basis`
2. Performance area:
   - equity curve chart
   - mode switch (`Backtest` / `Live`)
3. Monthly composition area in the same performance context:
   - mode label (`Live` or `Backtest`)
   - month selector (`YYYY-MM`)
   - composition table for selected month

### 6.2 Mode coupling between performance and composition

- Performance mode changes emit browser event: `performance:mode-change`.
- Monthly composition listens to `performance:mode-change` and switches data source:
  - `live` mode -> `monthly_live_snapshots`
  - `backtest` mode -> `monthly_backtest_snapshots`
- If `live` mode is not available, UI stays in `backtest` mode without hiding data.

### 6.3 Interpretation contract for users

Users evaluate index behavior on two synchronized axes:

- return dynamics from the equity curve (`live_series` or `backtest_series`);
- allocation dynamics from monthly composition snapshots for the same mode.

This prevents comparing a live curve with backtest composition (and vice versa) in the same UI state.

### 6.4 Verification anchors

- Template anchors: `data-performance-live-since`, `data-performance-backtest-window`,
  `data-performance-fees`, `data-performance-slippage`, `data-performance-chart`,
  `data-performance-composition-root`.
- Smoke coverage is fixed in `tests/frontend/test_smoke.py` to validate:
  - sequential rendering order for transparency -> performance -> composition;
  - event-based mode synchronization between chart context and monthly composition table.

## 7. Final Product Contour After Hardening (Step 10)

This section is the authoritative state for the monthly rollover behavior.

### 7.1 Monthly rollover on landing page

- At month rollover, chart and composition are rebuilt from new monthly live run artifacts for the same strategy.
- The chart uses a continuous series per strategy:
  - `continuous_series = backtest_series (before live_start_date) + live_series (from live_start_date)`.
- Monthly composition uses the same active strategy as the performance block and the same monthly artifact boundary.
- Strategy switch is synchronized via `performance:strategy-change`; composition updates immediately for the selected strategy.
- Composition month selector (`YYYY-MM`) switches snapshot rows inside the active strategy only and never mixes rows from another strategy.

### 7.2 Backend/Frontend contract alignment

- Backend returns strategy-scoped payloads:
  - `live_backtest_by_strategy`
  - `monthly_snapshots_by_strategy`
  - `monthly_live_snapshots_by_strategy`
  - `monthly_backtest_snapshots_by_strategy`
  - `monthly_snapshots_current_month_by_strategy`
- Frontend reads these strategy-scoped maps and resolves render state from the active strategy key.
- `aggressive` keeps alias compatibility with `risky`, but data source remains strategy-scoped.

### 7.3 Storage alignment

- Backtest performance series are read from persistent store `runs/_performance/series` with read-only fallback to bundled static files.
- Monthly live artifacts are read from `runs/<auto-prefix-*>/*` where prefixes are strategy specific:
  - `auto-classic`
  - `auto-conservative`
  - `auto-aggressive`
- Monthly snapshot builders do not fallback to unrelated run prefixes; missing strategy data remains empty for that strategy.
