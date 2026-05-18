# Billing & Subscription Flows

Этот документ описывает, как устроен биллинг AICI: какие тарифы существуют, как они связаны со Stripe Billing, и как работает self‑serve подписка через API.

## Тарифы и Price ID

Базовая линейка подписок:

| Plan       | Price ID (env)                 | Trial | Limits                                                              | Notes                                              |
|------------|--------------------------------|-------|---------------------------------------------------------------------|----------------------------------------------------|
| Free       | `AICI_STRIPE_PRICE_FREE`       | 0 d   | ≈500 токенов/мес, T+1 latency, урезанные параметры, только UI      | Жёсткий rate‑limit, без API‑ключей, $0             |
| Pro        | `AICI_STRIPE_PRICE_PRO`        | 14 d  | ≈10k токенов/мес, полный доступ к параметрам                       | Self‑serve checkout, базовые e‑mail отчёты         |
| Ultra      | `AICI_STRIPE_PRICE_ULTRA`      | 14 d  | ≈100k токенов/мес, приоритетный rate‑limit, расширенная история    | Для активных пользователей, API + экспорт          |
| Enterprise | `AICI_STRIPE_PRICE_ENTERPRISE` | 30 d  | Лимиты и параметры по договорённости, выделенный endpoint, SLA      | Заключается через менеджера / ручной инвойс        |

Price ID для каждого плана задаётся через переменные окружения (см. `docs/guides/environment_variables.md`) и дублируется в `config/pipeline.json` в секции `billing.plans`. Stripe‑часть берётся из env, поэтому изменение price_id в Stripe требует обновления env, а не кода.

Фактические квоты по API‑ключам (daily/monthly, burst‑лимиты и задержка данных) настраиваются отдельно в `config/pipeline.json` в секции `api_keys.plans` и должны быть согласованы с концепцией Free/Pro/Ultra/Enterprise.

## Настройка Stripe

1. В Stripe Dashboard создаются Products/Prices, их ID выносятся в `.env` через `AICI_STRIPE_PRICE_*`.
2. В Billing Portal настроен `Return URL` на `https://aici.pro/dashboard?billing=portal`.
3. Включён веб‑хук на endpoint  
   `POST https://<host>/api/v1/billing/webhook/stripe`  
   с событиями:
   - `checkout.session.completed`
   - `customer.subscription.created`
   - `customer.subscription.updated`
   - `customer.subscription.deleted`
   - `invoice.payment_succeeded`
   - `invoice.payment_failed`
4. Секреты (`AICI_STRIPE_SECRET_KEY`, `AICI_STRIPE_WEBHOOK_SECRET`, `AICI_STRIPE_PUBLISHABLE_KEY`) задаются в `.env` в группе "Billing / Stripe".

## Сценарии self‑serve подписки (Free / Pro / Ultra)

Self‑serve checkout поддерживает все публичные тарифы, кроме Enterprise.

1. Клиент из dashboard вызывает `POST /api/v1/billing/checkout` с `plan_code` (`free`, `pro`, `ultra`).
2. FastAPI слой через `BillingService.create_checkout_session` создаёт Stripe Checkout Session и возвращает URL для редиректа.
3. После оплаты Stripe шлёт веб‑хуки, `BillingService.process_stripe_webhook`:
   - создаёт/обновляет записи в `billing_customers` и `billing_subscriptions`,
   - пишет сырые события в `_intake/billing_events.jsonl`,
   - обновляет `subscription.plan_code` и `subscription.status` для аккаунта.
4. `GET /api/v1/billing/status` отдаёт агрегированную информацию по активной подписке; аналогичный блок возвращается в `GET /api/v1/auth/me`.
5. Для управления подпиской из UI есть кнопка "Manage plan", которая вызывает `POST /api/v1/billing/portal` и возвращает URL Stripe Billing Portal.

## Модель данных и аудит

- Таблицы `billing_customers`, `billing_subscriptions`, `billing_events` живут в auth‑БД (Postgres), см. `ai_crypto_index/accounts/models.py`.
- Статусы подписки (`trialing`, `active`, `past_due`, `canceled`) синхронизируются из веб‑хуков и складываются в `_intake/billing_events.jsonl` для дальнейшего экспорта в BI/CRM.
- `AccountService.build_profile` добавляет к профилю пользователя блок `subscription`, который используется фронтом для отображения текущего плана и его статуса.

## Enterprise‑сценарии

Enterprise‑клиенты не оформляют подписку через self‑serve checkout; для них используются админ‑эндпоинты:

1. `POST /api/v1/admin/billing/{account_id}/enterprise/invoice` (Basic Auth) — создаёт invoice в Stripe с `collection_method=send_invoice` и отправкой счёта клиенту.
2. `POST /api/v1/admin/billing/{account_id}/enterprise/extend` — ручное продление enterprise‑подписки (сдвиг `current_period_end`, установка `status=active`).

Параметр `AICI_BILLING_ENTERPRISE_TERMS_DAYS` управляет `days_until_due` для enterprise‑инвойсов. Все операции также логируются в `_intake/billing_events.jsonl`.

## Локальное тестирование

1. Установить `AICI_DEV=1` и запустить Stripe CLI:  
   `stripe listen --forward-to localhost:8000/api/v1/billing/webhook/stripe`.
2. Получить JWT и дернуть checkout:  
   `curl -H "Authorization: Bearer <jwt>" https://localhost:8000/api/v1/billing/checkout -d '{"plan_code":"pro"}'`.
3. Пройти Hosted Checkout, проверить, что:
   - в `billing_subscriptions` появилась корректная запись,
   - в `_intake/billing_events.jsonl` есть события Stripe.
4. Протестировать admin‑эндпоинт для enterprise‑инвойса и убедиться, что счёт появился в Stripe Dashboard.

