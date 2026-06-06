# База данных

По умолчанию используется SQLite через `aiosqlite` (`sqlite+aiosqlite:///./logoust.db`). Миграции — Alembic, sync-режим. URL переключается на sync-вариант в `alembic/env.py`.

## Схема

### `specialists`

| Колонка             | Тип       | NULL | Замечание                                |
| ------------------- | --------- | ---- | ---------------------------------------- |
| `id`                | INTEGER   | нет  | PK, autoincrement.                       |
| `invite_token`      | VARCHAR(64) | нет | Уникальный криптостойкий токен.          |
| `telegram_chat_id`  | BIGINT    | да   | `NULL` до первого `/start`. Уникальный частичный индекс при `NOT NULL`. |
| `telegram_username` | VARCHAR(64) | да | `from_user.username` на момент `/start`. |
| `welcomed_at`       | DATETIME  | да   | `NULL` ⇒ приглашение не использовано.    |
| `created_at`        | DATETIME  | нет  | `lambda: datetime.now(UTC)`.             |
| `timezone`          | VARCHAR(64) | нет | IANA-таймзона специалиста. Server-default `Asia/Yekaterinburg`. |
| `day_start`         | VARCHAR(5) | нет | Начало рабочего дня `ЧЧ:ММ`. Server-default `09:00`. |
| `day_end`           | VARCHAR(5) | нет | Конец рабочего дня `ЧЧ:ММ`. Server-default `20:00`. |
| `slot_minutes`      | INTEGER   | нет  | Длина слота в минутах. Server-default `60`. |
| `working_days`      | VARCHAR(20) | нет | Рабочие дни недели — канонически отсортированная строка индексов `date.weekday()` (Пн=0…Вс=6), напр. `0,1,2,3,4`. Server-default `0,1,2,3,4` (Пн–Пт). |
| `reminder_enabled`  | BOOLEAN     | нет | Включены ли авто-напоминания клиентам. Server-default `1` (opt-out). |
| `reminder_time`     | VARCHAR(5)  | нет | Настенное `ЧЧ:ММ` ежедневного прохода напоминаний. Server-default `12:00`. |
| `reminder_last_run_on` | DATE     | да  | Дата (в tz) последнего выполненного прохода напоминаний; антидубль/догон. `NULL` — ещё не выполнялся. |
| `morning_notify_enabled` | BOOLEAN | нет | Включена ли утренняя сводка специалисту. Server-default `1` (opt-out). |
| `morning_notify_time` | VARCHAR(5) | нет | Настенное `ЧЧ:ММ` ежедневной утренней сводки. Server-default `10:00`. |
| `morning_notify_last_run_on` | DATE | да  | Дата (в tz) последнего «решения за день» по сводке; антидубль/догон. `NULL` — ещё не выполнялся. |
| `subscription_presets` | VARCHAR(64) | нет | Варианты числа встреч (кнопки) при создании/продлении абонемента — список через запятую, канонизированный (по возрастанию, без повторов). Server-default `4,8,12`. |

Индексы:

- `ix_specialists_invite_token` — `UNIQUE` по `invite_token`.
- `ix_specialists_telegram_chat_id` — `UNIQUE`, частичный (`WHERE telegram_chat_id IS NOT NULL`).

Решения по схеме:

- `invite_token` — единственный идентификатор для сопоставления `/start` ↔ запись. Генерация через `secrets.token_urlsafe(16)` → 22 символа, URL-safe.
- `welcomed_at` служит и таймстампом, и маркером идемпотентности: повторный `/start` по тому же токену не приведёт к повторной записи.
- `telegram_username` — необязательное аудит-поле. Может быть `NULL` для пользователей без публичного username.
- Поле `name` сознательно не вводится — по требованию заказчика.
- Настройки расписания (`timezone`, `day_start`, `day_end`, `slot_minutes`, `working_days`) добавлены с `server_default`, чтобы у уже онбординнутых специалистов сразу была рабочая сетка. `timezone` управляет конверсией настенного времени записей ↔ UTC (см. [решение от 2026-06-04](decisions/2026-06-04_appointment_time_in_utc_per_specialist_tz.md)).
- `working_days` хранится строкой индексов дней недели (а не битмаской), в духе остальных настроек: парсится/канонизуется хелперами `parse_working_days`/`format_working_days` из `domain/schedule.py`, читаемо в БД. Управляет расчётом свободных окон («ближайшие N рабочих дней»).

### `appointments`

Записи клиента на приём. Принадлежат одновременно специалисту и клиенту; все выборки фильтруются по `specialist_id`.

