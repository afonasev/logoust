## 1. Тексты

- [x] 1.1 Добавить в `[schedule]` (`src/bot/messages.toml`): `notify_ask` (с `{text}`), `notify_yes`/`notify_no`, тексты для клиента `notify_created` / `notify_rescheduled` / `notify_cancelled` (с `{date}`/`{time}`), статусы `notify_sent` / `notify_failed` / `notify_not_linked` / `notify_skipped`
- [x] 1.2 Подключить новые ключи в `ScheduleMessages` (`src/bot/messages.py`) и в `load_messages`

## 2. Bot — шаг-вопрос «Уведомить клиента?» на результатах операций

- [x] 2.1 Хелперы сборки/парсинга `callback_data` `sched:ntf:<event>:<client_id>:<stamp>` (event ∈ c/r/x; stamp = `starts_at` в UTC как `YYYYMMDDHHMM`) и клавиатуры вопроса (`_notify_ask_keyboard`, кнопка «Нет» = `sched:ntfno`)
- [x] 2.2 `_ask_notify`: последним шагом результата create/reschedule/delete отдельным сообщением задать вопрос «Уведомить клиента?» с предпросмотром текста и кнопками Да/Нет — только при `client.telegram_chat_id IS NOT NULL`; обычный просмотр карточки (`show_card`) вопрос не показывает
- [x] 2.3 В `_finish_one_off` и `_do_reschedule` подтянуть клиента (chat_id) и вызвать `_ask_notify`
- [x] 2.4 В `do_delete`: до удаления загрузить запись (client_id, starts_at), после удаления показать «удалено» и затем задать шаг-вопрос (если клиент привязан) с event=x

## 3. Bot — отправка уведомления

- [x] 3.1 Хендлер `notify` на `sched:ntf:`: распарсить event/client_id/stamp, загрузить клиента через `get_for_specialist` (изоляция по владельцу), взять `telegram_chat_id`
- [x] 3.2 Повторная проверка привязки: если `telegram_chat_id IS NULL` — ответить `notify_not_linked`, не отправлять
- [x] 3.3 Собрать текст по событию (настенное время в tz специалиста) и отправить `callback.bot.send_message`
- [x] 3.4 Обработать `TelegramForbiddenError`/`TelegramBadRequest` → `notify_failed`; успех → `notify_sent`; логи `appointment.notified` / `appointment.notify_failed`
- [x] 3.5 Зарегистрировать колбэк `sched:ntf:` в `build_router`

## 3a. Bot — уведомление для регулярных записей (серий)

- [x] 3a.1 Ключи `notify_series_created` / `notify_series_changed` / `notify_series_cancelled` (с `{rule}`/`{time}`) в `[schedule]` и `ScheduleMessages`
- [x] 3a.2 Колбэк `sched:ntfs:<event>:<client_id>:<weekday>:<hhmm>` (event ∈ c/m/x), его сборка/парсинг и текст про правило (`_series_notify_text`, `_ask_series_notify`)
- [x] 3a.3 Хендлер `notify_series` на `sched:ntfs:` (повторная проверка привязки, отправка, ошибки, логи), регистрация в `build_router`
- [x] 3a.4 Шаг-вопрос на создании серии (`_finish_regular`, event=c), «Настроить» (`RecurringHandlers._finish`, event=m), «Отменить всё» (`do_stop`, event=x)
- [x] 3a.5 Перенос/отмена одной даты серии (`_do_move` event=r, `do_skip` event=x) используют одиночный `sched:ntf:` с конкретной датой/временем

## 4. Тесты

- [x] 4.1 Хелперы: сборка/парсинг `sched:ntf:` callback; формат stamp ↔ datetime
- [x] 4.2 Шаг-вопрос «Уведомить клиента?» появляется на create/reschedule/delete только для привязанного клиента и отсутствует для непривязанного; «Нет» → `notify_skipped`
- [x] 4.3 Хендлер `notify`: успешная отправка (моки `bot.send_message`) для c/r/x с корректным текстом и chat_id
- [x] 4.4 Сбой доставки (`TelegramForbiddenError`/`TelegramBadRequest`) → `notify_failed`, без падения
- [x] 4.5 Отвязанный к моменту нажатия / чужой клиент → `notify_not_linked` / не отправляется; покрытие 100%
- [x] 4.6 Серии: шаг-вопрос на create/настроить/отменить-всё (`sched:ntfs:` c/m/x) и на перенос/отмену даты (`sched:ntf:` r/x) только для привязанного; `notify_series` — успех/не привязан/сбой доставки

## 5. Документация

- [x] 5.1 Синхронизировать `openspec/specs/appointments/spec.md` (ADDED-требование) и `openspec validate appointments`
- [x] 5.2 Обновить `docs/bot.md` (шаг-вопрос и колбэки `sched:ntf:`/`sched:ntfno`, отправка/ошибки, новые ключи `[schedule]`, лог-события)
- [x] 5.3 Обновить `docs/features.md` (сценарий уведомления клиента для разовых и регулярных записей; снять из «не входит» пункт про напоминания, если он теперь покрыт)
- [x] 5.4 Обновить `README.md`, если затрагиваются ключевые возможности

## 6. Финальная проверка

- [x] 6.1 `make check` — формат, линт, типы, тесты с 100% покрытием
- [x] 6.2 Ручной прогон: создать запись привязанному клиенту → «Уведомить» → клиент получил сообщение; проверить перенос и отмену
