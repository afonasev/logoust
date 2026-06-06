## 1. Домен

- [x] 1.1 В `src/domain/recurring.py` добавить `RecurringSchedule` (id, specialist_id, client_id, comment, active, created_at, updated_at)
- [x] 1.2 Переименовать/переопределить `RecurringAppointment` → `RecurringSlot` (id, schedule_id, weekday, time_hhmm, start_date, materialized_through, active, created_at, updated_at) — без specialist_id/client_id/comment
- [x] 1.3 Переименовать `RecurringException` → `RecurringSlotOverride` (id, slot_id, original_date, skipped: bool, moved_to: datetime|None, comment: str|None, created_at)
- [x] 1.4 Обновить протоколы репозиториев: `RecurringScheduleRepo` (add, get_for_specialist, list_active_for_specialist, set_active), `RecurringSlotRepo` (add, list_for_schedule, get_for_specialist, set_active, set_materialized_through, update_rule), `RecurringSlotOverrideRepo` (upsert по трём осям, list_for_slot, list_for_specialist)

## 2. Инфраструктура и миграция

- [x] 2.1 Alembic-ревизия: drop `recurring_exceptions`, drop `recurring_appointments`; удалить из `appointments` строки с `series_id IS NOT NULL`; заменить колонку/FK `series_id`→`slot_id` и индекс `uq_appointments_series_origin`→`uq_appointments_slot_origin`
- [x] 2.2 В той же ревизии создать `recurring_schedules` (индекс `(specialist_id, active)`), `recurring_slots` (FK→schedules, индекс `(schedule_id, active)`), `recurring_slot_overrides` (FK→slots, unique `(slot_id, original_date)`)
- [x] 2.3 Реализовать downgrade (обратный дроп новых таблиц и восстановление прежней схемы без данных)
- [x] 2.4 ORM-модели в `src/infrastructure/recurring_repo.py`: `RecurringScheduleORM`, `RecurringSlotORM`, `RecurringSlotOverrideORM` с `Mapped`/`mapped_column`
- [x] 2.5 Реализовать репозитории: `SqlAlchemyRecurringScheduleRepo`, `SqlAlchemyRecurringSlotRepo`, `SqlAlchemyRecurringSlotOverrideRepo`
- [x] 2.6 В `src/infrastructure/appointments_repo.py`: `series_id`→`slot_id`, FK на `recurring_slots`, обновить `insert_occurrence` (on_conflict по `(slot_id, origin_date)`) и маппинг
- [x] 2.7 В `src/infrastructure/reminders_repo.py`: `series_id`→`slot_id` в ORM и маппинге

## 3. Сервисы — ядро occurrence (рефактор slot-центрично)

- [x] 3.1 Обновить `Appointment` (`src/domain/appointment.py`): `series_id`→`slot_id`; перепроверить `recurring_mark`
- [x] 3.2 Перенести `_occurrence`, `_effective_starts_at`, `occurrences_landing_in`, `series_taken_times`, `_settle_series`, `settle` на `RecurringSlot` + `RecurringSlotOverride`; учесть `skipped`/`moved_to` вместо `new_starts_at`
- [x] 3.3 Эффективный комментарий: `_occurrence` берёт `override.comment ?? schedule.comment`; `settle` пишет эффективный комментарий в строку
- [x] 3.4 `SeriesContext`/`load_series_context`: хранить активные слоты со ссылкой на их расписание (для comment/active), overrides по `slot_id`; фильтровать по `schedule.active AND slot.active`
- [x] 3.5 `occurrences_in_window(schedule_ctx, win_start, win_end)` — агрегатор occurrence по всем слотам расписания, отсортированный по времени (для карточки 14 дней)

## 4. Сервисы — use-cases расписания

- [x] 4.1 `create_schedule(...)` + `add_slot(...)` — создать расписание и слоты (start_date = ближайший weekday ≥ сегодня)
- [x] 4.2 `edit_slot(...)` — смена времени (сетку не двигает) и дня недели (пересчёт start_date, materialized_through→today)
- [x] 4.3 `remove_slot(...)` — `slot.active=false`; если активных слотов не осталось — `schedule.active=false`
- [x] 4.4 `stop_schedule(...)` — `schedule.active=false`
- [x] 4.5 Override-операции на occurrence: `skip_occurrence`, `move_occurrence`, `set_occurrence_comment` — upsert override по `(slot_id, original_date)` с правкой нужной оси
- [x] 4.6 Логирование бизнес-событий (`recurring.schedule_created`, `recurring.slot_added`, `recurring.slot_edited`, `recurring.slot_removed`, `recurring.schedule_stopped`, `recurring.occurrence_skipped/moved/commented`)

