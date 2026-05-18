# Продуктовая аналитика CTA: словарь, dashboard и источники данных (Шаг 11)

Дата обновления: 2026-02-25.

Этот документ фиксирует текущее состояние CTA-аналитики в продукте:
- словарь событий и значения ключевых `cta_id`;
- структуру admin-dashboard, KPI и правила интерпретации конверсий;
- где хранится raw backup и где смотреть агрегированную статистику.

## 1. Словарь событий и ключевые `cta_id`

### 1.1 События, участвующие в CTA-аналитике

| Событие | Где формируется | Что означает | Основные поля |
| --- | --- | --- | --- |
| `cta_click` | `POST /api/v1/events/cta` | Клик по CTA на `landing`, `pricing`, `docs` | `cta_id`, `location`, `href`, `metadata`, `referer`, `user_agent`, `received_at` |
| `signup_completed` (derived) | `accounts.account.created_at` | Аккаунт создан после CTA | `account_id`, `created_at` |
| `email_confirmed` (derived) | `accounts.account.email_verified_at` | Email подтвержден после CTA | `account_id`, `email_verified_at` |
| `paid` (derived) | Billing доменные таблицы | Пользователь дошел до платной стадии | `account_id`, timestamp оплаты/активации |

Источники для стадии `paid`:
- `billing_events.event_type` в множестве: `checkout.session.completed`, `invoice.payment_succeeded`, `crypto.payment.confirmed`, `crypto.activation.notified`;
- `billing_crypto_payments.status = confirmed`;
- `billing_subscriptions.plan_code != free` и `status in (trialing, active, past_due)`.

### 1.2 Значения `location`

Ключевые значения `location` (после нормализации):
- `header`
- `hero`
- `pricing`
- `api_section`
- `docs`

Сервис также поддерживает алиасы нормализации (например, `"/pricing"` -> `pricing`, `hero-section` -> `hero`).

### 1.3 Ключевые `cta_id`

#### `header`
- `landing_start_free_plan_header` - основной CTA в хедере лендинга.
- `header_login` - переход в логин из хедера.
- `header_profile` - переход в профиль из хедера для авторизованного пользователя.

#### `hero`
- `landing_start_free_plan_hero` - основной CTA hero.
- `landing_compare_plans_hero` - переход на pricing из hero.
- `hero_profile` - переход в профиль из hero для авторизованного пользователя.

#### `api_section`
- `landing_start_free_plan_api` - CTA "start free" в API-блоке лендинга.
- `landing_open_dashboard_api` - CTA перехода в дашборд из API-блока.

#### `pricing`
- Topbar: `pricing_back_to_site`, `pricing_open_docs`, `pricing_go_to_profile`, `pricing_sign_in`, `pricing_start_free_plan_topbar`.
- Plan cards: `pricing_start_free_plan_card`, `pricing_choose_pro_plan_card`, `pricing_choose_ultra_plan_card`, `pricing_contact_sales_card`.
- Footer: `pricing_back_to_site_footer`, `pricing_start_free_plan_footer`.

#### `docs`
- Topbar: `docs_back_to_site_topbar`, `docs_open_dashboard_topbar`.
- SDK actions: `docs_download_sdk_python_examples`, `docs_download_sdk_js_examples`.
- Footer: `docs_contact_sales_footer`, `docs_back_to_site_footer`, `docs_open_dashboard_footer`.

## 2. Admin-Dashboard: структура, KPI и API

### 2.1 Где находится dashboard

- UI: `/admin/cta-analytics`
- Доступ: Basic Auth админ-панели (`AICI_ADMIN_ENABLED=1` + admin credentials)

### 2.2 Блоки интерфейса

1. `Period And Dataset Controls`
- Пресеты периода: `24h`, `7d`, `30d`, `90d`
- Кастомный диапазон: `start_at`, `end_at`
- Интервал графика: `day` / `hour`
- Фильтры UI: `placement`, `traffic_source`, `cta_type`

2. `KPI Overview`
- `Total Clicks`
- `Unique Clicks`
- `Unique Users`
- `Attribution Coverage`
- Отдельно: метрики наблюдаемости (`Missing Data Slots`, `Invalid Events`, `Aggregation Lag`)
- Service state: `Last Accepted Event`, `Last Aggregated Slot`

3. `Clicks Dynamics`
- Time-series по кликам (`day` или `hour` bucket)

