## Фича: скрытая загрузка fresh data и кеширование по n_top_coins
- [x] Сформировать план реализации фичи кэширования n_top_coins и скрытия fresh data.
- [x] Уточнить и зафиксировать требования: начальная загрузка дефолтного набора (n_top_coins=100) в начале дня, использование без запросов fresh data для базовых пользователей.
- [x] Перенастроить загрузку дефолтного набора: джоб/cron, хранение дневного снапшота, флаг устаревания и путь хранения.
- [x] Скрыть параметр Force fresh data на фронтенде app/playground и запретить его в API/бэкенде, чтобы выбор источника делался автоматически.
- [x] Реализовать логику выбора источника данных: при n_top_coins=100 брать дневной дефолт, при других значениях проверять кэш, при отсутствии — загружать свежие данные и складывать рядом.
- [x] Добавить кэш по нестандартным n_top_coins: хранение до 5–10 разных значений, вытеснение по давности, метки времени, отдельная папка с ежемесячной очисткой.
- [x] Ограничить Free план: запрет изменения n_top_coins более 200 и Final asset count более 12 на фронте и в API, обеспечить fallback на дефолтный набор.
- [x] Обновить внутренние и пользовательские описания (help в playground, README/FAQ) о новой политике данных и ограничениях тарифов.

## Лендинг
- [x] API section: CTA “Go to profile” для незалогиненного пользователя ведёт в регистрацию/логин (без лишнего перехода в /app).

## Поддержка
- [x] Подавить подробные debug-логи ccxt в консоли при pipeline run.


## Фича: опциональный продвинутый прогноз (LSTM) за токены
- [x] Добавить флаг в API/пайплайне: advanced_forecast (true/false), по умолчанию false.
- [x] В режиме basic (advanced_forecast=false) использовать только EWMA и полностью пропускать обучение LSTM.
- [x] В режиме advanced (advanced_forecast=true) оставить текущую логику LSTM + EWMA (mix с α).
- [x] Обновить фронтенд app/playground: переключатель Advanced forecast, описание времени/стоимости, подсказки.
- [x] Логировать выбранный режим и время выполнения, чтобы прозрачно показывать стоимость/длительность.
- [x] Скрыть Advanced forecast в app/playground для не-Administrator и оставить advanced_forecast=true по умолчанию.
- [ ] Обновить тексты help/README/FAQ: что даёт advanced, как влияет на время и стоимость.

Коротко: этот roadmap закрывает технические дыры в фиче `Live vs Backtest + Monthly Composition`.
Проблемы на старте: неиспользуемая continuous-серия в рендере, live-авторан только для `classic`, риск недообновления `AICI_*` по датам, хранение части performance-данных вне persistent volume и риск дублей авторанов при нескольких scheduler/process.
Цель: получить устойчивый monthly-контур, где данные графика и composition синхронны по стратегиям, корректно обновляются при смене месяца и сохраняются без отката после обновления контейнера.
## План работ: Hardening Live vs Backtest + Monthly Composition + Container Persistence

- [x] Шаг 0. Зафиксировать roadmap hardening по выявленным рискам
  - Согласовать список технических дыр: неприменяемая continuous-серия, отсутствие live-ранов для 3 стратегий, риск недообновления series-файлов, неперсистентные пути `results_performance`, гонки планировщиков.
  - Зафиксировать границы работ: только продуктовая логика и данные, без задач тестирования и деплоя.

- [x] Шаг 1. Зафиксировать целевой контракт данных для графика и composition
  - Единый источник для `Live vs Backtest`: `live_backtest` на уровне выбранной стратегии с обязательной continuous-кривой `continuous_series = backtest_series (до live_start_date, не включая дату старта live) + live_series (с live_start_date и далее)`.
  - Контракт стратегий фиксируется как 3 независимых среза данных: `classic`, `conservative`, `aggressive` (технический alias источников/файлов: `aggressive -> risky`).
  - Для каждой стратегии данные графика и composition собираются из одной и той же стратегии и одного run-prefix:
    - `classic -> auto-classic -> backtest classic (AICI_classic.csv) -> monthly snapshots classic`
    - `conservative -> auto-conservative -> backtest conservative (AICI_conservative.csv) -> monthly snapshots conservative`
    - `aggressive -> auto-aggressive -> backtest risky (AICI_risky.csv) -> monthly snapshots aggressive`
  - Обязательная связка для UI: `active_strategy` графика всегда определяет источник `monthly_snapshots`, `monthly_live_snapshots`, `monthly_backtest_snapshots` и `monthly_snapshots_current_month` без кросс-стратегий.

