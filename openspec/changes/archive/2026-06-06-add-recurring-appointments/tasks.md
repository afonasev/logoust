<!--
Срез A (1–8): правило серии + занятость + материализованная история + создание + стоп.
Срез B (9–11): пропуск/перенос отдельной даты.
Документация (12) — в конце, по факту реализации.
-->

## 1. Домен: сущности и разворот серии

- [x] 1.1 `src/domain/recurring.py`: dataclass `RecurringAppointment` (id, specialist_id, client_id, weekday, time_hhmm, comment, active, start_date, materialized_through, created_at, updated_at) и `RecurringException` (id, series_id, original_date, new_starts_at, created_at)
- [x] 1.2 `src/domain/recurring.py`: протоколы `RecurringRepo` (add, list_active_for_specialist, get_for_specialist, set_active, set_materialized_through) и `RecurringExceptionsRepo` (upsert, list_for_series, list_for_specialist)
- [x] 1.3 `src/domain/schedule.py`: `next_weekday_on_or_after(today, weekday) -> date` (ближайший день недели ≥ today)
- [x] 1.4 `src/domain/schedule.py`: `series_occurrences(start_date, weekday, range_start, range_end) -> list[date]` (даты повтора в полуинтервале, шаг 7 дней)
- [x] 1.5 Тесты домена: `next_weekday_on_or_after` (сегодня как нужный день, обёртка через неделю), `series_occurrences` (границы диапазона, шаг недели, пустой диапазон)

## 2. Миграция

- [x] 2.1 Alembic-миграция: таблицы `recurring_appointments` и `recurring_exceptions` с FK и `UNIQUE(series_id, original_date)`
- [x] 2.2 Та же миграция: в `appointments` колонки `series_id` (FK nullable), `origin_date` (Date nullable), `UNIQUE(series_id, origin_date)`; downgrade — drop таблиц/колонок/индекса
- [x] 2.3 Обновить `tests/test_migration.py`: новые таблицы/колонки и сохранность существующих разовых записей (`series_id`/`origin_date` = NULL)

## 3. Инфраструктура: ORM и репозитории

- [x] 3.1 ORM-модели `RecurringAppointmentORM`, `RecurringExceptionORM` + `to_domain`, индексы по `(specialist_id, active)`
- [x] 3.2 В `AppointmentORM` добавить `series_id`, `origin_date`; проброс в доменный `Appointment` (поля nullable)
- [x] 3.3 `SqlAlchemyRecurringRepo` и `SqlAlchemyRecurringExceptionsRepo` реализуют протоколы
- [x] 3.4 В `appointments_repo`: метод insert-or-ignore материализованной occurrence по `UNIQUE(series_id, origin_date)` (для settle)
- [x] 3.5 Тесты репозиториев: round-trip серии и исключения; идемпотентность вставки occurrence; дефолт NULL у разовых

## 4. Сервисы: создание, стоп, разворот, settle

- [x] 4.1 `src/services/recurring.py`: `create_series(...)` — `start_date = next_weekday_on_or_after(today, weekday)`, лог `recurring.created`
- [x] 4.2 `src/services/recurring.py`: `stop_series(repo, series_id, specialist_id)` — `active=false`, лог `recurring.stopped`; чужая серия не трогается
- [x] 4.3 `src/services/recurring.py`: `expand_future(series, exceptions, range_start, range_end, tz, today) -> list[occurrence]` — только даты `>= today`, применяет пропуск/перенос (для среза A исключений ещё нет — функция уже учитывает пустой список)
- [x] 4.4 `src/services/recurring.py`: `settle(recurring_repo, exc_repo, appt_repo, specialist_id, today, tz)` — материализует occurrence из `(materialized_through, today)` через insert-or-ignore, обновляет `materialized_through`; идемпотентно
- [x] 4.5 Тесты сервисов: создание (start_date), стоп (active + прошлое не тронуто), settle (материализация прошлого, дневной guard, идемпотентность, конкурентный повтор), expand_future (граница today, шаг недели)

## 5. Сервисы: слияние виртуального будущего в занятость и расписание

- [x] 5.1 `src/services/appointments.py`: `taken_slot_times` учитывает повторы активных серий на выбранный день (только будущие дни)
- [x] 5.2 `src/services/appointments.py`: `list_specialist_day` / `list_specialist_week` подмешивают виртуальные occurrence (с признаком серии) к реальным строкам
- [x] 5.3 `src/services/appointments.py`: `list_client_future` добавляет одну ближайшую occurrence серии (помеченную); `nearest_future_by_client` учитывает ближайший повтор
- [x] 5.4 Тесты: занятость слота повтором; повтор в дне/неделе; ровно одна ближайшая occurrence в карточке клиента; разовые не задвоены
- [x] 5.5 Тест `availability`: повтор активной серии исключает слот из `list_free_windows` (без правок `windows.py` — занятость уже идёт через `taken_slot_times`)

