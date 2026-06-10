## 1. Данные отчёта

- [x] 1.1 В `src/services/consumption.py` добавить в `MissedEntry` поля `client_id: int` и `subscription_id: int | None`
- [x] 1.2 В `_record_outcome` заполнить `client_id` (всегда) и `subscription_id` (для EXHAUSTED — id активного абонемента, для NO_SUBSCRIPTION — `None`)

## 2. Тексты

- [x] 2.1 В `src/bot/messages.toml` секции `[consumption]` переформулировать `missed_no_subscription` / `missed_exhausted` как подписи кнопок (`❗ {child} {time} — …`)

## 3. Отчёт и клавиатура

- [x] 3.1 В `src/bot/scheduler.py::render_consumption_report` убрать печать ❗-строк по `report.missed` (оставить заголовки `title` / `deducted_header` / `missed_header`)
- [x] 3.2 В `_consumption_keyboard` добавить кнопки для `report.missed`: EXHAUSTED → `subs:card:<subscription_id>`, иначе → `clients:card:<client_id>`; убрать ранний `return None`, отдавать клавиатуру при наличии `deducted` или `missed`

## 4. Документация и спека

- [x] 4.1 Синхронизировать `openspec/specs/subscription-consumption/spec.md` с дельтой (требование «Отчёт специалисту по итогам прохода»)
- [x] 4.2 Поправить `docs/features.md` / `docs/bot.md`, если отчёт там описан

## 5. Тесты и проверка

- [x] 5.1 Тест: отчёт с ❗-встречей NO_SUBSCRIPTION содержит кнопку с callback `clients:card:<client_id>` и причиной в подписи
- [x] 5.2 Тест: отчёт с ❗-встречей EXHAUSTED содержит кнопку с callback `subs:card:<subscription_id>`
- [x] 5.3 Тест: тело сообщения не содержит текстовых ❗-строк (только заголовки)
- [x] 5.4 `make check` — формат, линт, типы, 100% покрытие
