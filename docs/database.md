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
| `created_at`    | DATETIME | нет  | `lambda: datetime.now(UTC)`.                       |
| `updated_at`    | DATETIME | нет  | Обновляется при переносе (`starts_at`).            |

Индексы:

- `ix_appointments_specialist_starts` — составной по `(specialist_id, starts_at)`. Обслуживает ленту специалиста (будущие/история по времени) и по левому префиксу — «все мои».
- `ix_appointments_client_starts` — составной по `(client_id, starts_at)`. Обслуживает списки записей в карточке клиента.

Решения по схеме:

- `starts_at` хранится в UTC; граница «будущее/история» и группировка по дням считаются по календарному дню в `timezone` специалиста, а не по времени сервера.
- Удаление записи — жёсткое (физический `DELETE`), в отличие от клиентов; архива нет.
- Перенос меняет только `starts_at` (и `updated_at`); `comment` и `client_id` не трогаются.
- Пагинация истории — паттерн «`LIMIT page_size + 1`» (без `COUNT`), как у архива клиентов.

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
| `created_at`       | DATETIME    | нет  | `lambda: datetime.now(UTC)`.                           |
| `updated_at`       | DATETIME    | нет  | Обновляется при любом изменении полей/статуса.         |

Индексы:

- `ix_clients_specialist_status` — составной по `(specialist_id, status)`. Обслуживает выборки списков по статусу и (по левому префиксу) «все мои». Архив дополнительно сортируется по `archived_at` убыванию (свежие сверху) и листается через `LIMIT/OFFSET`; на текущих объёмах сортировку делает SQLite в памяти, отдельный индекс по `archived_at` пока не нужен (YAGNI).

Решения по схеме:

- Контакт хранится «плоскими» полями (один основной родитель + `extra_contacts`), без отдельной таблицы контактов — YAGNI; второй родитель идёт в свободный текст.
- Удаления нет: неактивный клиент уходит в `archived`, данные карточки сохраняются.
- Минимум при создании валидируется в `services` (имя ребёнка + имя контакта + хотя бы один из `contact_phone`/`contact_telegram`), а не в БД — правило не зависит от способа ввода.

## Миграции

- Каталог: `alembic/versions/`.
- `0001_initial.py` — создаёт таблицу `specialists` и оба индекса.
- `0002_clients.py` — создаёт таблицу `clients`, FK на `specialists.id` и индекс `ix_clients_specialist_status`.
- `0003_appointments.py` — создаёт таблицу `appointments` (FK на `specialists` и `clients`, два индекса) и добавляет в `specialists` колонки настроек расписания (`timezone`, `day_start`, `day_end`, `slot_minutes`) с `server_default`. Down-ревизия удаляет таблицу и колонки.
- `0004_working_days.py` — добавляет в `specialists` колонку `working_days` (`String`, `server_default="0,1,2,3,4"` — Пн–Пт). Down-ревизия — `drop_column`.
- Применение: `make run` запускает `alembic upgrade head` перед стартом бота. Та же команда есть в `make create_invite`.
- Async-URL (`sqlite+aiosqlite://`) автоматически переключается на sync-вариант (`sqlite://`) внутри `alembic/env.py`.

## Конкурентность

В SQLite запись сериализуется на уровне базы. Возможные сценарии:

- Два `/start` для одного токена подряд: первый ставит `welcomed_at`, второй видит `welcomed_at IS NOT NULL` и возвращает `ALREADY_WELCOMED` без изменений.
- Один Telegram-аккаунт пробует второе приглашение: `UNIQUE`-индекс по `telegram_chat_id` поднимает `IntegrityError`. Этот кейс редкий, требует ручного разбора администратором.
