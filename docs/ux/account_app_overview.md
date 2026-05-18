# UX /app — макеты и состояния

Документ фиксирует low/high-fidelity макеты для маршрута `/app/*` и особенно для вкладки Overview, чтобы разработка могла опираться на stage3-компоненты (`docs/ux_ui_design.md`). Сетка, токены и микроанимации совпадают с правилами stage3: 12-колоночный грид на десктопе (ширина контейнера 1200px), 8 колонок на планшете и 4 на мобильном. Все блоки построены вокруг layout'а «левая панель 280px + контент 1fr», минимальная высота вьюпорта — 100vh.

## Low-fidelity (wireframes)

### Desktop ≥1440px
- Sidebar (280px) фиксирован, внутри логотип, avatar, primary CTA, список вкладок (`Overview`, `Keys & Security`, `Billing & Plans`, `Usage & Alerts`, `Playground & Docs`, `Support`), внизу — справка и статус системы.
- Хедер контента высотой 72px: breadcrumbs `/app/<tab>`, выпадающий для смены плана, индикатор квот + кнопки уведомлений/профиля.
- Hero-блок Overview: две колонки. Слева приветствие, прогресс onboarding (progress bar + %), CTA «Создать ключ» и «Открыть playground». Справа — карта статуса тарифа (лимиты, истекающий триал, кнопка «Upgrade»).
- Secondary сетка 2×2: карточка onboarding checklist (макс. 5 шагов, чекбоксы), блок быстрых действий (иконки), usage summary (бар-чарты + численные KPI), оповещения (toasts/announcements).
- Empty placeholder для незаполненных блоков — dotted border с иконкой.

```
 ---------------------------------------------------------
| Sidebar | Header / hero greeting           | Plan card |
|         |----------------------------------|-----------|
|         | Checklist | Quick CTA | Usage summary        |
|         |----------------------------------------------|
|         | Alerts / notifications                       |
 ---------------------------------------------------------
```

### Tablet 768–1024px
- Sidebar схлопывается в off-canvas (кнопка в хедере), сетка контента перестраивается в одну колонку, но hero сохраняет stacked layout: приветствие сверху, карточка тарифа ниже.
- Checklist и Quick CTA в двух колонках по 50%, usage summary и alerts — стопкой.
- Sticky toasts под шапкой с auto-hide.

### Mobile ≤480px
- Sidebar → выезжающая панель поверх контента. Хедер: бургер, breadcrumbs, иконка уведомлений.
- Hero = вертикальный стек: приветствие, прогресс, CTA (full width), тариф.
- Checklist = вертикальный список с swipe-анимацией; quick CTA = горизонтальный скролл карточек; usage summary = карточки 100% ширины с компактной типографикой.

## High-fidelity

- Цветовые токены: фон контента `var(--color-surface-elevated)`, sidebar `var(--color-surface)`, акценты `var(--color-accent)` из UI-кита stage3. Градиент hero: `linear-gradient(145deg, #0f172a 0%, rgba(0,229,255,0.12) 100%)`.
- Типографика: Inter 600 для заголовков, 500 для статусов, 400 для вспомогательного текста. Размеры: hero-title 28/34px desktop, 24/30 tablet, 20/28 mobile.
- Компоненты:
  - **Hero meter**: прогресс-бар толщиной 6px, скругление 999px, градиент от `#00E5FF` к `#66F2C9`.
  - **Checklist**: карточка с тенью `0 18px 40px rgba(2,9,28,0.45)`, статусы `done/active/blocked`, иконки из набора `icons/check-circle.svg`, `icons/clock.svg`.
  - **Quick CTA**: карточки 160×140 (desktop) с неоновой рамкой при hover, иконка 32px, подпись и action.
  - **Usage summary**: мини-линчарт (svg sparkline), KPI (Requests, Error rate, Latency P95, Remaining quota). Цвета серий: Requests `#4dabf7`, Errors `#ff6b6b`.
  - **Announcements**: строчные карточки с левой цветной чертой (`success`, `warning`, `info`).
- Микроанимации: hover CTA (glow), чеклист (progress fill), sparkline drawing из `stroke-dashoffset`, sidebar toggle (slide+blur). Соблюдаем `prefers-reduced-motion`.

## Состояния компонентов

### Hero (приветствие + прогресс)
- **Loading**: skeleton-блоки 16px высотой, shimmering gradient `--loading-shimmer`.
- **Empty**: fallback текст «Мы соберём ваш прогресс после регистрации ключа», CTA «Complete profile» включается.
- **Error**: карточка с красной рамкой, иконка `icons/warning.svg`, текст «Не удалось загрузить статус прогресса» + кнопка «Повторить».

### Onboarding checklist
- **Loading**: заменяем строки placeholder-линейками, чекбоксы в состоянии `aria-busy="true"`.
- **Empty**: карточка с иллюстрацией inbox zero, CTA «Запустить onboarding» ведёт на `/app/overview#onboarding`.
- **Error**: список скрывается, выводится текст с retry и ссылкой на документацию.

### Quick CTA
- **Loading**: кнопки disabled с прелоадером.
- **Empty**: отображается сообщение «Нет рекомендованных действий» + кнопка «Посмотреть все вкладки».
- **Error**: toast поверх карточек с описанием проблемы, карточки отключаются.

### Usage summary
- **Loading**: sparkline заменяется пульсирующей полосой, KPI показывают «—» и `aria-label="Данные загружаются"`.
- **Empty**: подпись «Пока нет вызовов API» + CTA «Создать ключ», карточка окрашена в нейтральный цвет.
- **Error**: карточка выделяется красным бордером, отображается код ошибки и кнопка «Повторить запрос».

### Toasts / notifications
- **Loading**: индикатор «Получаем уведомления...».
- **Empty**: скрываем блок, но сохраняем placeholder высотой 48px для стабильности layout.
- **Error**: fallback «Чат-бот недоступен» + ссылка на саппорт.

## Функциональные заметки
- Каждая вкладка `/app/<tab>` использует один и тот же layout: sidebar, header с breadcrumbs, content slot, глобальные toasts. Навигация подсвечивает текущую вкладку и поддерживает клавиатуру (`tabindex="0"`, `aria-current`).
- Breadcrumbs: `Home / App / <Tab>`, отображаем второстепенный степпер для onboarding.
- Toasts появляются в правом верхнем углу, auto-hide 6s, но доступны кнопкой «Скрыть».
- Empty/loading/error состояния управляются через дата-атрибуты (`data-state="loading"`) и CSS модификаторы, чтобы фронтенд мог переключать их без перезагрузки.

Документ служит исходником для реализации `account_base.html` и Overview.
