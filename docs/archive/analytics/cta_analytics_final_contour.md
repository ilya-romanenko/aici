# Р¤РёРЅР°Р»СЊРЅС‹Р№ РїСЂРѕРґСѓРєС‚РѕРІС‹Р№ РєРѕРЅС‚СѓСЂ CTA-Р°РЅР°Р»РёС‚РёРєРё (РЁР°Рі 12)

Р”Р°С‚Р° С„РёРєСЃР°С†РёРё: 2026-02-25.

Р”РѕРєСѓРјРµРЅС‚ С„РёРєСЃРёСЂСѓРµС‚ РёС‚РѕРіРѕРІС‹Р№ РєРѕРЅС‚СѓСЂ CTA-Р°РЅР°Р»РёС‚РёРєРё Рё С„РёРЅР°Р»СЊРЅС‹Р№ РєРѕРЅС‚СЂР°РєС‚ `frontend/backend/storage` РґР»СЏ СЃР»РµРґСѓСЋС‰РёС… РёС‚РµСЂР°С†РёР№ Р±РµР· РёР·РјРµРЅРµРЅРёСЏ Р±Р°Р·РѕРІРѕР№ СЃС…РµРјС‹.

## 1. РџРѕРґС‚РІРµСЂР¶РґРµРЅРёРµ Р±РёР·РЅРµСЃ-РІРѕРїСЂРѕСЃРѕРІ

| Р‘РёР·РЅРµСЃ-РІРѕРїСЂРѕСЃ | Р“РґРµ РѕС‚РІРµС‚ РІ Р°РґРјРёРЅРєРµ | API-РёСЃС‚РѕС‡РЅРёРє | Р§С‚Рѕ СЃС‡РёС‚Р°С‚СЊ РєР»СЋС‡РµРІС‹Рј |
| --- | --- | --- | --- |
| РљР°РєРёРµ CTA СЂР°Р±РѕС‚Р°СЋС‚ Р»СѓС‡С€Рµ РІСЃРµРіРѕ? | `Top CTA` + `KPI Overview` + `Clicks Dynamics` | `GET /api/v1/admin/cta-analytics/top-cta`, `GET /api/v1/admin/cta-analytics/dashboard/summary`, `GET /api/v1/admin/cta-analytics/timeseries` | РЎСЂР°РІРЅРµРЅРёРµ `cta_id` РїРѕ `total_clicks`, `unique_clicks`, `click_to_signup`, `click_to_paid` РїСЂРё РѕРґРёРЅР°РєРѕРІРѕРј РѕРєРЅРµ Рё С„РёР»СЊС‚СЂР°С…. |
| Р“РґРµ РїРѕС‚РµСЂРё РІРѕСЂРѕРЅРєРё? | `Funnel` + `Location Breakdown` | `GET /api/v1/admin/cta-analytics/funnel`, `GET /api/v1/admin/cta-analytics/breakdown` | РџСЂРѕСЃР°РґРєРё РЅР° С€Р°РіР°С… `click -> signup -> confirmed -> paid` Рё РїРѕ Р»РѕРєР°С†РёСЏРј (`location`). |
| РљР°РєРёРµ РєР°РЅР°Р»С‹ РґР°СЋС‚ РєРѕРЅРІРµСЂСЃРёСЋ? | `Period And Dataset Controls` + РІСЃРµ Р°РЅР°Р»РёС‚РёС‡РµСЃРєРёРµ Р±Р»РѕРєРё | Р›СЋР±РѕР№ `/api/v1/admin/cta-analytics/*` endpoint СЃ С„РёР»СЊС‚СЂРѕРј `traffic_source` | РЎСЂР°РІРЅРµРЅРёРµ РєРѕРЅРІРµСЂСЃРёР№ РїРѕ `traffic_source` (`direct`, `referral`, `utm_source=*`) Рё `attribution_coverage`. |

РџСЂРёРјРµС‡Р°РЅРёРµ РїРѕ РёРЅС‚РµСЂРїСЂРµС‚Р°С†РёРё:
- Р’РѕСЂРѕРЅРєР° СЃС‡РёС‚Р°РµС‚СЃСЏ РЅР° `account:*` Р°РєС‚РѕСЂР°С…, РїРѕСЌС‚РѕРјСѓ `unique_clicks` РјРѕР¶РµС‚ Р±С‹С‚СЊ РІС‹С€Рµ `conversion.click_users`.
- РњРµС‚СЂРёРєР° `attribution_coverage` РѕР±СЏР·Р°С‚РµР»СЊРЅР° РґР»СЏ С‡С‚РµРЅРёСЏ СЂСЏРґРѕРј СЃ РєРѕРЅРІРµСЂСЃРёСЏРјРё.

