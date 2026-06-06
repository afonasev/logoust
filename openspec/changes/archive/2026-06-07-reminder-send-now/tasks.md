## 1. Сервис: ungated-сбор напоминаний

- [x] 1.1 В `src/services/reminder.py` выделить немутирующее ядро сбора (завтрашние occurrence + `insert_pending` + формирование `ReminderToSend`), переиспользуемое боевым проходом
- [x] 1.2 Добавить `run_reminders_now(...)` — тот же сбор без гейта `is_reminder_due` и без `mark_reminder_run`; `run_reminders_if_due` оставляет гейт+stamp поверх общего ядра
- [x] 1.3 Тесты сервиса: ручной запуск шлёт по новой occurrence; не дублирует уже журналированную; не проставляет `reminder_last_run_on`; работает при выключенных напоминаниях

## 2. Bot: кнопка и хендлер ручного запуска

- [x] 2.1 В `src/bot/messages.toml` + `src/bot/messages.py` добавить подпись кнопки и подсказки результата (успех со счётчиком / «некого напоминать»)
- [x] 2.2 В `src/bot/handlers/settings.py` добавить коллбэк-кнопку «Отправить напоминания сейчас», хендлер: собрать `ReminderToSend` через сервис (с `resolve_template("appt_reminder")`) и доставить через воронку из `src/bot/scheduler.py` (`_deliver`: send + `record_client_message` SENT/FAILED)
- [x] 2.3 Показать специалисту итог (сколько ушло / некого), зарегистрировать роут
- [x] 2.4 Тесты хендлера: успешная отправка пишет строку аудита (SENT) и кнопки; пустой случай показывает подсказку; сбой доставки пишет FAILED и не прерывает остальных

## 3. Документация и спека

- [x] 3.1 Перенести требование из delta в `openspec/specs/appointment-reminder/spec.md`; `openspec validate appointment-reminder`
- [x] 3.2 Обновить `docs/bot.md` (новая кнопка/коллбэк) и `docs/features.md` (ручной запуск напоминаний)
- [x] 3.3 Добавить точку отправки в список «уже сделано» в `.claude/rules/bot.md`

## 4. Проверка

- [x] 4.1 `make check` — формат, линт, типы, 100% покрытие
