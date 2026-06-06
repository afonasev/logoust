## 1. Данные и миграция

- [x] 1.1 Добавить в домен `Specialist` (`src/domain/specialist.py`) поля `morning_notify_enabled: bool = True`, `morning_notify_time: str = "10:00"`, `morning_notify_last_run_on: date | None = None` и константы-дефолты
- [x] 1.2 Добавить колонки в ORM-модель специалиста (`src/infrastructure/specialists_repo.py`): `morning_notify_enabled` (Boolean, server_default true), `morning_notify_time` (String, server_default '10:00'), `morning_notify_last_run_on` (Date, nullable)
- [x] 1.3 Alembic-миграция `alembic/versions/0007_morning_digest.py`: добавить три колонки с server-default'ами; downgrade — drop колонок
- [x] 1.4 Обновить маппинг ORM↔domain (чтение/запись новых полей) и фикстуры/билдеры специалиста в `tests/conftest.py`

## 2. Домен — логика «кому пора»

- [x] 2.1 Чистая функция `is_digest_due(specialist, now)` в домене (без БД/таймера): `enabled` И `utc_to_wall(now, tz) >= morning_notify_time` И `morning_notify_last_run_on != today_in_tz(now, tz)`
- [x] 2.2 Расширить протокол `SpecialistsRepo`: `list_digest_candidates()` (все с `morning_notify_enabled`) и `mark_digest_run(specialist_id, run_on)`
- [x] 2.3 Тесты `is_digest_due`: до времени / после времени / уже обработан сегодня / выключен / догон после простоя

## 3. Репозиторий

- [x] 3.1 Реализовать `list_digest_candidates` и `mark_digest_run` в `SqlAlchemySpecialistsRepo`
- [x] 3.2 Тесты репозитория: выборка только включённых; пометка `last_run_on`

## 4. Сервис — сборка и решение

- [x] 4.1 Use-case `send_digest_if_due(repo, appts_repo, specialist, now, send)` в `src/services/` (или новый модуль `digest.py`): проверка `is_digest_due` → `list_specialist_day(..., series=ctx)` на сегодня → если непусто вызвать `send(chat_id, text)` → в любом случае `mark_digest_run(today)`
- [x] 4.2 Рендер текста сводки: заголовок + строки «время — клиент (комментарий)» по возрастанию времени, настенное время/дата в tz; все тексты из `messages.toml`
- [x] 4.3 Логи `specialist.digest_sent` / `specialist.digest_skipped_empty` / `specialist.digest_failed` с `specialist_id`
- [x] 4.4 Тесты сервиса: непустой день → отправка + пометка; пустой день → без отправки + пометка; не-due → ничего; сбой `send` → лог + пометка (без повтора)

## 5. Фоновый таск

- [x] 5.1 `run_digest_loop(bot, session_factory, messages)`: сон до начала следующей минуты → один проход по кандидатам → повтор; весь проход в `try/except Exception` с логом (не ронять polling)
- [x] 5.2 Один проход: открыть сессию, `list_digest_candidates`, для каждого вызвать сервис с `send = bot.send_message`, ловить `TelegramForbiddenError`/`TelegramBadRequest`
- [x] 5.3 Подключить в `src/__main__.py`: `asyncio.gather(dp.start_polling(bot), run_digest_loop(...))`; корректное завершение при остановке
- [x] 5.4 Тест одного прохода с замоканными зависимостями (без реального sleep): кандидаты → вызовы send для due, пропуск для не-due

## 6. Настройки в боте

- [x] 6.1 Тексты в `messages.toml` (`[settings]`): кнопки тумблера вкл/выкл, кнопка времени, подсказка ввода, ошибка; включить в `render_settings` отображение текущего состояния
- [x] 6.2 Подключить ключи в `SettingsMessages` (`src/bot/messages.py`) и `load_messages`
- [x] 6.3 Хендлеры в `src/bot/handlers/settings.py`: тумблер `morning_notify_enabled`; ввод времени через FSM-стейт по образцу `day_start` с `parse_hhmm`; регистрация колбэков в `build_router`
- [x] 6.4 Тексты сводки в `messages.toml` (заголовок, строка встречи, «комментарий») + ключи в соответствующем `*Messages`
- [x] 6.5 Кнопка «Отправить сейчас» в меню настроек: колбэк собирает срез сегодня и шлёт `callback.bot.send_message` в чат специалиста — мимо `is_digest_due`, без вызова `mark_digest_run`; пустой день → алерт «записей нет»; ловить `TelegramForbiddenError`/`TelegramBadRequest`
- [x] 6.6 Тесты настроек: тумблер меняет флаг; валидное время сохраняется; некорректное время отклоняется; «Отправить сейчас» шлёт сводку и НЕ меняет `last_run_on`; пустой день → алерт; отправка при выключенной сводке работает

## 7. Документация

- [x] 7.1 `docs/database.md` — три новые колонки + миграция 0007
- [x] 7.2 `docs/architecture.md` — фоновый asyncio-таск рядом с polling (новый паттерн), точка входа
- [x] 7.3 `docs/bot.md` — настройки утренней сводки и тексты
- [x] 7.4 `docs/features.md` — сценарий утренней сводки специалисту
- [x] 7.5 `README.md` — раздел «Ключевые возможности», если затронут
- [x] 7.6 `openspec validate daily-digest`

## 8. Финальная проверка

- [x] 8.1 `make check` — формат, линт, типы, тесты со 100% покрытием
- [x] 8.2 Ручной прогон: включить сводку, выставить ближайшее время, убедиться, что приходит список на сегодня; пустой день — тишина; повторный тик/рестарт — без дубля
