## 1. Данные и миграция

- [x] 1.1 Добавить в домен `Specialist` (`src/domain/specialist.py`) поле `deferred_notify_time: str = "20:00"` и константу `DEFAULT_DEFERRED_NOTIFY_TIME = "20:00"`
- [x] 1.2 Создать доменную сущность `ScheduledClientMessage` (`src/domain/scheduled_message.py`): `id`, `specialist_id`, `client_id`, `chat_id`, `text`, `target_key`, `due_at` (UTC), `status` (enum `queued`/`sent`/`failed`/`cancelled`), `created_at`, `sent_at | None`
- [x] 1.3 ORM-модель `scheduled_client_messages` (`src/infrastructure/scheduled_messages_repo.py`) + колонка `deferred_notify_time` (String(5), server_default `'20:00'`) в ORM-модели специалиста; индексы `(status, due_at)`, `(specialist_id, client_id, status)`, `(specialist_id, target_key, status)`
- [x] 1.4 Alembic-миграция `alembic/versions/0012_deferred_client_notify.py`: создать таблицу `scheduled_client_messages` + добавить колонку `specialists.deferred_notify_time` (server_default `'20:00'`); downgrade — drop колонки и таблицы
- [x] 1.5 Обновить маппинг ORM↔domain специалиста (чтение/запись `deferred_notify_time`) и билдеры/фикстуры специалиста в `tests/conftest.py`

## 2. Домен — чистые функции и протокол

- [x] 2.1 Чистая функция `next_occurrence_utc(hhmm, now, tz)` в `src/domain/schedule.py` (или рядом): настенное `HH:MM` сегодня в tz; если соответствующий UTC `<= now` — +1 сутки; возвращает UTC `datetime`
- [x] 2.2 Чистая функция/предикат «строка готова к отправке» (`due_at <= now` и статус `queued`) — для прохода доставки
- [x] 2.3 Хелпер построения `target_key`: `appt:<id>` / `series:<series_id>` / `series:<series_id>:<origin_date>`
- [x] 2.4 Протокол `ScheduledMessagesRepo` (`src/domain/scheduled_message.py`): `enqueue_superseding(msg) -> (inserted, superseded_due_at | None)`, `list_due(now) -> list`, `mark_sent(id, at)`, `mark_failed(id, at)`, `cancel(id, specialist_id) -> bool`, `list_queued_for_client(specialist_id, client_id) -> list`
- [x] 2.5 Тесты `next_occurrence_utc`: время ещё не прошло → сегодня; прошло → завтра; граница; разные tz
- [x] 2.6 Тесты `target_key` и предиката due

## 3. Репозиторий

- [x] 3.1 Реализовать `SqlAlchemyScheduledMessagesRepo`: `enqueue_superseding` (в одной транзакции перевести прежнюю `queued` строку с тем же `(specialist_id, target_key)` в `cancelled` и вставить новую), `list_due`, `mark_sent`, `mark_failed`, `cancel` (owner-scoped), `list_queued_for_client`
- [x] 3.2 Тесты репозитория: вытеснение прежней по тому же `target_key`; сосуществование разных `target_key`; `list_due` берёт только `queued` с `due_at<=now` по возрастанию; `cancel` чужого не трогает; `list_queued_for_client` только свои `queued`

## 4. Сервисы — постановка, доставка, отмена

- [x] 4.1 Use-case постановки в очередь (`src/services/scheduled_messages.py`): принять снимок текста, `chat_id`, `target_key`, `due_at`; вызвать `enqueue_superseding`; вернуть результат с признаком вытеснения и временем прежней отправки (для сообщения специалисту); лог `appointment.notify_deferred`
- [x] 4.2 Use-case сбора и доставки готовых: `collect_due(now)` → список к отправке; пометка `mark_sent`/`mark_failed`; отправка — в слое `bot/` (сервис без aiogram)
- [x] 4.3 Use-case отмены (`cancel`) и выборки отложенных по клиенту (`list_queued_for_client`) для карточки
- [x] 4.4 Логи `appointment.notify_deferred` / `appointment.notify_failed` с `specialist_id`, `client_id`
- [x] 4.5 Тесты сервисов: постановка с вытеснением и без; сбор due; отмена; выборка по клиенту

## 5. Шедулер — проход доставки

- [x] 5.1 `run_outbox_pass(bot, session_factory, messages, now)` в `src/bot/scheduler.py`: `list_due` → для каждой повторно проверить привязку клиента → `bot.send_message(chat_id, text)` → `mark_sent`; при `TelegramForbiddenError`/`TelegramBadRequest` → `mark_failed` + лог + сообщение специалисту о недоставке (его сбой — глотать)
- [x] 5.2 Подключить `run_outbox_pass` в `_scheduler_loop` (`src/__main__.py`) рядом с reminder/digest, в отдельном `try/except` с логом
- [x] 5.3 Тест одного прохода (без реального sleep, замоканные зависимости): due → отправка + `sent`; сбой → `failed` + сообщение специалисту; отвязанный клиент → не шлём; не-due не трогаются