- [x] Шаг 2. Ввести устойчивое хранилище performance-series в персистентной зоне runs
  - Перенести рабочие CSV для `AICI_*`, `BTC_USD`, `ETH_USD` из runtime-путей образа в каталог внутри `runs` (например, `runs/_performance/series`).
  - Оставить fallback на `src/dist static results` только как резервный read-only источник.
  - Обновить резолвер путей в backend, чтобы чтение/запись шли через новый персистентный слой.

- [x] Шаг 3. Исправить логику инкрементального обновления series без потери новых дат
  - Пересчитать окно дозагрузки так, чтобы всегда хватало истории для прогрева модели и вычисления новых точек.
  - Исключить сценарий, когда `next_run_date` уже наступила, но `AICI_*` не продвигаются по датам.
  - Зафиксировать предсказуемое поведение при коротком/рваном хвосте данных: безопасный fallback без обнуления файлов.

- [x] Шаг 4. Реализовать monthly live auto-runs для всех 3 стратегий
  - Добавить отдельные префиксы авторанов: `auto-classic`, `auto-conservative`, `auto-aggressive`.
  - Привязать каждому префиксу свой профиль параметров `run_monthly_update` (risk caps, asset count и т.д.).
  - Обеспечить ежемесячный запуск каждой стратегии и запись полного набора артефактов (`weights`, `perf`, `equity_curve`, `meta`).

- [x] Шаг 5. Перестроить сборку live/backtest payload по стратегиям
  - Генерировать `live_backtest` данные отдельно для каждой стратегии, а не только для classic.
  - Для каждой стратегии выбирать первый валидный auto-run каждого месяца и склеивать месяцы в непрерывный live-хвост.
  - Синхронизировать `live_start_date` и границы `backtest_window` с фактическими данными выбранной стратегии.

- [x] Шаг 6. Подключить continuous-серию в реальный рендер графика
  - Переключить фронтенд-рендер с чистого snapshot-источника на сформированную continuous-серию для активной стратегии.
  - Убрать рассинхрон, когда серия вычисляется в JS, но не применяется к `updateChart`.
  - Сохранить совместимость текущих KPI/легенды с новым источником графика.

- [x] Шаг 7. Привязать Monthly Current Composition к активной стратегии
  - Строить `monthly_snapshots_current_month` и таблицу composition из данных той стратегии, которая выбрана в performance-блоке.
  - Убрать смешивание classic-live с backtest-срезами других режимов.
  - Гарантировать корректное поведение селектора месяца при смене стратегии и при переходе на новый месяц.
  - Обновлено: в блоке monthly composition добавлен отдельный селектор пресета (`Classic/Conservative/Aggressive`), который управляет только composition и не зависит от переключателя пресета графика/KPI.

- [x] Шаг 8. Устранить гонки авторанов и планировщиков в multi-worker режиме
  - Ввести распределенный/файловый lock в персистентном каталоге `runs`, чтобы один и тот же monthly job не стартовал параллельно.
  - Разделить lock-контуры для index-auto и performance-auto.
  - Зафиксировать idempotent-поведение при повторном старте процесса в пределах одного месяца.

- [x] Шаг 9. Закрыть требования к контейнерной конфигурации для бесшовного обновления
  - Зафиксировать обязательные персистентные каталоги данных (`runs`, `data`, performance-series store) и их роль.
  - Зафиксировать runtime-настройки scheduler-интервалов и префиксов через env-переменные.
  - Обеспечить, чтобы после обновления контейнера live-история и monthly snapshots продолжались без отката.