| Колонка         | Тип      | NULL | Замечание                                          |
| --------------- | -------- | ---- | -------------------------------------------------- |
| `id`            | INTEGER  | нет  | PK, autoincrement.                                 |
| `specialist_id` | INTEGER  | нет  | FK → `specialists.id`. Владелец записи.            |
| `client_id`     | INTEGER  | нет  | FK → `clients.id`. Клиент записи.                  |
| `starts_at`     | DATETIME | нет  | Время начала в **aware UTC** (настенное ↔ UTC через `timezone` специалиста). |
| `comment`       | TEXT     | да   | Необязательный комментарий к записи.               |
| `series_id`     | INTEGER  | да   | FK → `recurring_appointments.id`. `NULL` у разовой записи; заполнен у материализованной occurrence серии. |
| `origin_date`   | DATE     | да   | Плановая дата occurrence серии. `NULL` у разовой записи. |
| `created_at`    | DATETIME | нет  | `lambda: datetime.now(UTC)`.                       |
| `updated_at`    | DATETIME | нет  | Обновляется при переносе (`starts_at`).            |

Индексы:

- `ix_appointments_specialist_starts` — составной по `(specialist_id, starts_at)`. Обслуживает ленту специалиста (будущие/история по времени) и по левому префиксу — «все мои».
- `ix_appointments_client_starts` — составной по `(client_id, starts_at)`. Обслуживает списки записей в карточке клиента.
- `uq_appointments_series_origin` — **уникальный** по `(series_id, origin_date)`. Делает материализацию прошедших occurrence (`settle`) идемпотентной: повторная/конкурентная вставка той же occurrence — no-op (`INSERT … ON CONFLICT DO NOTHING`).

Решения по схеме:

- `starts_at` хранится в UTC; граница «будущее/история» и группировка по дням считаются по календарному дню в `timezone` специалиста, а не по времени сервера.
- Удаление записи — жёсткое (физический `DELETE`), в отличие от клиентов; архива нет.
- Перенос меняет только `starts_at` (и `updated_at`); `comment` и `client_id` не трогаются.
- Пагинация истории — паттерн «`LIMIT page_size + 1`» (без `COUNT`), как у архива клиентов.
- `series_id`/`origin_date` оба `NULL` ⇒ разовая запись (как раньше); оба заполнены ⇒ материализованная прошедшая occurrence регулярной серии — история и расписание прошедших дней читают её как обычную запись. FK на `recurring_appointments` объявлен в ORM, но в миграции колонка добавлена без inline-FK: SQLite не обеспечивает FK и не умеет ALTER-ить ограничение без пересоздания таблицы (см. [решение от 2026-06-05](decisions/2026-06-05_recurring_materialized_past_virtual_future.md)).

### `recurring_appointments`

Правило еженедельной регулярной записи («серия»). Принадлежит специалисту и клиенту; повторяется в свой день недели и настенное время бесконечно, пока `active`.

| Колонка                | Тип        | NULL | Замечание                                              |
| ---------------------- | ---------- | ---- | ------------------------------------------------------ |
| `id`                   | INTEGER    | нет  | PK, autoincrement.                                     |
| `specialist_id`        | INTEGER    | нет  | FK → `specialists.id`. Владелец серии.                 |
| `client_id`            | INTEGER    | нет  | FK → `clients.id`. Клиент серии.                       |
| `weekday`              | INTEGER    | нет  | День недели `date.weekday()` (Пн=0…Вс=6).             |
| `time_hhmm`            | VARCHAR(5) | нет  | Настенное время `ЧЧ:ММ` в `timezone` специалиста; в UTC конвертируется отдельно на каждую дату (DST-safe). |
| `comment`              | TEXT       | да   | Необязательный комментарий серии.                     |
| `active`               | BOOLEAN    | нет  | `false` ⇒ серия остановлена: будущие повторы исчезают, прошлые строки остаются. |
| `start_date`           | DATE       | нет  | Первая дата серии (ближайший `weekday` ≥ дня создания); якорь недельной сетки. |
| `materialized_through` | DATE       | нет  | Докуда прошлое уже застывлено в строки `appointments`; дневной guard для `settle`. |
| `created_at`           | DATETIME   | нет  | `lambda: datetime.now(UTC)`.                          |
| `updated_at`           | DATETIME   | нет  | Обновляется при стопе/редактировании.                |

Индексы:

- `ix_recurring_specialist_active` — составной по `(specialist_id, active)`. Обслуживает выборку активных серий специалиста (`settle` и слияние виртуального будущего в чтениях).

### `recurring_exceptions`