4. `Top CTA`
- Таблица лидирующих `cta_id` + конверсии

5. `Location Breakdown`
- Срез по `location` с конверсиями

6. `Funnel`
- Воронка: `CTA Click -> Signup -> Confirmed -> Paid`

### 2.3 KPI и формулы

- `total_clicks`: число записей в `cta_events_fact` после дедупликации.
- `unique_clicks`: `count(distinct unique_actor_id)`.
- `unique_users`: уникальные `unique_actor_id` c префиксом `account:`.
- `unique_sessions`: уникальные `unique_actor_id` c префиксом `session:`.
- `unique_anonymous`: уникальные `unique_actor_id`, не попавшие в `account:` и `session:`.
- `attribution_coverage`: `conversion.click_users / unique_clicks`.

Поля воронки `conversion`:
- `click_users`, `signup_users`, `confirmed_users`, `paid_users`
- `click_to_signup`, `click_to_confirmed`, `signup_to_confirmed`, `confirmed_to_paid`, `signup_to_paid`, `click_to_paid`

### 2.4 Admin API (агрегированная выдача)

- `GET /api/v1/admin/cta-analytics/dashboard/summary`
- `GET /api/v1/admin/cta-analytics/timeseries`
- `GET /api/v1/admin/cta-analytics/top-cta`
- `GET /api/v1/admin/cta-analytics/breakdown`
- `GET /api/v1/admin/cta-analytics/funnel`
- `GET /api/v1/admin/cta-analytics/export?dataset=summary|timeseries|top_cta|breakdown|funnel`

Поддерживаемые фильтры API:
- `start_at`, `end_at`, `lookback_days`
- `page`, `placement`, `cta_id`, `cta_type`
- `traffic_source`, `auth_state`, `referrer`, `utm`

## 3. Правила интерпретации конверсий

### 3.1 Атрибуция и окно конверсии

- Модель атрибуции: last CTA click для аккаунта внутри выбранного среза.
- `lookback_days` по умолчанию `7` (допустимо `1..90`).
- Событие учитывается только если находится в окне:
  - `click_at <= signup_at <= click_at + lookback_window`
  - `click_at <= confirmed_at <= click_at + lookback_window`
  - `click_at <= paid_at <= click_at + lookback_window`

### 3.2 Порядок стадий

- `confirmed_users` считается только если `confirmed_at >= signup_at` (когда обе даты есть).
- `paid_users` считается только если `paid_at >= confirmed_at` (когда обе даты есть).

### 3.3 Важное ограничение по знаменателю

Конверсии строятся только на `account:*` акторах (тех, кого можно связать с аккаунтом).

Из-за этого:
- `unique_clicks` может быть существенно больше `click_users` (там есть `session:*` и fingerprint/anonymous);
- `attribution_coverage` показывает долю кликов, по которым у нас вообще есть надежная пользовательская атрибуция для воронки.

### 3.4 Интерпретация `traffic_source`

- `direct`: пустые `utm_source` и `referer`
- `referral`: пустой `utm_source`, но непустой `referer`
- любое другое значение: точное сравнение с `utm_source`

## 4. Где лежат raw backup и агрегаты

Пути указаны относительно `runs_root`.

### 4.1 Raw backup

- `runs/_intake/cta_events.jsonl` - сырой intake payload (как пришло с endpoint).
- `runs/_intake/cta_events_analytics.jsonl` - нормализованный и недублированный аналитический payload.

### 4.2 Агрегированная статистика и факт-слой

- `runs/_analytics/cta_analytics.db` (SQLite)

Основные таблицы:
- `cta_events_fact` - факт-клики (drill-down слой).
- `cta_metrics_hourly` - агрегаты по часам.
- `cta_metrics_daily` - агрегаты по дням.
- `cta_ingestion_quality_hourly` - метрики качества ingestion.

### 4.3 Архив старых факт-данных

- `runs/_intake/archive/cta_analytics/<YYYY-MM>/cta_events_fact_<from>_to_<to>_<ts>.jsonl.gz`

Архивация управляется env-параметрами:
- `AICI_CTA_FACT_RETENTION_DAYS`
- `AICI_CTA_RETENTION_CHECK_SECONDS`
- `AICI_CTA_RETENTION_BATCH_SIZE`
- `AICI_CTA_ARCHIVE_FILE_RETENTION_DAYS`

