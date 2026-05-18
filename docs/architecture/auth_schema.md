# Auth Schema Blueprint

Ниже зафиксирована целевая схема PostgreSQL для Этапа 1 (аккаунты + аутентификация). В тестах допускается `sqlite+aiosqlite`, но все типы и индексы оптимизированы под PostgreSQL 14+.

## Таблицы

| Таблица | Назначение | Ключевые поля |
| --- | --- | --- |
| `auth_organizations` | Карточка юрлица/фонда. | `name`, `size_label`, `primary_use_case`, `country`. |
| `auth_accounts` | Пользователи self-serve. | `email` (уникальный), `full_name`, `hashed_password`, `status`, `organization_id`, `use_case`, `newsletter_opt_in`, `email_verified_at`. |
| `auth_roles` | Ролевые профили. | `slug` (`admin`, `moderator`, `member`), `priority`, `is_default`. |
| `auth_account_roles` | M2M между аккаунтами и ролями. | `account_id`, `role_id`, `granted_at`. |
| `auth_communication_channels` | История коммуникаций (email, Slack, Telegram). | `channel_type`, `value`, `status`, `is_primary`, `verified_at`, `opt_out_at`. |
| `auth_account_consents` | История согласий (terms/privacy/marketing). | `consent_type`, `version`, `granted`, `ip_address`, `user_agent`, `source`, `extra JSON`. |
| `auth_email_tokens` | Токены подтверждения e-mail и приглашений. | `token_hash` (SHA-256), `expires_at`, `delivery_channel`, `consumed_at`. |
| `auth_password_reset_tokens` | Токены восстановления пароля. | `token_hash`, `expires_at`, `ip_address`, `user_agent`, `consumed_at`. |
| `auth_sessions` | Refresh/Session cookies. | `refresh_token_hash`, `issued_at`, `expires_at`, `revoked_at`, `metadata JSON`. |

## Индексы и связи

- `auth_accounts.email` — `UNIQUE`, `auth_accounts` также имеет составной индекс `(organization_id, status)` для модерации.
- `auth_account_roles` — `UNIQUE(account_id, role_id)` + каскадное удаление.
- `auth_account_consents` — индекс `(account_id, consent_type)` для быстрого построения истории.
- `auth_communication_channels` — `UNIQUE(account_id, channel_type, value)` и таймстемпы в UTC.
- Токены и сессии имеют отдельные индексы по `account_id` и `refresh_token_hash` для быстрой ревокации.

## Значения по умолчанию

- Все даты в UTC (`timezone=True`).
- Булевы поля везде имеют `server_default`.
- Роли сидируются через `accounts.bootstrap.ensure_default_roles`:
  - `admin` (priority 1)
  - `moderator` (priority 5)
  - `member` (priority 10, `is_default=True`)

## Конфигурация

- Путь к БД задаётся через `AICI_AUTH_DATABASE_URL` или `auth.database_url` в `config/pipeline.json`.
- На dev/compose окружениях по умолчанию используется `postgresql+asyncpg://aici:aici_local@auth-db:5432/aici_auth`. Для unit-тестов допускается `sqlite+aiosqlite`, если строка подключения переопределена в фикстурах. При запуске FastAPI выполняется `ensure_schema()` и сидирование ролей.
- TTL токенов и параметры cookie читаются из блока `auth` в `config/pipeline.json` или соответствующих переменных окружения (см. `docs/guides/environment_variables.md`).
