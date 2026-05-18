# Контракт CTA-аналитики (Шаги 1-2)

Дата фиксации: 2026-02-24.

Документ фиксирует продуктовые KPI, словарь CTA-событий и контракт данных для ingestion endpoint `/api/v1/events/cta` с целевой нормализованной моделью аналитики поверх JSONL-бэкапа.

## 1. Область действия

- Источник кликов: frontend (`data-cta-id`, `data-cta-location`) на `landing` и `pricing`.
- Ingestion: `POST /api/v1/events/cta`.
- Raw backup: `runs/_intake/cta_events.jsonl`.
- Цель этапа: единый словарь и контракт, без изменений архитектуры хранения на этом шаге.

## 2. Словарь placement и единые `cta_id`

### 2.1 Основные placement

- `header`
- `hero`
- `pricing`
- `api_section`

Нормализация `location`:
- целевое значение для pricing: `pricing`;
- совместимость на переходный период: входящее `"/pricing"` нормализуется в `pricing`.

### 2.2 Канонические `cta_id`

Формат: `<context>_<action>_<placement_or_surface>`.

| Placement | Канонические `cta_id` |
| --- | --- |
| `header` | `landing_start_free_plan_header`, `header_login`, `header_profile` |
| `hero` | `landing_start_free_plan_hero`, `landing_compare_plans_hero`, `hero_profile` |
| `pricing` | `pricing_start_free_plan_topbar`, `pricing_start_free_plan_card`, `pricing_start_free_plan_footer`, `pricing_choose_pro_plan_card`, `pricing_choose_ultra_plan_card`, `pricing_contact_sales_card`, `pricing_sign_in`, `pricing_go_to_profile`, `pricing_open_docs`, `pricing_back_to_site` |
| `api_section` | `landing_start_free_plan_api`, `landing_open_dashboard_api` |

## 3. KPI и правила расчета

Окно расчета по умолчанию: UTC-день, с фильтрами по периодам.

| KPI | Формула | Источник |
| --- | --- | --- |
| `total_clicks` | Количество событий CTA после дедупликации | `cta_events_fact` |
| `unique_clicks` | `count(distinct unique_actor_id)` | `cta_events_fact` |
| `ctr_by_placement` | `unique_clicks(placement) / unique_page_views(page)` | `cta_events_fact` + `analytics page_view` |
| `conversion_to_signup` | `unique_actors_with_signup / unique_actors_with_cta_click` | `cta_events_fact` + `signup_started/signup_completed` |
| `conversion_to_paid` | `unique_actors_with_paid / unique_actors_with_cta_click` | `cta_events_fact` + billing (`checkout_succeeded`/активная подписка) |

Правила атрибуции для конверсий:

- модель: last CTA click;
- lookback window: 7 дней (по умолчанию, configurable);
- приоритет идентификатора: `account_id` -> `metadata.session_id` -> `unique_actor_id`.

## 4. Обязательные атрибуты CTA-события

Ниже перечислены обязательные ключи итоговой записи события (в хранилище/бэкапе). Значение может быть `null`, но ключ должен присутствовать.

| Поле | Тип | Источник |
| --- | --- | --- |
| `cta_id` | `string` | request body |
| `location` | `string` | request body (`data-cta-location`) |
| `href` | `string \| null` | request body или DOM `href` |
| `metadata` | `object` | request body (по умолчанию `{}`) |
| `referer` | `string \| null` | request headers (`referer`/`referrer`) |
| `user_agent` | `string \| null` | request headers (`user-agent`) |
| `received_at` | `datetime(UTC)` | server timestamp |

Рекомендуемые ключи в `metadata` для последующей сегментации:

- `page_path`
- `section`
- `auth_state` (`anonymous`/`authenticated`)
- `utm_source`, `utm_medium`, `utm_campaign`, `utm_content`, `utm_term`
- `plan` (для pricing CTA)
- `session_id` (если доступен на клиенте)

## 5. Контракт ingestion для `/api/v1/events/cta`

### 5.1 Request

- Method: `POST`
- Path: `/api/v1/events/cta`
- Content-Type: `application/json`

Тело запроса (v1):

```json
{
  "cta_id": "landing_start_free_plan_hero",
  "location": "hero",
  "href": "/auth/login",
  "metadata": {
    "page_path": "/",
    "section": "hero",
    "auth_state": "anonymous",
    "utm_source": "google"
  }
}
```

### 5.2 Server-enriched event record

