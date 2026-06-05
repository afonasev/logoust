## 1. Данные и миграция

- [x] 1.1 Доменная сущность `AppointmentReminder` (`src/domain/recurring.py` или новый `src/domain/reminder.py`): `id`, `specialist_id`, `client_id`, `starts_at` (aware UTC), `series_id`/`origin_date` (NULL для разовой), `status` (`pending`/`confirmed`/`declined` — enum), `sent_at`, `responded_at`; + протокол `RemindersRepo`
- [x] 1.2 Добавить в домен `Specialist` поля `reminder_enabled: bool = True`, `reminder_time: str = "12:00"`, `reminder_last_run_on: date | None = None` и константы-дефолты
- [x] 1.3 ORM-модель `appointment_reminders` (`src/infrastructure/`): колонки + `UNIQUE(specialist_id, client_id, starts_at)`; индекс по `(specialist_id, client_id, starts_at)` для чтения статусов
- [x] 1.4 Колонки на ORM-модели специалиста: `reminder_enabled` (Boolean, server_default true), `reminder_time` (String, server_default '12:00'), `reminder_last_run_on` (Date, nullable); маппинг ORM↔domain
- [x] 1.5 Alembic-миграция `alembic/versions/0007_appointment_reminders.py` (или след. номер): таблица `appointment_reminders` + три колонки на `specialists` с server-default'ами; downgrade — drop таблицы и колонок
- [x] 1.6 Обновить фикстуры/билдеры специалиста в `tests/conftest.py` под новые поля
- [x] 1.7 Тест миграции (`tests/test_migration.py`): up/down, наличие таблицы, колонок и UNIQUE

## 2. Домен — логика прохода

- [x] 2.1 Чистая функция `is_reminder_due(specialist, now)` в домене: `reminder_enabled` И `utc_to_wall(now, tz) >= reminder_time` И `reminder_last_run_on != today_in_tz(now, tz)`
- [x] 2.2 Тесты `is_reminder_due`: до времени / после времени / уже выполнен сегодня / выключен / догон после простоя

## 3. Репозиторий напоминаний

- [x] 3.1 `SqlAlchemyRemindersRepo`: `insert_pending(reminder) -> bool` (`INSERT … ON CONFLICT(specialist_id, client_id, starts_at) DO NOTHING`); `get_for_specialist(reminder_id, specialist_id)`; `set_status(reminder_id, status, responded_at) -> старый статус` (для определения перехода); `statuses_for_day(specialist_id, occurrences)` — статусы по списку `(client_id, starts_at)`
- [x] 3.2 Расширить `SpecialistsRepo`: `list_reminder_candidates()` (все с `reminder_enabled`) и `mark_reminder_run(specialist_id, run_on)`
- [x] 3.3 Тесты репозитория: идемпотентность вставки (повторный insert не плодит дубли); set_status возвращает прежний статус; выборка статусов по occurrence; кандидаты только включённые; пометка `reminder_last_run_on`

## 4. Сервис — проход и обработка ответа

- [x] 4.1 Use-case прохода `run_reminders_if_due(specialist, now, ...)` (новый `src/services/reminder.py`): проверка `is_reminder_due` → `list_specialist_day(..., series=ctx)` на ЗАВТРА → для каждого occurrence привязанного клиента `insert_pending` → если вставлено, отдать `(chat_id, text)` для отправки → в любом случае `mark_reminder_run(today)`
- [x] 4.2 Рендер текста напоминания клиенту: настенные дата/время записи в tz; все формулировки из `messages.toml`
- [x] 4.3 Use-case обработки ответа `apply_reminder_response(reminder_id, specialist_id, answer)`: загрузить через `get_for_specialist` (изоляция), перезаписать статус, вернуть сигнал «нужно уведомить специалиста» только при переходе `* → declined`
- [x] 4.4 Use-case чтения статусов для дневного экрана/карточки (`statuses_for_day`/`status_for_occurrence`)
- [x] 4.5 Логи `appointment.reminder_sent` / `appointment.reminder_confirmed` / `appointment.reminder_declined` / `appointment.reminder_failed` с `specialist_id`/`client_id`
- [x] 4.6 Тесты сервиса: due-проход (привязанный → отправка+журнал, непривязанный → пропуск, серия включена, пустое завтра → только пометка); идемпотентность (повторный проход не шлёт); ответ confirmed/declined/смена/повтор → корректный сигнал специалисту