- [x] Шаг 10. Финализировать продуктовый контур фичи после hardening
  - Утвердить итоговое поведение страницы на смене месяца: новые live-run артефакты, обновленный график, обновленный composition.
  - Свести backend/frontend контракты и конфигурацию хранения к единому рабочему состоянию.
  - Обновлено: live-хвост для завершённых месяцев строится из фактических цен и весов первого auto-run месяца (monthly live-MTM), с fallback на `equity_curve.csv` при недостатке данных.
  - Обновлено: в мобильной версии блока `Live vs Backtest performance` скрыт текст `landing-performance__composition-toggle-text`, оставлена иконка тоггла composition.
  - Обновлено: на телефонах для `landing-performance__mode-switch` исправлено переполнение кнопок режимов, а для `composition-table__cell--asset` увеличена минимальная ширина колонки актива.
  - Обновлено: устранён мобильный визуальный дефект у активной кнопки в `landing-performance__mode-switch` (смягчён active-state и снято обрезание внутри контейнера).
  - Обновлено: у `landing-composition__status-dot` зафиксированы размеры во `flex`-контексте, чтобы индикатор оставался круглым на узких экранах.
  - Обновлено: в `Live vs Backtest performance` таблица monthly composition переиспользует `landing-composition__table composition-table` (единая разметка и адаптивные нюансы для SSR и JS-рендера).
  - Обновлено: в подписи `landing-performance__composition-source` значение источника `auto` отображается пользователю как `live` (SSR и JS).
  - Обновлено: в блоке `Why teams trust the index` на лендинге карточки переписаны в формат продуктовых преимуществ (операционная надежность, прозрачный decision trail, integration-ready controls) без изменения дизайна.
  - Обновлено: в hero удалён `landing-hero__subtitle-free` и его текст перенесён в tooltip у CTA `Start free plan`; в `Current index composition` добавлены tooltip-подсказки для `HHI` и `Effective assets`; блок `How it works` переписан в терминах продуктового результата; удалён `landing-performance__chart-caption`; в `landing-performance__transparency-note` добавлен отдельный абзац после `launch.`; для desktop выровнена высота карточек `landing-performance__transparency` и `landing-performance__chart`.
  - Обновлено: исправлена битая разметка в `landing-performance__meta` (`Live since (UTC) ...`) — восстановлен `span[data-performance-summary-mode]`, удалён артефакт `n data-performance-summary-mode>`.
  - Обновлено: в `landing-performance__meta` добавлен разделитель `•` после `Live since (UTC)`, чтобы статус режима не склеивался с первым текстовым блоком.
  - Обновлено: для CTA `Start free plan` внедрён кастомный tooltip в фирменном стиле (header/hero/API), нативный `title` заменён на компонент с единым дизайном и поддержкой `focus-visible`.
  - Обновлено: в `Current index composition` подсказки `HHI` и `Effective assets` переведены на переиспользуемый компонент `landing-cta__tooltip`; тексты переписаны в более прикладном формате (что означает метрика и как её интерпретировать на практике).
  - Обновлено: в карточке `Max weight` исправлен note — удалено некорректное утверждение о фиксированном лимите 15%, текст заменён на нейтральное описание метрики текущего снимка.
  - Обновлено: в `landing` и `docs` уточнено значение маршрута `/app` в копирайте (`account dashboard (/app)`), чтобы убрать расплывчатую формулировку и явно указать, что это раздел личного кабинета.


## Этап 7. Investable index hardening (P0)
- [ ] Ввести universe-filter до кластеризации: `market_cap_min`, `volume_24h_min`, `min_exchange_count`, denylist commodity/stable tokens.
- [ ] Добавить forced-majors guardrail для `BTC`/`ETH` с контролем combined weight.
- [x] Исправить применение `weight_cap`: внедрить bounded-simplex projection без нарушения cap после нормализации.
- [ ] Добавить execution/liquidity фильтр: доступность на целевых биржах и минимальные пороги ликвидности исполнения.
- [x] Убрать look-ahead смещение в backtesting-ветке при horizon-фильтрации активов.
- [x] Добавить execution realism правила по региональным/листинговым ограничениям и логирование причин исключения активов в run.



## Этап 0. Подготовка
- [x] Сформировать roadmap реализации.
- [x] Зафиксировать целевые события: `cta_click`, `signup_started`, `email_confirmed`, `paid`.
- [x] Зафиксировать обязательные поля событий: `cta_id`, `page_path`, `placement`, `cta_format`, `utm_*`, `timestamp`, `actor_id`.
- [x] Утвердить единую логику атрибуции пользователя: `account_id` -> `session_id` -> `fingerprint`.

## Этап 1. Сбор событий на фронтенде
- [x] Добавить/проверить отправку `cta_click` на `/pricing`, `/docs`, `/app`.
- [x] Добавить явную отправку `signup_started` в точке старта регистрации.
- [x] Обеспечить перенос и сохранение `utm_*` из URL в payload событий.
- [x] Обеспечить стабильный `session_id` на весь визит пользователя.

