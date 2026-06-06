> Предусловие: реализуется после слияния `add-subscriptions`, `daily-digest`,
> `appointment-notify`. Номера миграции/имена ниже скорректировать под фактическое состояние.

## 1. Данные и миграция

- [x] 1.1 Добавить в домен `Specialist` (`src/domain/specialist.py`) поля `payment_reminder_enabled: bool = True`, `payment_reminder_time: str = "12:00"`, `payment_reminder_last_run_on: date | None = None` + константы-дефолты
- [x] 1.2 Добавить в домен `Subscription` (`src/domain/subscription.py`) поле `payment_reminded_at: datetime | None = None`
- [x] 1.3 Колонки в ORM специалиста (`src/infrastructure/specialists_repo.py`): `payment_reminder_enabled` (Boolean, server_default true), `payment_reminder_time` (String, server_default '12:00'), `payment_reminder_last_run_on` (Date, nullable)
- [x] 1.4 Колонка в ORM абонемента (`src/infrastructure/subscriptions_repo.py`): `payment_reminded_at` (DateTime(timezone=True), nullable)
- [x] 1.5 Alembic-миграция: добавить четыре колонки с server-default'ами; downgrade — drop колонок
- [x] 1.6 Обновить маппинг ORM↔domain и билдеры специалиста/абонемента в `tests/conftest.py`

## 2. Домен — чистая логика

- [x] 2.1 `is_payment_reminder_due(specialist, now) -> bool`: `payment_reminder_enabled` И `utc_to_wall(now, tz) >= payment_reminder_time` И `payment_reminder_last_run_on != today_in_tz(now, tz)`
- [x] 2.2 `subscription_needs_payment_reminder(sub) -> bool`: `status == active` И `remaining == 0` И `payment_reminded_at is None`
- [x] 2.3 Тесты: due до/после времени, уже обработан сегодня, выключен, догон после простоя; предикат на active/closed × remaining 0/>0 × флаг set/None

## 3. Репозитории

- [x] 3.1 `SpecialistsRepo`: `list_payment_reminder_candidates()` (все с `payment_reminder_enabled`) и `mark_payment_reminder_run(specialist_id, run_on)`
- [x] 3.2 `SubscriptionsRepo`: получение активного абонемента клиента (если ещё нет) и `mark_payment_reminded(subscription_id, at)`
- [x] 3.3 Реализация методов в SqlAlchemy-репозиториях
- [x] 3.4 Тесты репозиториев: выборка только включённых; пометка `last_run_on`; пометка `payment_reminded_at`

## 4. Сброс флага при продлении (capability `subscriptions`)

- [x] 4.1 В use-case продления абонемента (`src/services/subscriptions.py`) сбрасывать `payment_reminded_at = None` (только продление переводит remaining 0 → >0)
- [x] 4.2 Тест: продление абонемента с установленным `payment_reminded_at` обнуляет флаг; «вычесть» флаг не трогает

## 5. Сервис — проход и решение

- [x] 5.1 Use-case `run_payment_reminders_if_due(specialist, appts_repo, subs_repo, clients_repo, now, alert)`: `is_payment_reminder_due` → срез `list_specialist_day(..., tomorrow, series=ctx)` → группировка по `client_id` (ближайшее время для текста) → для клиента активный абонемент → `subscription_needs_payment_reminder` → собрать данные алерта (флаг привязки по `telegram_chat_id`) → `alert(...)` → `mark_payment_reminded(sub_id, now)`; в конце `mark_payment_reminder_run(today)`
- [x] 5.2 Логи `subscription.payment_reminder_alerted` (+ `linked: bool`) с `specialist_id`/`client_id`/`subscription_id`
- [x] 5.3 Тесты сервиса: запись завтра + пустой абонемент привязанного клиента → alert с флагом отправки + пометка; непривязанный → alert без флага + пометка; remaining>0 → нет alert; нет записи завтра → нет alert; уже напоминали → нет alert; не-due → ничего; в любом исходе `mark_payment_reminder_run`

## 6. Фоновый цикл (рефактор `daily-digest`)

- [x] 6.1 Обобщить минутный цикл в `src/__main__.py`: один тик → последовательный прогон джоб (утренняя сводка + напоминание об оплате); весь проход в `try/except Exception` с логом, polling не роняется
- [x] 6.2 Джоба напоминания: открыть сессию, `list_payment_reminder_candidates`, для каждого вызвать сервис; `alert = bot.send_message(chat_id, text, reply_markup=...)` с кнопкой при наличии привязки; ловить `TelegramForbiddenError`/`TelegramBadRequest`
- [x] 6.3 Тест одного прохода с замоканными зависимостями (без реального sleep): due-кандидаты → alert с/без кнопки; не-due → пропуск

## 7. Отправка клиенту (bot)

- [x] 7.1 Колбэк-хендлер `pay:send:<client_id>`: загрузить клиента (владелец — текущий специалист), повторно проверить `telegram_chat_id`; отправить фиксированный шаблон; успех → «отправлено», `TelegramForbiddenError`/`TelegramBadRequest` → «не доставлено», нет привязки → «клиент не привязан»
- [x] 7.2 Регистрация роутера/колбэка в сборке диспетчера
- [x] 7.3 Логи `subscription.payment_reminder_sent` / `subscription.payment_reminder_send_failed`
- [x] 7.4 Тесты: успешная отправка; `TelegramForbiddenError` → «не доставлено», без падения; рейс отвязки → «не привязан»

## 8. Настройки в боте

- [x] 8.1 Тексты в `messages.toml`: тумблер вкл/выкл, кнопка времени, подсказка ввода, ошибка; сообщение специалисту (имя клиента, время записи, превью текста клиенту); подпись кнопки «Отправить»; фиксированный текст клиенту; статусы отправлено/не доставлено/не привязан
- [x] 8.2 Подключить ключи в соответствующих `*Messages` (`src/bot/messages.py`) и `load_messages`
- [x] 8.3 Хендлеры в `src/bot/handlers/settings.py`: тумблер `payment_reminder_enabled`; ввод времени через FSM по образцу `morning_notify_time` с `parse_hhmm`; отображение текущего состояния в `render_settings`
- [x] 8.4 Тесты настроек: тумблер меняет флаг; валидное время сохраняется; некорректное отклоняется

## 9. Документация

- [x] 9.1 `docs/database.md` — четыре новые колонки + миграция
- [x] 9.2 `docs/architecture.md` — общий фоновый цикл с несколькими джобами (обновить раздел из daily-digest)
- [x] 9.3 `docs/bot.md` — настройки напоминания об оплате, сообщение специалисту и кнопка, тексты
- [x] 9.4 `docs/features.md` — сценарий напоминания об оплате
- [x] 9.5 `README.md` — раздел «Ключевые возможности», если затронут
- [x] 9.6 `openspec validate subscription-payment-reminder`

## 10. Финальная проверка

- [x] 10.1 `make check` — формат, линт, типы, тесты со 100% покрытием
- [x] 10.2 Ручной прогон: пустой абонемент + запись завтра → алерт; «Отправить» → клиент получает текст; повторный тик/рестарт/следующий день — без дубля; продление → флаг сброшен, следующее обнуление снова даёт алерт