## 5. Чтение расписания и напоминания

- [x] 5.1 `src/services/appointments.py`: `taken_slot_times`/`list_specialist_day` — занятость и виртуальные occurrence по нескольким слотам (включая два слота в один день); эффективный комментарий в виртуальной occurrence
- [x] 5.2 `src/services/reminder.py`: чтение occurrence и `slot_id`/`origin_date`/комментария через обновлённый `SeriesContext`
- [x] 5.3 `src/bot/scheduler.py`: прокинуть новые репозитории (schedule/slot/override) в `run_reminders_if_due`

## 6. Бот — визард создания

- [x] 6.1 FSM визарда: цикл «выбрал день → выбрал время → добавить ещё день?»; накопление списка слотов в state
- [x] 6.2 Шаг общего комментария расписания (с возможностью пропустить)
- [x] 6.3 Финал: `create_schedule` + слоты, переход на карточку расписания

## 7. Бот — карточки (двухуровневые)

- [x] 7.1 Карточка расписания (Экран 1): клиент, общий комментарий, правило (список активных слотов), кнопки на occurrence окна 14 дней (через `occurrences_in_window`), «Настроить»/«Отменить всё»; пустое окно → сообщение
- [x] 7.2 Карточка одной встречи (Экран 2): клиент, дата, время, эффективный комментарий; «Перенести»/«Отменить»/«Комментарий»/«К расписанию»
- [x] 7.3 Тап на регулярную occurrence в дневном расписании (`src/bot/handlers/schedule.py`, `src/bot/handlers/clients.py`, `src/bot/handlers/reminders.py`) открывает Экран 2 с контекстом `(slot_id, origin_date)`
- [x] 7.4 Переразложить callback-префиксы под schedule/slot/occurrence (например `recur:sched:*`, `recur:slot:*`, `recur:occ:*`); обновить парсеры callback_data

## 8. Бот — настройка и override-действия

- [x] 8.1 «Настроить»: список слотов → по слоту `сменить время / сменить день / удалить`; «добавить день»
- [x] 8.2 «Отменить всё» с подтверждением → `stop_schedule`
- [x] 8.3 «Перенести» occurrence: выбор даты/времени → `move_occurrence`
- [x] 8.4 «Отменить» occurrence с подтверждением → `skip_occurrence`
- [x] 8.5 «Комментарий» occurrence: ввод текста → `set_occurrence_comment` (будущая — override; прошедшая — правка строки `appointments`)

## 9. Тексты

- [x] 9.1 Переписать секцию `[recurring]` в `src/bot/messages.toml` под мульти-слот: создание (цикл, «добавить день»), карточка расписания (правило-список, ближайшие, пустое окно), карточка встречи (перенести/отменить/комментарий), настройка слотов, подтверждения

## 10. Тесты

- [x] 10.1 Домен: `RecurringSchedule`/`RecurringSlot`/`RecurringSlotOverride` и эффективный комментарий
- [x] 10.2 Репозитории: schedule/slot/override CRUD, unique `(slot_id, original_date)`, `insert_occurrence` идемпотентность по `(slot_id, origin_date)`
- [x] 10.3 Ядро occurrence: несколько слотов, два слота в один день, skip/move/comment override, материализация и эффективный комментарий в строке
- [x] 10.4 Use-cases: create_schedule/add_slot/edit_slot (смена дня/времени)/remove_slot (в т.ч. гашение расписания)/stop_schedule, skip/move/set_comment occurrence
- [x] 10.5 Окно 14 дней: `occurrences_in_window` с переносами/пропусками, пустое окно
- [x] 10.6 Чтение/занятость с несколькими слотами и напоминания (slot_id/origin_date/комментарий)
- [x] 10.7 Хендлеры: визард создания, обе карточки, настройка слотов, перенос/отмена/комментарий occurrence; доступ только к своим расписаниям
- [x] 10.8 Проверить 100% покрытие (`make check`)

## 11. Документация и спека

- [x] 11.1 Обновить `openspec/specs/recurring-appointments/spec.md` (через `/opsx:sync` или вручную из дельты)
- [x] 11.2 `docs/database.md` — новые таблицы, FK, индексы, миграция, диаграмма
- [x] 11.3 `docs/bot.md` — визард, двухуровневые карточки, новые callback-префиксы, тексты
- [x] 11.4 `docs/features.md` — мульти-слотовое расписание, окно 14 дней, комментарий уровня встречи
- [x] 11.5 `README.md` — раздел «Ключевые возможности», если меняется верхнеуровневое описание регулярных записей
- [x] 11.6 `openspec validate multi-slot-recurring-schedule` и финальный `make check`
