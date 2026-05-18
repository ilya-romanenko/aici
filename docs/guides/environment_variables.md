# Environment Variables Reference

Все переменные окружения, которые использует стек AICI, сгруппированы по смыслу. Значения по умолчанию указаны только для ориентира — при деплое их можно переопределять через `.env`, секреты CI/CD или параметры контейнера.

---

## Базовая конфигурация
- `AI_CRYPTO_CONFIG` (`config/pipeline.json`) — путь до JSON-конфига, где описаны данные и директории запусков. Относительные пути считаются от корня репозитория/образа.
- `AICI_DEV` (`0`) — включает dev-режим (горячая перезагрузка Uvicorn, подробные логи). В продакшене держим `0`.
- `AICI_LOG_LEVEL` (`INFO`) — уровень логирования FastAPI/бэкенда.
- `AICI_ALLOWED_ORIGINS` (`*`) — список origin’ов через запятую для CORS. Используй конкретные хосты в бою.

## Поведение API и пайплайна
- `AICI_ENABLE_PIPELINE` (`1`) — разрешает эндпоинту `/api/v1/run` запускать heavy-пайплайн. Ставь `0`, если запуском занимаются воркеры отдельно.
- `AICI_RUN_PIPELINE_ON_START` (`auto`) — управляет bootstrap-запуском при старте контейнера: `auto` (только при отсутствии готовых run’ов), `always`, `never`.
- `AICI_FAIL_ON_BOOTSTRAP_ERROR` (`0`) — если `1`, контейнер падает при неудачном bootstrap-run’е.
- `AICI_HOST` (`0.0.0.0`) и `AICI_PORT` (`8000`) — биндинг Uvicorn внутри контейнера.
- `AICI_UVICORN_WORKERS` (пусто) — количество воркеров; не задавай в dev с `--reload`.
- `AICI_UVICORN_RELOAD` (`0`) — форсирует `--reload`, если нужно горячее обновление вне dev-режима.
- `AICI_RATE_LIMIT` (`120`) и `AICI_RATE_LIMIT_WINDOW` (`60`) — лимит запросов и окно (секунд) для `RateLimitMiddleware`.

## Swagger / OpenAPI
- `AICI_SWAGGER_ENABLED` (`0`) — включает публикацию `/api/docs` и `/api/openapi.json`.
- `AICI_SWAGGER_USERNAME` / `AICI_SWAGGER_PASSWORD` — базовая аутентификация для Swagger. Обязательны, если `AICI_SWAGGER_ENABLED=1`.
- `AICI_SWAGGER_DOCS_URL` (`/docs`) — маршрут для UI.
- `AICI_SWAGGER_OPENAPI_URL` (`/openapi.json`) — маршрут для JSON-схемы.

## Статика и CDN
- `AICI_STATIC_CDN_BASE_URL` — базовый HTTPS-URL CDN/реверс-прокси. Когда задан, `url_for('static', ...)` переписывается на CDN c коротким hash-квери.
- `AICI_ASSET_MANIFEST_PATH` — явный путь к `asset-manifest.json`, если файл не в `/app/dist/asset-manifest.json`.

## Email-уведомления
- `AICI_SMTP_HOST` — SMTP-хост (обязательно).
- `AICI_SMTP_PORT` (`587`) — порт сервера.
- `AICI_SMTP_USERNAME` / `AICI_SMTP_PASSWORD` — креды для SMTP (App Password, если Gmail).
- `AICI_SMTP_USE_TLS` (`1`) — включить STARTTLS.
- `AICI_SMTP_USE_SSL` (`0`) — true для SMTPS (при этом TLS отключится автоматически).
- `AICI_EMAIL_SENDER` — адрес в поле `From:`. Если пусто, берётся username или `no-reply@<host>`.
- `AICI_EMAIL_RECIPIENTS` — список получателей через запятую (минимум один).

## Деплой и инфраструктура
- `AICI_WEBAPI_IMAGE` (`aici-app:local`) — тег образа для сервиса в `docker-compose.yml`.
- `AICI_WEBAPI_PORT` (`8000`) — внешний порт, на который мапится контейнерный `8000`.
- `CLOUDFLARE_TUNNEL_TOKEN` — токен для sidecar `cloudflared` (опционально).
- `WATCHTOWER_CONTAINER_NAME` (`aici-watchtower`) — имя контейнера auto-update.
- `WATCHTOWER_LABEL_ENABLE` (`true`) — смотреть только на сервисы с меткой `com.centurylinklabs.watchtower.enable=true`.
- `WATCHTOWER_POLL_INTERVAL` (`300`) — период (сек) опроса Docker Registry.

