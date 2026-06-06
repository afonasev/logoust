## 1. Domain

- [x] 1.1 Создать `src/domain/audit.py`: enum'ы `AuditKind` (`message`/`action`), `AuditEvent` (закрытый список: `notify_created`/`notify_rescheduled`/`notify_cancelled`/`welcome`/`reminder` + `client_created`/`client_archived`/`client_restored`/`appt_created`/`appt_rescheduled`/`appt_deleted`), `DeliveryStatus` (`sent`/`failed`)
- [x] 1.2 Добавить dataclass `AuditEntry` (id, specialist_id, created_at, kind, event, client_id?, text?, status?, error?)
- [x] 1.3 Описать протокол `AuditRepo`: `record(...)` и `list_for_specialist(specialist_id, *, limit, offset)` + `count_for_specialist(specialist_id)` (для пагинации)

## 2. Infrastructure + миграция

- [x] 2.1 Создать `AuditLogORM` (таблица `audit_log`) в `src/infrastructure/audit_repo.py`: `Mapped`-колонки, FK `specialist_id`, nullable FK `client_id`, `created_at` через `lambda: datetime.now(UTC)`, индекс `(specialist_id, created_at)`, `__repr__`
- [x] 2.2 Реализовать `SqlAlchemyAuditRepo` (record + постраничное чтение, сортировка `created_at DESC`, join/догрузка имени клиента для рендера)
- [x] 2.3 Написать Alembic-миграцию `alembic/versions/0007_audit_log.py` (create table + index; downgrade — drop)
- [x] 2.4 Обновить `tests/test_migration.py` под новую таблицу

## 3. Services

- [x] 3.1 Создать `src/services/audit.py`: `record_action(...)`, `record_message(...)`, `list_audit(repo, specialist_id, page)` (страница → limit/offset, признак наличия соседних страниц)
- [x] 3.2 Вписать запись `action` в `src/services/clients.py` (created/archived/restored) — той же сессией, что и операция
- [x] 3.3 Вписать запись `action` в `src/services/appointments.py` (create/reschedule/delete) — той же сессией, что и операция

## 4. Bot

- [x] 4.1 Добавить секцию `[audit]` в `src/bot/messages.toml` (button `📜 Аудит`, заголовок, пустой журнал, шаблоны строк message/action, подписи кнопок пагинации) + расширить `BotMessages` в `src/bot/messages.py`
- [x] 4.2 Добавить кнопку `📜 Аудит` в `build_main_keyboard` (`src/bot/handlers/clients.py`) рядом с `⚙️ Настройки`
- [x] 4.3 Создать `src/bot/handlers/audit.py`: `AuditHandlers` (открытие ленты, рендер строк по `kind`, пагинация `◀ позже / раньше ▶`), `build_router` с `SpecialistMiddleware`
- [x] 4.4 Подключить router аудита в `src/bot/dispatcher.py`

## 5. Тесты

- [x] 5.1 `tests/test_audit_repo.py`: record + постраничное чтение, изоляция по `specialist_id`, сортировка
- [x] 5.2 `tests/test_audit_service.py`: record_action/record_message, пагинация (`list_audit`)
- [x] 5.3 Тесты записи `action` из сервисов clients/appointments (событие появляется; нелогируемое действие — нет)
- [x] 5.4 `tests/test_audit_handlers.py`: открытие ленты, пустой журнал, перелистывание, различимый рендер message/action (fake Message/CallbackQuery)

## 6. Документация

- [x] 6.1 `docs/database.md`: таблица `audit_log` (колонки, индекс, миграция 0007)
- [x] 6.2 `docs/bot.md`: меню `📜 Аудит`, router, тексты
- [x] 6.3 `docs/features.md`: фича журнала событий
- [x] 6.4 `README.md`: пункт в «Ключевые возможности»

## 7. Завершение

- [x] 7.1 `make check` (format + lint + type-check + 100% coverage) зелёный
- [x] 7.2 Заметка о зависимости: `appointment-notify` после мерджа должен писать `message`-строки и убрать из своих Non-Goals «хранение истории отправок» (правки — в его change, не здесь)
