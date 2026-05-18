# Roadmap

## Google OAuth — вход через Google

### Backend
- [x] Добавить таблицу `auth_oauth_connections` (account_id, provider, provider_user_id, access_token, refresh_token, expires_at) и Alembic-миграцию.
- [x] Добавить в `ServiceSettings` поля `google_client_id` и `google_client_secret`.
- [x] Установить зависимости `authlib` и `httpx`.
- [x] Реализовать `GET /auth/google/login` — генерация `state`, сохранение в cookie (CSRF-защита), редирект на Google OAuth consent screen.
- [x] Реализовать `GET /auth/google/callback` — валидация `state` из cookie, обмен кода на токен, получение профиля из Google (`email`, `name`, `sub`), создание/поиск аккаунта, возврат JWT + refresh token.
- [x] Обработать edge case: email уже существует с паролем — связать OAuth с существующим аккаунтом.
- [x] Новые аккаунты через OAuth создавать сразу со статусом `ACTIVE` (email подтверждён Google — `PENDING_ACTIVATION` не нужен).
- [x] Заполнять `full_name` из профиля Google при создании аккаунта.
- [x] Проставлять `email_verified_at` сразу для всех OAuth-аккаунтов (Google гарантирует верификацию email).

### Frontend
- [x] Добавить кнопку «Continue with Google» в модалки логина и регистрации.
- [x] Обработать redirect после callback: восстановить pre-auth URL или перенаправить в `/app`.

---

## GitHub Public Release — подготовка репозитория к публикации

### 1. Критическая безопасность (сделать ДО публикации)
- [x] **Вычистить git-историю от .env файлов** — удалено через `git filter-repo --invert-paths --path .env --path .env.dev.local`; история перезаписана и force-push на GitHub выполнен.
- [x] **Создать `.env.example`** — шаблон со всеми переменными, но с заглушками (`your_google_client_id_here`, `change_me`, и т.д.).
- [x] **Отозвать/сменить все ключи, которые засветились в истории** — Google OAuth Client Secret, Stripe secret key, NOWPayments API key, Gmail app password, Docker Hub token, JWT secret, Postgres password.
- [x] **Убедиться что `cloudflared.exe` не попадёт в репо** — 66 МБ бинарник; уже в `.gitignore`, но проверить `git status`.

### 2. Чистка кода
- [x] **Заменить `print()` на `logging`** в `src/ai_crypto_index/api/app.py` — там несколько debug-принтов с JSON.
- [x] **Убрать захардкоженный email-дефолт** в `src/ai_crypto_index/shared/email_notifications.py` — строка `_DEFAULT_RECIPIENT = "aicryptoindex@gmail.com"` должна читаться из env-переменной.
- [x] **Удалить `tmp_icon_32.png`** из корня репозитория — временный файл.
- [x] **Добавить в `.gitignore`** кэш-директории если ещё не там: `.pytest_cache/`, `.ruff_cache/`.

### 3. Документация
- [x] **Переписать `README.md` на английском** — для портфолио аудитория международная; описать проект, стек, скриншоты/гифки UI, инструкцию по локальному запуску через Docker.
- [x] **Добавить скриншоты или GIF** в README — показать дашборд, лендинг, авторизацию.
- [x] **Добавить `SECURITY.md`** — минимальный файл с инструкцией "как сообщить об уязвимости".
- [x] **Убрать `docs/rules/`** из репо — внутренние инструкции для AI-ассистента, странно смотрятся в публичном репо.
- [x] **Убрать `CLAUDE.md` и `AGENTS.md`** из публичного репо или добавить в `.gitignore`.

### 4. Структура и порядок
- [x] **Унифицировать язык комментариев** — привести всё к английскому для публичного репо.
- [x] **Проверить `examples/`** на наличие личных данных или захардкоженных путей.
- [x] **Проверить `docs/update_scripts/`** — убедиться что нет упоминания реальных паролей внутри SQL-скриптов.
- [x] **Убрать внутренние ops-документы** (`docs/deployment/swagger_access.md` и подобные), которые раскрывают операционную инфраструктуру.

### 5. GitHub-настройки после публикации`
- [x] **Добавить topics** в репозитории: `python`, `fastapi`, `cryptocurrency`, `portfolio-optimization`, `machine-learning`, `docker`, `postgresql`.
- [x] **Написать описание репозитория** — например: *"AI-driven cryptocurrency index with portfolio optimization, backtesting, and self-serve billing"*.
- [x] **Настроить GitHub Actions secrets** в Settings → Secrets нового публичного репо.
- [x] **Сделать репозиторий публичным** на GitHub.