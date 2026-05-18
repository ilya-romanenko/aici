# Куки аутентификации: состояние на 2026-01-27

## Какие куки выставляем
- Единственная обязательная cookie — refresh-сессия. Имя берётся из `auth.session_cookie_name` (`config/pipeline.json`, по умолчанию `aici_session`).
- Устанавливается в `_set_refresh_cookie` (`src/ai_crypto_index/api/app.py`) с параметрами: `HttpOnly`, `SameSite=Lax`, `path=/`, `max_age=auth.refresh_token_ttl_seconds` (дефолт 2 592 000 сек ≈ 30 дней). `Secure` и `Domain` берутся из `auth.session_cookie_secure` и `auth.session_cookie_domain`.
- Логаут (`_clear_refresh_cookie`) удаляет cookie по тем же имени и домену.

## Где хранится конфигурация
- Блок `auth` в `config/pipeline.json`: `session_cookie_name`, `session_cookie_secure`, `session_cookie_domain`, `refresh_token_ttl_seconds` фиксируют имя, защитные флаги и TTL refresh-cookie.
- Переменные окружения `AICI_AUTH_SESSION_COOKIE`, `AICI_AUTH_SESSION_SECURE`, `AICI_AUTH_SESSION_DOMAIN`, `AICI_AUTH_REFRESH_TTL` зеркалируют эти поля.

## Проверка трекинга
- В статических бандлах (`src/ai_crypto_index/frontend/static/js/main.js`, `.../account.js`) нет загрузчиков аналитики: поиск по `gtag|analytics|segment|mixpanel|hotjar|pixel` пуст.
- Баннер cookie-consent хранит только флаг `analytics` в `localStorage`; он не подгружает внешние скрипты без явного включения.
- Шаблоны подключают только first-party бандлы и Google Fonts; сторонние трекинг-пиксели или SDK по умолчанию не загружаются.