## Этап 2. Ingestion и хранение
- [x] Расширить backend ingestion для поддержки `event_type` и `cta_format`.
- [x] Нормализовать `page_path` и `utm_source` (trim/lowercase/пустые значения).
- [x] Реализовать дедупликацию событий по actor + event + cta + окно времени.
- [x] Сохранять raw-события и агрегаты для быстрой аналитики.

## Этап 3. Воронка и метрики
- [x] Реализовать расчёт воронки `cta_click -> signup_started -> email_confirmed -> paid`.
- [x] Добавить разрезы по `utm_source`, `page_path`, `cta_id`, `cta_format`, `placement`.
- [x] Зафиксировать окно атрибуции (7 дней) и единый подход атрибуции.
- [x] Добавить метрики: `CTR`, `signup CR`, `confirm CR`, `paid CR`.

## Этап 4. Админка CTA Analytics
- [x] Добавить в UI фильтры: период, `utm_source`, `page`, `cta_format`, `cta_id`, `placement`.
- [x] Вывести таблицы и funnel-виджет по `/pricing`, `/docs`, `/app` в разрезе `utm_source`.
- [x] Добавить экспорт CSV для `summary`, `funnel`, `timeseries`, `breakdown`.
- [x] Сохранять состояние фильтров в URL для шаринга отчётов.

## Этап 5. Еженедельная оптимизация CTA-форматов
- [x] Реализовать задачу каждые 7 дней: выбрать top-3 `cta_format` по `CTR` и `signup CR`.
- [x] Автоматически переводить top-3 в `active`, остальные в `paused`.
- [x] Логировать причины решений и изменения статусов.
- [x] Показать в админке блок «Решения за 7 дней».

## Этап 6. Готовый продукт
- [x] Подтвердить end-to-end поток данных: от клика до `paid` с корректной атрибуцией.
- [x] Обновить документацию по событиям, фильтрам и метрикам.
- [x] Зафиксировать статус: функциональность полностью готова к использованию.
- [x] Исправить мобильное позиционирование tooltip в `landing-composition__summary-card`, чтобы подсказка не выходила за ширину экрана.
- [x] Исправить CTA-поведение на `/pricing`: гостевые primary-кнопки открывают registration modal, а для авторизованных plan CTA ведут в `/app/billing` и footer показывает `Go to profile`.
- [x] Убрать `href=/auth/login` у гостевых modal-CTA на `/pricing`, чтобы hover-URL у `Start free plan` указывал на `#registration-modal`.
- [x] Исправить CTA-поведение на `/docs`: для гостя в header и footer показывать `Sign in` + `Start free plan`, для авторизованных оставлять вход в dashboard/profile.
- [x] Исправить live/backtest continuity в `Equity curve`: автораны первого числа корректно засчитываются в покрываемый прошлый месяц при отсутствии точек текущего месяца, а benchmark в continuous-режиме больше не растягивается плоской линией за предел доступных дат.
- [x] Исправить определение месяца для live monthly composition: для авторанов первого числа использовать покрываемый месяц из `equity_curve.csv`, чтобы в селекторе не пропадал `YYYY-02`.
- [x] Оптимизировать мобильную верстку `/pricing`: улучшить отступы, читабельность карточек тарифов и поведение таблицы pricing matrix на узких экранах.
- [x] Добавить в header страниц `/pricing` и `/docs` бургер-меню `landing-header__burger` с мобильным drawer/overlay как на главной странице.
- [x] Синхронизировать header `/pricing` и `/docs` с цветом главной страницы, прижать `Back to site`/`Docs` вправо на desktop, сделать drawer-кнопки full-width на mobile и убрать mobile-overflow у hero, carousel и pricing matrix на `/pricing`.
- [x] Синхронизировать начертание бренда `AI Crypto Index` в header на `/pricing` и `/docs` с главной страницей.
- [x] Заменить публичные контакты с `aicryptoindex@gmail.com` на `contact@aici.pro` и `support@aici.pro` на страницах сайта и в user-facing сообщениях.
- [x] Переключить page-slice в `CTA Analytics` с `/app` на `Landing/Home` для utm-аналитики acquisition.