Исключение для одной даты серии — унифицированно для пропуска и переноса.

| Колонка         | Тип      | NULL | Замечание                                                       |
| --------------- | -------- | ---- | --------------------------------------------------------------- |
| `id`            | INTEGER  | нет  | PK, autoincrement.                                              |
| `series_id`     | INTEGER  | нет  | FK → `recurring_appointments.id`. Серия исключения.            |
| `original_date` | DATE     | нет  | Плановая дата серии, к которой относится исключение.           |
| `new_starts_at` | DATETIME | да   | `NULL` ⇒ пропуск (occurrence нет); задано (aware UTC) ⇒ перенос на это время. |
| `created_at`    | DATETIME | нет  | `lambda: datetime.now(UTC)`.                                    |

Ограничения:

- `uq_exception_series_date` — `UNIQUE(series_id, original_date)`. Пропуск и перенос одной даты — одна строка (`upsert`): повторный пропуск/перенос перезаписывает `new_starts_at`.

### `appointment_reminders`

Журнал авто-напоминаний клиенту о записи на завтра и его ответа. Occurrence идентифицируется натуральным ключом `(specialist_id, client_id, starts_at)`, поэтому журнал одинаково работает для разовой записи и для виртуального повтора серии (у которого нет строки в `appointments`).

| Колонка         | Тип         | NULL | Замечание                                                          |
| --------------- | ----------- | ---- | ------------------------------------------------------------------ |
| `id`            | INTEGER     | нет  | PK, autoincrement. Используется в `callback_data` кнопок.          |
| `specialist_id` | INTEGER     | нет  | FK → `specialists.id`. Владелец.                                   |
| `client_id`     | INTEGER     | нет  | FK → `clients.id`. Получатель напоминания.                         |
| `starts_at`     | DATETIME    | нет  | Время начала occurrence в **aware UTC**; вместе с `client_id` идентифицирует occurrence. |
| `series_id`     | INTEGER     | да   | `NULL` у разовой записи; задан у (виртуального) повтора серии — для кнопки «Открыть запись». |
| `origin_date`   | DATE        | да   | Плановая дата occurrence серии; `NULL` у разовой.                  |
| `status`        | VARCHAR(16) | нет  | `pending` \| `confirmed` \| `declined` (enum строкой).             |
| `sent_at`       | DATETIME    | нет  | Момент отправки напоминания (aware UTC).                           |
| `responded_at`  | DATETIME    | да   | Момент последнего ответа клиента; `NULL` — ещё не ответил.         |

Ограничения:

- `uq_reminder_occurrence` — `UNIQUE(specialist_id, client_id, starts_at)`. Делает дневной проход идемпотентным (`INSERT … ON CONFLICT DO NOTHING`): повторный минутный тик и рестарт не плодят дубли и не шлют повторно. Этот же уникальный индекс обслуживает чтение статусов по occurrence (левый префикс `specialist_id, client_id`).

### `clients`

Картотека клиентов специалиста (ребёнок + основной контакт родителя). Принадлежит специалисту: каждый видит и меняет только своих.

| Колонка            | Тип         | NULL | Замечание                                              |
| ------------------ | ----------- | ---- | ------------------------------------------------------ |
| `id`               | INTEGER     | нет  | PK, autoincrement.                                     |
| `specialist_id`    | INTEGER     | нет  | FK → `specialists.id`. Владелец карточки.              |
| `child_name`       | VARCHAR(200)| нет  | Имя ребёнка. Обязательное.                             |
| `contact_name`     | VARCHAR(200)| нет  | Имя контакта-родителя. Обязательное.                   |
| `contact_phone`    | VARCHAR(32) | да   | Канон `+7XXXXXXXXXX`; нераспознанный ввод — как введён.|
| `contact_telegram` | VARCHAR(64) | да   | Без ведущего `@`.                                      |
| `extra_contacts`   | TEXT        | да   | Доп. контакты свободным текстом (не нормализуется).    |
| `note`             | TEXT        | да   | Свободная заметка.                                     |
| `status`           | VARCHAR(16) | нет  | `active` \| `archived` (enum строкой).                 |
| `archived_at`      | DATETIME    | да   | Время архивации; `NULL` у активного.                   |
| `invite_token`     | VARCHAR(64) | да   | Токен приглашения клиента в бота; уникален. `NULL` — не приглашён. |
| `telegram_chat_id` | BIGINT      | да   | `chat_id` клиента, захваченный при онбординге. **НЕ** уникален — один аккаунт может быть привязан к нескольким карточкам. |
| `linked_at`        | DATETIME    | да   | Время последней привязки Telegram; `NULL` — не привязан. |
| `created_at`       | DATETIME    | нет  | `lambda: datetime.now(UTC)`.                           |
| `updated_at`       | DATETIME    | нет  | Обновляется при любом изменении полей/статуса.         |

