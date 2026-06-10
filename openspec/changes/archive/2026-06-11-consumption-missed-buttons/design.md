## Context

Вечерний отчёт строится в `src/bot/scheduler.py`: `render_consumption_report` даёт текст,
`_consumption_keyboard` — клавиатуру. Сейчас кнопки отдаются только для `report.deducted`
(`subs:card:<id>`). `report.missed` (тип `MissedEntry`: `child_name`, `starts_at`, `reason`)
печатается текстовыми ❗-строками без кнопок.

`MissReason` ∈ {`NO_SUBSCRIPTION`, `EXHAUSTED`}. Источник `missed` — `_record_outcome` в
`src/services/consumption.py`, где доступны клиент и (для EXHAUSTED) активный абонемент.
Хендлеры `clients:card:<id>` и `subs:card:<id>` уже зарегистрированы и принимают такие callback.

## Goals / Non-Goals

**Goals:**
- ❗-встречи в отчёте — кнопки перехода: NO_SUBSCRIPTION → карточка клиента, EXHAUSTED →
  карточка абонемента.
- Причина видна в подписи кнопки; текстовые ❗-строки из тела сообщения убраны.

**Non-Goals:**
- Не трогаем логику списания, антидубль, расписание прохода.
- Не меняем кнопки `deducted`.
- Без миграций БД и новых зависимостей.

## Decisions

**1. `MissedEntry` несёт оба идентификатора цели.** Добавляем `client_id: int` (всегда) и
`subscription_id: int | None` (только EXHAUSTED). Клавиатура выбирает callback по `reason`:
EXHAUSTED → `subs:card:<subscription_id>`, иначе → `clients:card:<client_id>`. Альтернатива —
один полиморфный «target callback» в записи — отвергнута: `MissedEntry` остаётся чистыми
данными, маппинг reason→callback живёт в слое `bot/` рядом с остальной клавиатурой.

**2. ❗-строки уезжают из текста полностью (вариант «только кнопка»).** `render_consumption_report`
оставляет заголовки (`title`, `deducted_header`, `missed_header`), но не печатает строки по
`missed`. Подписи `missed_no_subscription` / `missed_exhausted` переезжают в подпись кнопки
(формат `❗ {child} {time} — …`). Симметрия с `deducted`: там тоже header-текст + кнопки.

**3. Сообщение по-прежнему не пустое без тапа.** `missed_header` остаётся, поэтому даже когда
все строки — кнопки, специалист видит, что это за блок.

## Risks / Trade-offs

- [Длинная подпись кнопки при длинном имени ребёнка] → лимит Telegram на текст inline-кнопки
  высок (~256 символов на практике достаточно); имена детей короткие, риск минимален.
- [EXHAUSTED без `subscription_id` из-за рассинхрона] → `subscription_id` для EXHAUSTED всегда
  известен в `_record_outcome` (списание не прошло именно из-за `remaining = 0` этого абонемента);
  на NO_SUBSCRIPTION он `None` и не используется. Покрываем тестом обеих веток.