## 6. Бот: создание регулярной записи

- [x] 6.1 `src/bot/messages.toml`: тексты потока создания (кнопка «Регулярная запись», выбор дня недели, заголовки, подтверждение)
- [x] 6.2 `src/bot/handlers/clients.py`: кнопка «Регулярная запись» в карточке клиента
- [x] 6.3 `src/bot/handlers/schedule.py`: FSM-поток создания серии (день недели → время по сетке/произвольно → комментарий), вызов `create_series`
- [x] 6.4 Тесты хендлера: успешное создание по слоту и с произвольным временем/комментарием; некорректное время переспрашивает

## 7. Бот: settle на взаимодействии

- [x] 7.1 `SpecialistMiddleware` (или общий вход специалиста) вызывает `settle` с дневным guard'ом через `materialized_through`
- [x] 7.2 Тест: первое взаимодействие за день материализует прошедшие occurrence, повторное — нет

## 8. Бот: карточка серии и пометка регулярных + стоп

- [x] 8.1 `src/bot/messages.toml`: тексты карточки серии, пометки «регулярная», подтверждения остановки
- [x] 8.2 `src/bot/handlers/schedule.py`: рендер виртуальных occurrence в дне/неделе с пометкой 🔁 и callback `sched:series:<id>:<origin_date>`
- [x] 8.3 `src/bot/handlers/schedule.py`: карточка серии (клиент, день недели, время, комментарий) + кнопка «Остановить серию» с подтверждением
- [x] 8.4 Тесты хендлера: открытие карточки серии; остановка с подтверждением убирает будущее; отмена сохраняет серию; чужая серия недоступна

## 9. Срез A — редактирование серии

- [x] 9.1 `src/services/recurring.py`: `edit_series(repo, series_id, specialist_id, *, weekday, time_hhmm, comment, today)` — обновляет правило; при смене `weekday` пересчитывает `start_date` на ближайший новый день ≥ today и поднимает `materialized_through=today`; лог `recurring.edited`
- [x] 9.2 `src/bot/messages.toml` + `src/bot/handlers/schedule.py`: действие «Изменить серию» в карточке серии (день недели → время → комментарий, тем же пикером)
- [x] 9.3 Тесты: смена времени двигает только будущее (прошлые строки целы); смена дня недели пересчитывает `start_date`/`materialized_through`; чужая серия недоступна

## 11. Срез B — домен/сервисы: пропуск и перенос даты

- [x] 11.1 `src/services/recurring.py`: `skip_date(exc_repo, series_id, specialist_id, original_date)` (исключение, `new_starts_at=NULL`), лог `recurring.date_skipped`
- [x] 11.2 `src/services/recurring.py`: `move_date(exc_repo, series_id, specialist_id, original_date, new_starts_at)` (upsert исключения), лог `recurring.date_moved`
- [x] 11.3 Убедиться, что `expand_future` и `settle` корректно учитывают пропуск (нет occurrence) и перенос (occurrence по `new_starts_at`, исходный слот свободен)
- [x] 11.4 Тесты: пропуск убирает только эту дату; перенос сдвигает только эту дату; занятость исходного слота снимается; соседние повторы не затронуты; материализация после прохождения даты учитывает исключение

## 12. Срез B — бот: действия пропуска и переноса в карточке серии

- [x] 12.1 `src/bot/messages.toml`: тексты кнопок/подтверждений «Пропустить эту дату», «Перенести эту дату»
- [x] 12.2 `src/bot/handlers/schedule.py`: в карточке серии действия пропуска (подтверждение) и переноса (выбор нового времени тем же пикером), относящиеся к `origin_date` из callback
- [x] 12.3 Тесты хендлера: пропуск даты; перенос даты на новое время; чужая серия недоступна

## 13. Документация

- [x] 13.1 `docs/database.md`: таблицы `recurring_appointments`, `recurring_exceptions`, новые колонки/индексы `appointments`, раздел миграции
- [x] 13.2 `docs/bot.md`: поток создания/редактирования регулярной записи, карточка серии, пометка регулярных в расписании
- [x] 13.3 `docs/features.md`: пользовательский сценарий регулярной записи (создание, редактирование, стоп, пропуск/перенос)
- [x] 13.4 `docs/decisions/`: файл с обоснованием модели M (материализованное прошлое + виртуальное будущее, settle без планировщика) и того, что занятость для `availability` идёт через общий `taken_slot_times`
- [x] 13.5 `README.md`: упомянуть регулярные записи в «Ключевых возможностях»
- [x] 13.6 `make check` зелёный (формат, линт, типы, 100% покрытие)