Индексы:

- `ix_clients_specialist_status` — составной по `(specialist_id, status)`. Обслуживает выборки списков по статусу и (по левому префиксу) «все мои». Архив дополнительно сортируется по `archived_at` убыванию (свежие сверху) и листается через `LIMIT/OFFSET`; на текущих объёмах сортировку делает SQLite в памяти, отдельный индекс по `archived_at` пока не нужен (YAGNI).
- `ix_clients_invite_token` — уникальный по `invite_token`. Обслуживает поиск карточки при онбординге клиента по `cli_`-deep-link. Несколько `NULL` допускаются (клиент ещё не приглашён).

Решения по схеме:

- Контакт хранится «плоскими» полями (один основной родитель + `extra_contacts`), без отдельной таблицы контактов — YAGNI; второй родитель идёт в свободный текст.
- Удаления нет: неактивный клиент уходит в `archived`, данные карточки сохраняются.
- Минимум при создании валидируется в `services` (имя ребёнка + имя контакта + хотя бы один из `contact_phone`/`contact_telegram`), а не в БД — правило не зависит от способа ввода.
- `telegram_chat_id` намеренно **не** уникален (в отличие от `specialists`): специалист может тестировать рассылку под своим аккаунтом, привязав его к нескольким карточкам. Маршрутизация оператора идёт через `specialists`, по `clients.telegram_chat_id` никто не ищет.

### `subscriptions`

Абонемент клиента на несколько встреч. Принадлежит клиенту; `specialist_id` денормализован (как в `appointments`) ради дешёвой проверки владельца без джойна. Не более одного абонемента со `status = active` на клиента — инвариант держится в сервисе (создание из одной точки UI). Закрытые записи не удаляются.

| Колонка         | Тип         | NULL | Замечание                                                      |
| --------------- | ----------- | ---- | -------------------------------------------------------------- |
| `id`            | INTEGER     | нет  | PK, autoincrement. Используется в `callback_data`.             |
| `client_id`     | INTEGER     | нет  | FK → `clients.id`. Клиент абонемента.                          |
| `specialist_id` | INTEGER     | нет  | FK → `specialists.id`. Денормализованный владелец.             |
| `purchased`     | INTEGER     | нет  | Всего куплено встреч за жизнь абонемента (растёт при продлении).|
| `remaining`     | INTEGER     | нет  | Текущий остаток. Списание `-1` с нижней границей 0; продление `+N`. |
| `status`        | VARCHAR(16) | нет  | `active` \| `closed` (enum строкой).                           |
| `created_at`    | DATETIME    | нет  | Момент создания (aware UTC).                                   |
| `closed_at`     | DATETIME    | да   | Момент закрытия; `NULL` у активного.                           |

Индексы:

- `ix_subscriptions_client_status` — составной по `(client_id, status)`. Обслуживает поиск активного абонемента клиента (проверка инварианта «один активный» и кнопка на карточке клиента).

Решения по схеме:

- `purchased` и `remaining` — оба кумулятивные счётчики, без отдельной таблицы движений (YAGNI): карточка отвечает на «сколько куплено / сколько осталось» без журнала.
- Инвариант «один активный» держится запросом `client_id = ? AND status = 'active'` в сервисе; partial unique index оставлен на будущее (поток ввода последовательный, гонка двойного создания практически нулевая).
- Абонемент не связан со встречами/расписанием: остаток меняется только ручными действиями.

### `message_templates`

Переопределение специалистом текста клиентского сообщения. Строка есть → это override; строки нет → при рендере берётся дефолт из `messages.toml`. Каталог настраиваемых ключей и их whitelist плейсхолдеров живут в домене (`CLIENT_TEMPLATES`); данными таблица не наполняется.

| Колонка         | Тип         | NULL | Замечание                                                          |
| --------------- | ----------- | ---- | ------------------------------------------------------------------ |
| `id`            | INTEGER     | нет  | PK, autoincrement.                                                 |
| `specialist_id` | INTEGER     | нет  | FK → `specialists.id`. Владелец переопределения.                   |
| `template_key`  | VARCHAR(64) | нет  | Ключ из каталога `CLIENT_TEMPLATES` (например, `appt_reminder`).   |
| `body`          | TEXT        | нет  | Текст специалиста; прошёл строгую валидацию плейсхолдеров.          |

