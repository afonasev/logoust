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

## Миграции

- Каталог: `alembic/versions/`.
- `0001_initial.py` — создаёт таблицу `specialists` и оба индекса.
- Применение: `make run` запускает `alembic upgrade head` перед стартом бота. Та же команда есть в `make create_invite`.
- Async-URL (`sqlite+aiosqlite://`) автоматически переключается на sync-вариант (`sqlite://`) внутри `alembic/env.py`.

## Конкурентность

В SQLite запись сериализуется на уровне базы. Возможные сценарии:

- Два `/start` для одного токена подряд: первый ставит `welcomed_at`, второй видит `welcomed_at IS NOT NULL` и возвращает `ALREADY_WELCOMED` без изменений.
- Один Telegram-аккаунт пробует второе приглашение: `UNIQUE`-индекс по `telegram_chat_id` поднимает `IntegrityError`. Этот кейс редкий, требует ручного разбора администратором.
