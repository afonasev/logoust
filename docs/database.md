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

Индексы:

- `ix_specialists_invite_token` — `UNIQUE` по `invite_token`.
- `ix_specialists_telegram_chat_id` — `UNIQUE`, частичный (`WHERE telegram_chat_id IS NOT NULL`).

Решения по схеме:

- `invite_token` — единственный идентификатор для сопоставления `/start` ↔ запись. Генерация через `secrets.token_urlsafe(16)` → 22 символа, URL-safe.
- `welcomed_at` служит и таймстампом, и маркером идемпотентности: повторный `/start` по тому же токену не приведёт к повторной записи.
- `telegram_username` — необязательное аудит-поле. Может быть `NULL` для пользователей без публичного username.
- Поле `name` сознательно не вводится — по требованию заказчика.

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
- Применение: `make run` запускает `alembic upgrade head` перед стартом бота. Та же команда есть в `make create_invite`.
- Async-URL (`sqlite+aiosqlite://`) автоматически переключается на sync-вариант (`sqlite://`) внутри `alembic/env.py`.

## Конкурентность

В SQLite запись сериализуется на уровне базы. Возможные сценарии:

- Два `/start` для одного токена подряд: первый ставит `welcomed_at`, второй видит `welcomed_at IS NOT NULL` и возвращает `ALREADY_WELCOMED` без изменений.
- Один Telegram-аккаунт пробует второе приглашение: `UNIQUE`-индекс по `telegram_chat_id` поднимает `IntegrityError`. Этот кейс редкий, требует ручного разбора администратором.