Индексы:

- `uq_message_template_key` — `UNIQUE(specialist_id, template_key)`. Одно переопределение на пару; upsert заменяет, отсутствие строки = дефолт.

Решения по схеме:

- Отдельная таблица, а не JSON-колонка на `specialists`: есть FK-дисциплина и дешёвый точечный сброс одного ключа (`DELETE`).
- Новый клиентский текст в будущем = новый ключ в каталоге + дефолт в `messages.toml`, миграция данных не нужна.

## Миграции

- Каталог: `alembic/versions/`.
- `0001_initial.py` — создаёт таблицу `specialists` и оба индекса.
- `0002_clients.py` — создаёт таблицу `clients`, FK на `specialists.id` и индекс `ix_clients_specialist_status`.
- `0003_appointments.py` — создаёт таблицу `appointments` (FK на `specialists` и `clients`, два индекса) и добавляет в `specialists` колонки настроек расписания (`timezone`, `day_start`, `day_end`, `slot_minutes`) с `server_default`. Down-ревизия удаляет таблицу и колонки.
- `0004_working_days.py` — добавляет в `specialists` колонку `working_days` (`String`, `server_default="0,1,2,3,4"` — Пн–Пт). Down-ревизия — `drop_column`.
- `0005_client_telegram_link.py` — добавляет в `clients` колонки `invite_token`, `telegram_chat_id`, `linked_at` (все nullable) и уникальный индекс `ix_clients_invite_token`. Существующие строки → `NULL` (валидное «не приглашён»). Down-ревизия удаляет индекс и колонки.
- `0006_recurring_appointments.py` — создаёт таблицы `recurring_appointments` (индекс `ix_recurring_specialist_active`) и `recurring_exceptions` (`UNIQUE(series_id, original_date)`); добавляет в `appointments` колонки `series_id`, `origin_date` (обе nullable) и уникальный индекс `uq_appointments_series_origin`. Существующие записи → `series_id`/`origin_date = NULL` (разовые, поведение не меняется). Колонки добавлены без inline-FK (SQLite не ALTER-ит ограничения). Down-ревизия удаляет индекс, колонки и обе таблицы.
- `0007_appointment_reminders.py` — добавляет в `specialists` колонки `reminder_enabled` (server-default `1`), `reminder_time` (server-default `12:00`), `reminder_last_run_on` (nullable); создаёт таблицу `appointment_reminders` (`UNIQUE(specialist_id, client_id, starts_at)`). Существующие специалисты → напоминания включены на 12:00. Down-ревизия удаляет таблицу и три колонки.
- `0008_subscriptions.py` — добавляет в `specialists` колонку `subscription_default` (server-default `8`); создаёт таблицу `subscriptions` (FK на `clients` и `specialists`, индекс `ix_subscriptions_client_status`). Существующие специалисты → `subscription_default = 8`. Down-ревизия удаляет таблицу и колонку.
- `0009_message_templates.py` — создаёт таблицу `message_templates` (FK на `specialists`, `UNIQUE(specialist_id, template_key)`). Данные не наполняются: отсутствие строки = дефолт из `messages.toml`. Down-ревизия удаляет таблицу.
- `0010_subscription_presets.py` — заменяет в `specialists` колонку `subscription_default` (одно число) на `subscription_presets` (список вариантов через запятую, server-default `4,8,12`). Существующие специалисты получают стандартный список; старое значение не переносится. Down-ревизия возвращает `subscription_default` (server-default `8`).
- `0011_morning_digest.py` — добавляет в `specialists` колонки `morning_notify_enabled` (server-default `1`), `morning_notify_time` (server-default `10:00`), `morning_notify_last_run_on` (nullable). Существующие специалисты → утренняя сводка включена на 10:00. Down-ревизия удаляет три колонки.
- Применение: `make run` запускает `alembic upgrade head` перед стартом бота. Та же команда есть в `make create_invite`.
- Async-URL (`sqlite+aiosqlite://`) автоматически переключается на sync-вариант (`sqlite://`) внутри `alembic/env.py`.

## Конкурентность

В SQLite запись сериализуется на уровне базы. Возможные сценарии:

- Два `/start` для одного токена подряд: первый ставит `welcomed_at`, второй видит `welcomed_at IS NOT NULL` и возвращает `ALREADY_WELCOMED` без изменений.
- Один Telegram-аккаунт пробует второе приглашение: `UNIQUE`-индекс по `telegram_chat_id` поднимает `IntegrityError`. Этот кейс редкий, требует ручного разбора администратором.