```json
{
  "request_id": "17b70df9f0be4d9fb23f6c4f91c25e2d",
  "cta_id": "landing_start_free_plan_hero",
  "location": "hero",
  "href": "/auth/login",
  "metadata": {
    "page_path": "/",
    "section": "hero",
    "auth_state": "anonymous",
    "utm_source": "google"
  },
  "referer": "https://example.com/",
  "user_agent": "Mozilla/5.0 ...",
  "received_at": "2026-02-24T18:30:12.313291+00:00"
}
```

### 5.3 Response

```json
{
  "event_id": "17b70df9f0be4d9fb23f6c4f91c25e2d",
  "received_at": "2026-02-24T18:30:12.313291+00:00"
}
```

## 6. Нормализованная модель аналитики поверх JSONL

### 6.1 Слои хранения

1. Raw backup (как есть):
- файл: `runs/_intake/cta_events.jsonl`.

2. Нормализованный факт (`cta_events_fact`):
- grain: 1 строка = 1 принятое CTA-событие после дедупликации;
- минимальные поля:
  - `event_id`, `received_at`, `event_date`, `event_hour`
  - `cta_id`, `location`, `location_norm`
  - `page_path`, `section`
  - `href`, `referer`, `user_agent`, `device_type`
  - `auth_state`
  - `utm_source`, `utm_medium`, `utm_campaign`, `utm_content`, `utm_term`
  - `unique_actor_id`
  - `metadata_json`.

3. Агрегации:
- `cta_metrics_hourly` (`event_hour`, `location_norm`, `cta_id`, `device_type`, `auth_state`, клики total/unique);
- `cta_metrics_daily` (`event_date`, те же измерения, клики + CTR);
- `cta_funnel_daily` (`event_date`, `location_norm`, `cta_id`, click -> signup -> paid и конверсии).

### 6.2 Дедупликация

- Ключ дедупликации: `hash(cta_id, location_norm, href, unique_actor_id, time_bucket_8s)`.
- Окно: 8 секунд (синхронизировано с frontend cooldown).

## 7. Поля сегментации

Обязательные измерения аналитики:

- период: `event_date`, `event_hour`;
- страница: `page_path`;
- секция: `section`/`location_norm`;
- устройство: `device_type` (`desktop`, `mobile`, `tablet`, `bot`, `unknown`);
- авторизованность: `auth_state`;
- UTM: `utm_source`, `utm_medium`, `utm_campaign`, `utm_content`, `utm_term`.

Правила извлечения:

- `page_path`: из `metadata.page_path`, fallback из `href`;
- `section`: из `metadata.section`, fallback из `location_norm`;
- `auth_state`: из `metadata.auth_state`, fallback `anonymous`;
- `utm_*`: сначала из `metadata`, затем парсинг query string у `href`/`referer`;
- `device_type`: парсинг `user_agent`.

## 8. Retention And Archive Strategy (Step 5)

The CTA analytics stack now has three persistence layers:

1. Raw intake backup:
- `runs/_intake/cta_events.jsonl` keeps request-level raw payloads.
- `runs/_intake/cta_events_analytics.jsonl` keeps normalized non-duplicate analytics payloads.

2. Persistent analytics DB:
- `runs/_analytics/cta_analytics.db` stores `cta_events_fact`, `cta_metrics_hourly`, `cta_metrics_daily`.
- Hourly and daily metrics are updated online during ingestion.
- Indexes support period and `cta_id` filters for admin charts.

3. Archive for aged fact rows:
- Before deleting old `cta_events_fact` rows, data is exported to
  `runs/_intake/archive/cta_analytics/<YYYY-MM>/cta_events_fact_<from>_to_<to>_<ts>.jsonl.gz`.
- Archive retention can be controlled separately.

Retention policy:
- Fact retention: keep recent rows in `cta_events_fact` for interactive drill-down.
- Aggregates retention: `cta_metrics_hourly` and `cta_metrics_daily` remain the KPI source of truth and are not pruned by fact retention.
- Unique-actor helper tables are pruned for old windows after fact retention cut-off.

Environment controls:
- `AICI_CTA_FACT_RETENTION_DAYS` (default: `90`)
- `AICI_CTA_RETENTION_CHECK_SECONDS` (default: `3600`)
- `AICI_CTA_RETENTION_BATCH_SIZE` (default: `5000`)
- `AICI_CTA_ARCHIVE_FILE_RETENTION_DAYS` (default: `365`)