## 5. Фоновая джоба в общем планировщике

- [x] 5.1 Общий minute-loop в `src/__main__.py` (сон до следующей минуты → проход по due-джобам → повтор), весь проход в `try/except Exception` с логом; если loop уже введён `daily-digest` — зарегистрировать джобу напоминаний в него
- [x] 5.2 Джоба напоминаний: открыть сессию, `list_reminder_candidates`, для каждого вызвать сервис с `send = bot.send_message`, ловить `TelegramForbiddenError`/`TelegramBadRequest`
- [x] 5.3 Тест одного прохода с замоканными зависимостями (без реального sleep): кандидаты → send для due, пропуск для не-due, сбой одного не прерывает остальных

## 6. Bot — ответ клиента и уведомление специалисту

- [x] 6.1 Хелпер сборки/парсинга `callback_data` `appt:cfm:<reminder_id>:<y|n>`
- [x] 6.2 Кнопки `[✅ Подтверждаю] [❌ Не смогу]` в сообщении напоминания (тексты из `messages.toml`)
- [x] 6.3 Хендлер `appt:cfm:`: распарсить, вызвать `apply_reminder_response`; ответить клиенту тостом «принято»; при сигнале об отказе — `bot.send_message` специалисту с кнопкой `[→ Открыть запись]`
- [x] 6.4 Кнопка `[→ Открыть запись]` ведёт на существующую карточку: разовая — по `appointment_id`, виртуальный повтор — по `(series_id, origin_date)` тем же путём, что из дневного экрана
- [x] 6.5 Зарегистрировать колбэк `appt:cfm:` в `build_router`
- [x] 6.6 Тесты: парсинг callback; confirmed → тост, без сообщения специалисту; declined → тост + сообщение специалисту с кнопкой; чужое напоминание → не применяется; покрытие 100%

## 7. Bot — отображение статуса специалисту

- [x] 7.1 Дневной экран расписания (`src/bot/handlers/schedule.py`): ✅ префикс у записи с `status=confirmed` (подтянуть статусы через `statuses_for_day`)
- [x] 7.2 Карточка записи (`show_card`): пометка «подтверждена» при `confirmed` (текст из `messages.toml`)
- [x] 7.3 Тесты: ✅ в дневном экране только для confirmed; пометка на карточке для confirmed; отсутствие индикатора для pending/declined/без напоминания

## 8. Настройки в боте

- [x] 8.1 Тексты в `messages.toml` (`[settings]`): тумблер вкл/выкл напоминаний, кнопка времени, подсказка ввода, ошибка; показать текущее состояние в `render_settings`
- [x] 8.2 Подключить ключи в `SettingsMessages` (`src/bot/messages.py`) и `load_messages`
- [x] 8.3 Хендлеры в `src/bot/handlers/settings.py`: тумблер `reminder_enabled`; ввод времени через FSM по образцу `day_start` с `parse_hhmm`; регистрация колбэков в `build_router`
- [x] 8.4 Тесты настроек: тумблер меняет флаг; валидное время сохраняется; некорректное — отклоняется

## 9. Документация

- [x] 9.1 `docs/database.md` — таблица `appointment_reminders` + колонки на `specialists` + миграция
- [x] 9.2 `docs/architecture.md` — общий планировщик-петля (минутный тик, due-джобы) рядом с polling
- [x] 9.3 `docs/bot.md` — колбэк `appt:cfm:`, сообщение напоминания и кнопки, уведомление об отказе, ✅/пометка статуса, настройки, новые тексты и лог-события
- [x] 9.4 `docs/features.md` — сценарий напоминания с подтверждением
- [x] 9.5 `README.md` — раздел «Ключевые возможности», если затронут
- [x] 9.6 Синхронизировать `openspec/specs/appointment-reminder/` и `openspec/specs/appointments/`; `openspec validate`

## 10. Финальная проверка

- [x] 10.1 `make check` — формат, линт, типы, тесты со 100% покрытием
- [x] 10.2 Ручной прогон: выставить ближайшее время → напоминание приходит привязанному клиенту с записью на завтра; ✅ → пометка в плане и на карточке; ❌ → специалисту уведомление с переходом на карточку; повторный тик/рестарт — без дубля