## Smoke/тесты
- `AICI_SMOKE_BASE_URL` — URL сервиса для smoke-тестов (`tests/container/test_api_smoke.py`).
- `AICI_SMOKE_TIMEOUT` (`60`) — максимум секунд на ожидание старта сервисов.
- `AICI_SMOKE_POLL_INTERVAL` (`2`) — интервал опроса статуса во время smoke-тестов.

---

## Рекомендуемый .env для продакшена

```env
AI_CRYPTO_CONFIG=/app/config/pipeline.json
AICI_DEV=0
AICI_LOG_LEVEL=INFO
AICI_ALLOWED_ORIGINS=https://aici.pro
AICI_ENABLE_PIPELINE=1
AICI_RUN_PIPELINE_ON_START=auto
AICI_FAIL_ON_BOOTSTRAP_ERROR=1
AICI_HOST=0.0.0.0
AICI_PORT=8000
AICI_UVICORN_WORKERS=4
AICI_UVICORN_RELOAD=0
AICI_RATE_LIMIT=120
AICI_RATE_LIMIT_WINDOW=60
AICI_SWAGGER_ENABLED=0
AICI_SWAGGER_USERNAME=
AICI_SWAGGER_PASSWORD=
AICI_SWAGGER_DOCS_URL=/docs
AICI_SWAGGER_OPENAPI_URL=/openapi.json
AICI_STATIC_CDN_BASE_URL=
AICI_ASSET_MANIFEST_PATH=
AICI_SMTP_HOST=smtp.yourprovider.com
AICI_SMTP_PORT=587
AICI_SMTP_USERNAME=api@aici.ai
AICI_SMTP_PASSWORD=change-me
AICI_SMTP_USE_TLS=1
AICI_SMTP_USE_SSL=0
AICI_EMAIL_SENDER=no-reply@aici.ai
AICI_EMAIL_RECIPIENTS=alerts@aici.ai,ops@aici.ai
AICI_WEBAPI_IMAGE=ghcr.io/aici/webapi:prod
AICI_WEBAPI_PORT=8000
CLOUDFLARE_TUNNEL_TOKEN=
WATCHTOWER_CONTAINER_NAME=aici-watchtower
WATCHTOWER_LABEL_ENABLE=true
WATCHTOWER_POLL_INTERVAL=300
AICI_SMOKE_BASE_URL=https://api.aici.ai
AICI_SMOKE_TIMEOUT=60
AICI_SMOKE_POLL_INTERVAL=2
```

### Работа с `.env`
1. Скопируй `.env` в окружение, где планируется запуск, и проставь обязательные секреты (`SMTP`, Swagger креды, `CLOUDFLARE_TUNNEL_TOKEN` и т.д.).
2. Для локальной разработки достаточно дефолтов: добавь только SMTP и при необходимости `AICI_DEV=1`.

## Аккаунты и аутентификация
- `POSTGRES_DB` / `POSTGRES_USER` / `POSTGRES_PASSWORD` — параметры контейнера `auth-db`. Локально используем `aici_auth` / `aici` / `aici_local`, в продакшене задаём значения из секретов.
- `POSTGRES_HOST` (`auth-db`) и `POSTGRES_PORT` (`5432`) — хост/порт PostgreSQL. Внутри Docker Compose указываем сервис `auth-db`, извне — `localhost`.
- `AICI_AUTH_DATABASE_URL` (`postgresql+asyncpg://aici:aici_local@auth-db:5432/aici_auth`) — строка подключения к БД пользователей. Для тестов можно временно задать `sqlite+aiosqlite:///...`, но продакшн всегда на PostgreSQL (`postgresql+asyncpg://user:pass@host:5432/dbname`).
- `AICI_AUTH_JWT_SECRET` (`change-me-in-prod`) — секрет подписи JWT/сессионных маркеров, должен быть длинным и уникальным.
- `AICI_AUTH_JWT_ALG` (`HS256`) — алгоритм подписи access-токена.
- `AICI_AUTH_ACCESS_TTL` (`3600`) — срок жизни access-токена в секундах.
- `AICI_AUTH_REFRESH_TTL` (`2592000`) — срок жизни refresh-токена и серверной сессии.
- `AICI_AUTH_EMAIL_TOKEN_TTL` (`259200`) — TTL токена подтверждения email/приглашения.
- `AICI_AUTH_RESET_TOKEN_TTL` (`3600`) — TTL токена восстановления пароля.
- `AICI_AUTH_SESSION_COOKIE` (`aici_session`) — имя httpOnly-cookie с refresh-токеном.
- `AICI_AUTH_SESSION_DOMAIN` (пусто) — домен cookie; укажи, если фронтенд и API на разных хостах.
- `AICI_AUTH_SESSION_SECURE` (`0`) — если `1`, cookie выставляется только по HTTPS (Secure).
- `AICI_AUTH_APP_URL` (`https://aici.pro`) — базовый URL, который попадёт в ссылки подтверждения email/сброса пароля.
- `AICI_AUTH_DEBUG_TOKENS` (`0`) — когда `1`, API в dev-режиме возвращает токены подтверждения/сброса в ответе (для тестов).