## 6. Бот — выбор момента отправки (schedule.py)

- [x] 6.1 Под-клавиатура момента после «Да»: «Сейчас» / «в HH:MM» (из `deferred_notify_time`) / «Своё время» — заменяет немедленную отправку на шаге `notify`/`notify_series`
- [x] 6.2 «Сейчас» → существующий немедленный путь (`_send_notify`) без изменений поведения
- [x] 6.3 При нажатии «Да» складывать контекст уведомления (событие, `client_id`, occurrence/правило, `target_key`, снимок текста, `chat_id`) в FSM-данные; под-кнопки момента читают его
- [x] 6.4 «в HH:MM» (пресет) → посчитать `due_at` через `next_occurrence_utc`, поставить в очередь, показать «будет отправлено …»; при вытеснении — «прежняя отправка на … заменена»
- [x] 6.5 «Своё время» → FSM-стейт `Schedule.notify_custom_time`, принять `ЧЧ:ММ` через `parse_hhmm`; невалидно → ошибка и повтор; валидно → как 6.4
- [x] 6.6 Мягкий фолбэк при утере FSM-контекста (другая операция сделала `state.clear()`): под-кнопки отвечают «сессия устарела, повторите», ничего не отправляя
- [x] 6.7 Протянуть устойчивый идентификатор цели (`appointment_id` для разовой — для отмены зафиксировать до удаления; `series_id`/`origin_date` для серий) из обработчиков операций в шаг уведомления
- [x] 6.8 Регистрация новых колбэков/стейта в `build_router`
- [x] 6.9 Тесты: «Да» показывает выбор момента; «Сейчас» шлёт немедленно; пресет/своё ставят в очередь с верным `due_at`; вытеснение проговаривается; невалидное своё время; утерянный контекст → фолбэк; серия и одна дата серии

## 7. Бот — отложенные на карточке клиента (clients.py)

- [x] 7.1 В `_card_view` подгрузить `list_queued_for_client` и отрисовать блок: текст (кратко) + время отправки в tz + кнопка «Отменить» (`clients:dnotify:cancel:<id>`); пустой список → блока нет
- [x] 7.2 Хендлер отмены: owner-scoped `cancel`, перерисовать карточку; регистрация колбэка
- [x] 7.3 Тесты: блок показывается при наличии очереди и скрыт без неё; отмена убирает из списка и не отправляет; чужое отменить нельзя

## 8. Настройки — deferred_notify_time

- [x] 8.1 Расширить `SettingField`/`update_setting` (`src/services/specialists.py`) полем `DEFERRED_NOTIFY_TIME` с валидацией `parse_hhmm`
- [x] 8.2 Пункт в меню настроек (`src/bot/handlers/settings.py`): отображение текущего значения в `render_settings`, кнопка и ввод через FSM по образцу `digest_time`; регистрация колбэка
- [x] 8.3 Тесты настроек: валидное время сохраняется; некорректное отклоняется; отображается в меню

## 9. Тексты

- [x] 9.1 `messages.toml` `[schedule]`: подписи кнопок момента (Сейчас / в {time} / Своё время), подсказка ввода своего времени, статусы «будет отправлено {when}», «прежняя отправка на {when} заменена», «сессия устарела», сообщение специалисту о сбое отложенной доставки
- [x] 9.2 `messages.toml` `[clients]`: заголовок блока отложенных, строка отложенного (текст + время), кнопка «Отменить»
- [x] 9.3 `messages.toml` `[settings]`: кнопка и подсказка времени отложенной отправки
- [x] 9.4 Подключить новые ключи в соответствующие `*Messages` (`src/bot/messages.py`) и `load_messages`

## 10. Документация

- [x] 10.1 `docs/database.md` — таблица `scheduled_client_messages` + колонка `deferred_notify_time` + миграция 0012
- [x] 10.2 `docs/bot.md` — под-шаг выбора момента, блок отложенных на карточке клиента, проход доставки
- [x] 10.3 `docs/architecture.md` — новый проход `run_outbox_pass` в минутном шедулере
- [x] 10.4 `docs/features.md` — сценарий отложенной отправки уведомления
- [x] 10.5 `README.md` — раздел «Ключевые возможности», если затронут

## 11. Финальная проверка

- [x] 11.1 `openspec validate deferred-client-notify`
- [x] 11.2 `make check` — формат, линт, типы, тесты со 100% покрытием
- [x] 11.3 Ручной прогон: создать/перенести запись привязанному клиенту → «Да» → «в 20:00»: проверить отложенную доставку; повторно отложить ту же запись → проговаривается замена; отменить с карточки; «Своё время» ночью → завтра; рестарт процесса до `due_at` → доставка догоняется; сбой доставки → сообщение специалисту