## 2. РС‚РѕРіРѕРІС‹Р№ РєРѕРЅС‚СЂР°РєС‚ Frontend

### 2.1 РљРѕРЅС‚СЂР°РєС‚ РєР»РёРєР° CTA (РёСЃС‚РѕС‡РЅРёРє РґР°РЅРЅС‹С…)

- Frontend РѕС‚РїСЂР°РІР»СЏРµС‚ `POST /api/v1/events/cta` СЃ payload:
  - `cta_id: string` (`2..120`, РїР°С‚С‚РµСЂРЅ `[A-Za-z0-9_.-]+`)
  - `location: string | null`
  - `href: string | null`
  - `metadata: object | null`
- РЎРµСЂРІРµСЂ РІСЃРµРіРґР° РІРѕР·РІСЂР°С‰Р°РµС‚:
  - `event_id: string`
  - `received_at: datetime(UTC)`

### 2.2 РљРѕРЅС‚СЂР°РєС‚ admin UI С„РёР»СЊС‚СЂРѕРІ

- РЎС‚СЂР°РЅРёС†Р°: `/admin/cta-analytics`
- UI СЃРѕСЃС‚РѕСЏРЅРёРµ СЃРёРЅС…СЂРѕРЅРёР·РёСЂСѓРµС‚СЃСЏ СЃ query-РїР°СЂР°РјРµС‚СЂР°РјРё URL:
  - `lookback_days`, `interval`, `start_at`, `end_at`
  - `placement` (multi)
  - `traffic_source` (multi)
  - `cta_type` (multi)
- UI РґР»СЏ РѕС‚СЂРёСЃРѕРІРєРё РґР°С€Р±РѕСЂРґР° РёСЃРїРѕР»СЊР·СѓРµС‚ С‚РѕР»СЊРєРѕ Р°РіСЂРµРіРёСЂРѕРІР°РЅРЅС‹Рµ admin endpoint-С‹ Рё РЅРµ С‡РёС‚Р°РµС‚ raw-С„Р°Р№Р»С‹ РЅР°РїСЂСЏРјСѓСЋ.

## 3. РС‚РѕРіРѕРІС‹Р№ РєРѕРЅС‚СЂР°РєС‚ Backend

### 3.1 Ingestion

- Endpoint: `POST /api/v1/events/cta`
- РЎРµСЂРІРµСЂРЅС‹Рµ РёРЅРІР°СЂРёР°РЅС‚С‹:
  - РЅРѕСЂРјР°Р»РёР·Р°С†РёСЏ `location`, `href`, `metadata`;
  - РІС‹С‡РёСЃР»РµРЅРёРµ `unique_actor_id` (`account:` -> `session:` -> fingerprint);
  - РґРµРґСѓРї РІ РѕРєРЅРµ 8 СЃРµРєСѓРЅРґ;
  - Р·Р°РїРёСЃСЊ raw + Р·Р°РїРёСЃСЊ normalized analytics + РѕР±РЅРѕРІР»РµРЅРёРµ Р°РіСЂРµРіР°С‚РѕРІ/quality.

### 3.2 Admin analytics API

- Endpoints:
  - `GET /api/v1/admin/cta-analytics/dashboard/summary`
  - `GET /api/v1/admin/cta-analytics/timeseries`
  - `GET /api/v1/admin/cta-analytics/top-cta`
  - `GET /api/v1/admin/cta-analytics/breakdown`
  - `GET /api/v1/admin/cta-analytics/funnel`
  - `GET /api/v1/admin/cta-analytics/export?dataset=summary|timeseries|top_cta|breakdown|funnel`
- РћР±С‰РёРµ С„РёР»СЊС‚СЂС‹:
  - `start_at`, `end_at`, `lookback_days=1..90`
  - `page`, `placement`, `cta_id`, `cta_type`
  - `traffic_source`, `auth_state`, `referrer`, `utm`
- РџР°РіРёРЅР°С†РёСЏ:
  - `timeseries`, `top-cta`, `breakdown`: `page_number`, `page_size`
- `traffic_source` РёРЅС‚РµСЂРїСЂРµС‚РёСЂСѓРµС‚СЃСЏ РµРґРёРЅРѕРѕР±СЂР°Р·РЅРѕ:
  - `direct`: РїСѓСЃС‚С‹Рµ `utm_source` Рё `referer`
  - `referral`: РїСѓСЃС‚РѕР№ `utm_source` Рё РЅРµРїСѓСЃС‚РѕР№ `referer`
  - РёРЅС‹Рµ Р·РЅР°С‡РµРЅРёСЏ: С‚РѕС‡РЅРѕРµ СЃСЂР°РІРЅРµРЅРёРµ СЃ `utm_source`