## Админ-панель
- `AICI_ADMIN_ENABLED` (`0`) — включает HTML-консоль `/admin/moderation` и API `/api/v1/admin/*`.
- `AICI_ADMIN_USERNAME` / `AICI_ADMIN_PASSWORD` — Basic Auth для входа в модерацию. Без пары значений панель отключена.
3. Перед прод-деплоем проверь CORS, rate limiting и включи Swagger по инструкции, если нужен доступ.
## Stripe / billing
- `AICI_STRIPE_SECRET_KEY` (�����������) � ��������� API ���� Stripe; ��� ���� checkout � ������ �� ��������.
- `AICI_STRIPE_PUBLISHABLE_KEY` (�����������) � publishable key ��� ���������, ���� �������� Stripe Elements.
- `AICI_STRIPE_WEBHOOK_SECRET` (�����������) � signing secret ��� `/api/v1/billing/webhook/stripe` �� Stripe CLI/Live Dashboard.
- `AICI_STRIPE_PRICE_FREE` / `AICI_STRIPE_PRICE_PRO` / `AICI_STRIPE_PRICE_ENTERPRISE` � �������� price-id �� Stripe Billing; ����� �������� � `.env`, ����� �� ��������� prod-�������� � `config/pipeline.json`.
- `AICI_BILLING_TRIAL_DAYS` (`14`) � ��������� ������������ �������� ������� ��� self-serve, ���� � ������� ���� �� �������������� trial.
- `AICI_BILLING_ENTERPRISE_TERMS_DAYS` (`30`) � ������� ���� ��� ������� �� ������ enterprise-������� (�������� `days_until_due`).

## API-����� � ������
- AICI_API_KEY_SECRET (None) � ������-���� ��� ������������� ���������� �������� API-������. � dev ��������� �� ��������� ������������ JWT-������, �� � production ����� ������ ��������� 32-�������� ������ � Base64. ��������� ������� ������ ������������ ����� �����������������.


## Runtime storage and scheduler loops
- `AICI_RUNS_ROOT` (`runs`) — overrides `runs.root` for runtime artifacts (`/app/runs` in container by default).
- `AICI_DATA_ROOT` (`data`) — overrides `data.root` for market data cache (`/app/data` in container by default).
- `AICI_PERFORMANCE_AUTO_ENABLED` (`1`) — enables/disables performance auto-refresh loop.
- `AICI_PERFORMANCE_POLL_SECONDS` (`3600`) — polling interval for performance refresh checks.
- `AICI_INDEX_AUTO_POLL_SECONDS` (`21600`) — polling interval for monthly index auto-run checks.
- `AICI_INDEX_AUTO_PREFIX` (`auto-classic`) — run prefix for classic strategy auto-runs.
- `AICI_INDEX_AUTO_PREFIX_CONSERVATIVE` (`auto-conservative`) — run prefix for conservative strategy auto-runs.
- `AICI_INDEX_AUTO_PREFIX_AGGRESSIVE` (`auto-aggressive`) — run prefix for aggressive strategy auto-runs.
- `AICI_MONTHLY_JOB_LOCK_STALE_SECONDS` (`21600`) — stale timeout for monthly lock files.
- `AICI_DAILY_SNAPSHOT_ENABLED` (`1`) — enables/disables daily snapshot scheduler loop.
- `AICI_DAILY_SNAPSHOT_HOUR_UTC` (`0`) — UTC hour for daily snapshot.
- `AICI_DAILY_SNAPSHOT_MINUTE_UTC` (`0`) — UTC minute for daily snapshot.
- `AICI_BILLING_REMINDERS_ENABLED` (`1`) — enables/disables billing reminder scheduler loop.
- `AICI_BILLING_REMINDER_SECONDS` (`3600`) — polling interval for billing reminder checks.