## 4. РС‚РѕРіРѕРІС‹Р№ РєРѕРЅС‚СЂР°РєС‚ Storage

### 4.1 Raw backup

- `runs/_intake/cta_events.jsonl`: СЃС‹СЂС‹Рµ ingestion-СЃРѕР±С‹С‚РёСЏ.
- `runs/_intake/cta_events_analytics.jsonl`: РЅРѕСЂРјР°Р»РёР·РѕРІР°РЅРЅС‹Рµ analytics-СЃРѕР±С‹С‚РёСЏ РїРѕСЃР»Рµ РґРµРґСѓРїР»РёРєР°С†РёРё.

### 4.2 РђРЅР°Р»РёС‚РёС‡РµСЃРєРѕРµ С…СЂР°РЅРёР»РёС‰Рµ (SQLite)

- `runs/_analytics/cta_analytics.db`
- РљРѕРЅС‚СѓСЂ С‚Р°Р±Р»РёС†:
  - `cta_events_fact`: С„Р°РєС‚-РєР»РёРєРё (drill-down СЃР»РѕР№)
  - `cta_metrics_hourly`: РїРѕС‡Р°СЃРѕРІС‹Рµ Р°РіСЂРµРіР°С‚С‹
  - `cta_metrics_daily`: РґРЅРµРІРЅС‹Рµ Р°РіСЂРµРіР°С‚С‹
  - `cta_ingestion_quality_hourly`: РєР°С‡РµСЃС‚РІРѕ ingestion
  - СЃР»СѓР¶РµР±РЅС‹Рµ С‚Р°Р±Р»РёС†С‹ СѓРЅРёРєР°Р»СЊРЅРѕСЃС‚Рё Рё retention-Р»РѕРіРѕРІ

### 4.3 Retention Рё Р°СЂС…РёРІ

- РђСЂС…РёРІ СЃС‚Р°СЂС‹С… С„Р°РєС‚РѕРІ:
  - `runs/_intake/archive/cta_analytics/<YYYY-MM>/cta_events_fact_<from>_to_<to>_<ts>.jsonl.gz`
- РЈРїСЂР°РІР»СЏСЋС‰РёРµ env:
  - `AICI_CTA_FACT_RETENTION_DAYS`
  - `AICI_CTA_RETENTION_CHECK_SECONDS`
  - `AICI_CTA_RETENTION_BATCH_SIZE`
  - `AICI_CTA_ARCHIVE_FILE_RETENTION_DAYS`

## 5. Р—Р°РјРѕСЂРѕР·РєР° РєРѕРЅС‚СЂР°РєС‚Р° РґР»СЏ СЃР»РµРґСѓСЋС‰РёС… РёС‚РµСЂР°С†РёР№

РљРѕРЅС‚СЂР°РєС‚ РІРµСЂСЃРёРё `v1` СЃС‡РёС‚Р°РµС‚СЃСЏ Р·Р°С„РёРєСЃРёСЂРѕРІР°РЅРЅС‹Рј РЅР° РґР°С‚Сѓ `2026-02-25`:

- РќРµ РјРµРЅСЏРµРј СЃРµРјР°РЅС‚РёРєСѓ СЃСѓС‰РµСЃС‚РІСѓСЋС‰РёС… РїРѕР»РµР№ Рё С„РёР»СЊС‚СЂРѕРІ Р±РµР· РјРёРіСЂР°С†РёРё Рё Р°РїРґРµР№С‚Р° РґРѕРєСѓРјРµРЅС‚Р°С†РёРё.
- Р Р°СЃС€РёСЂРµРЅРёСЏ РґРµР»Р°РµРј С‡РµСЂРµР· additive-РїРѕРґС…РѕРґ:
  - РЅРѕРІС‹Рµ РїРѕР»СЏ response;
  - РЅРѕРІС‹Рµ Р·РЅР°С‡РµРЅРёСЏ `cta_type`/`placement`;
  - РЅРѕРІС‹Рµ dataset РґР»СЏ export.
- Р›СЋР±РѕРµ breaking-РёР·РјРµРЅРµРЅРёРµ С‚СЂРµР±СѓРµС‚:
  - РѕР±РЅРѕРІР»РµРЅРёСЏ `docs/analytics/cta_analytics.md` Рё СЌС‚РѕРіРѕ РґРѕРєСѓРјРµРЅС‚Р°;
  - СЏРІРЅРѕР№ РїРѕРјРµС‚РєРё РІ `docs/roadmap.md` РЅР° С€Р°РіРµ СЃРѕРѕС‚РІРµС‚СЃС‚РІСѓСЋС‰РµР№ РёС‚РµСЂР°С†РёРё.

