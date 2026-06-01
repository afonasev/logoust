# Design: онбординг специалиста через invite-ссылку

## Архитектурная раскладка

Сохраняем Clean Architecture, заявленную в `CLAUDE.md`. Внешние «двери» в приложение — только `bot/` (aiogram-хендлеры) и `cli/` (служебные команды администратора). Веб-слой (HTTP/REST) не планируется.

```
src/
├── config.py                 # pydantic-settings: env → settings
├── logging_setup.py          # уже есть, без изменений по сути
├── __main__.py               # entrypoint: запускает aiogram-бота
│
├── domain/
│   └── specialist.py         # сущность Specialist, протокол репозитория
│
├── services/
│   └── invites.py            # create_invite(), consume_invite()
│
├── infrastructure/
│   ├── db.py                 # async engine, sessionmaker, base
│   └── specialists_repo.py   # SQLAlchemy-реализация репозитория
│
├── bot/
│   ├── __init__.py
│   ├── dispatcher.py         # сборка aiogram Dispatcher
│   ├── handlers/
│   │   └── start.py          # /start <token>
│   ├── messages.py           # загрузчик messages.toml
│   └── messages.toml         # тексты на русском, редактируемый файл
│
└── cli/
    └── create_invite.py      # CLI-вход для `make create_invite`

alembic/
├── env.py
└── versions/
    └── 0001_initial.py       # создание таблицы specialists
```

Правила зависимостей (без изменений к canonical-схеме):

- `domain/` ← ничего извне (только stdlib).
- `services/` ← `domain/`.
- `infrastructure/` ← `domain/` (+ SQLAlchemy).
- `bot/`, `cli/` ← `services/`, `infrastructure/`, `domain/`, `config`. Никогда наоборот.

## Поток данных

### Поток A: создание приглашения

```
admin@shell
   │
   │  make create_invite
   ▼
src/cli/create_invite.py
   │  asyncio.run(...)
   ▼
services.invites.create_invite(repo) -> InviteCreated
   │  token = secrets.token_urlsafe(16)
   │  repo.add(Specialist(invite_token=token, ...))
   ▼
SQLite (table specialists)
   │
   ▼
stdout: https://t.me/<bot_username>?start=<token>
```

### Поток B: приветствие

```
specialist@telegram
   │  переходит по deep-link
   ▼
Telegram API
   │  /start <token>
   ▼
aiogram polling
   │
   ▼
bot.handlers.start  (handler /start)
   │  payload = command.args
   ▼
services.invites.consume_invite(repo, token, chat_id, username)
   │
   │  match repo.find_by_token(token):
   │     None                              -> ConsumeResult.unknown_token
   │     Specialist(welcomed_at != None)   -> ConsumeResult.already_welcomed
   │     Specialist(welcomed_at is None)   -> repo.mark_welcomed(...)
   │                                          ConsumeResult.welcomed
   ▼
handler читает messages.toml и отправляет соответствующий текст
```

## Модель данных

Единственная таблица, появляющаяся в этом изменении:

```sql
CREATE TABLE specialists (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    invite_token        TEXT NOT NULL UNIQUE,
    telegram_chat_id    INTEGER NULL UNIQUE,
    telegram_username   TEXT NULL,
    welcomed_at         DATETIME NULL,
    created_at          DATETIME NOT NULL
);

CREATE UNIQUE INDEX ix_specialists_invite_token ON specialists(invite_token);
CREATE UNIQUE INDEX ix_specialists_telegram_chat_id ON specialists(telegram_chat_id)
    WHERE telegram_chat_id IS NOT NULL;
```

Решения:

- `invite_token` — единственный идентификатор, по которому мы узнаём специалиста на `/start`. Генерация через `secrets.token_urlsafe(16)`. Длина 22 символа, URL-safe, криптографически случайный — этого достаточно для не-перебираемости.
- `telegram_chat_id` — `NULL` до первого `/start`. После — `UNIQUE`, чтобы один и тот же Telegram-аккаунт не оказался привязан к двум приглашениям случайно.
- `telegram_username` — необязательное аудит-поле: сохраняем `from_user.username` при `/start`, чтобы потом было видно «кто это был», когда токены забудутся. Может быть `NULL` (специалист без публичного username).
- `welcomed_at` — `NULL` ⇒ приглашение не использовано. Не-`NULL` ⇒ использовано. Это же поле служит маркером идемпотентности: повторный `/start <token>` не вызывает повторного приветствия.
- `created_at` — обычная служебная метка, `lambda: datetime.now(UTC)` (по правилу из `.claude/rules/python.md`).

Поле `name` не вводим — пользователь явно сказал «без указания имени». Если позже потребуется — отдельной миграцией.

## Конфиг и переменные окружения

`src/config.py` через `pydantic-settings`:

| Переменная             | Тип   | Дефолт                      | Назначение                                                                  |
| ---------------------- | ----- | --------------------------- | --------------------------------------------------------------------------- |
| `TELEGRAM_BOT_TOKEN`   | `str` | — (обязательная)            | Токен бота из BotFather. Без него и бот, и CLI не стартуют.                |
| `TELEGRAM_BOT_USERNAME`| `str` | — (обязательная)            | Без `@`. Нужен CLI, чтобы напечатать корректный deep-link.                  |
| `DATABASE_URL`         | `str` | `sqlite+aiosqlite:///./logoust.db` | Async-драйвер. Alembic дёргает sync-вариант (`sqlite:///./logoust.db`). |
| `LOG_FORMAT`           | `str` | `text`                       | Используется существующим `logging_setup.py`.                              |
| `LOG_LEVEL`            | `str` | `INFO`                       | Аналогично.                                                                 |
| `LOG_FILE_ENABLED`     | `bool`| `false`                      | Аналогично.                                                                 |
| `LOG_DIR`              | `str` | `./logs`                     | Аналогично.                                                                 |
| `LOG_FILE_BACKUP_DAYS` | `int` | `7`                          | Аналогично.                                                                 |

Минимальный `.env` для разработки:

```
TELEGRAM_BOT_TOKEN=...
TELEGRAM_BOT_USERNAME=logoust_assistant_bot
```

Всё остальное берёт дефолты.

## Каталог текстов: `src/bot/messages.toml`

Все формулировки, видимые специалисту, живут в одном TOML-файле и редактируются без перекомпиляции. Загрузчик — `src/bot/messages.py`, читает один раз при старте процесса через `tomllib`, кладёт результат в `frozen dataclass`. Если ключа нет — `RuntimeError` на старте (fail-fast, не «молча шлём None»).

Черновик содержимого:

```toml
[start]
welcome = """
Здравствуйте! Я ассистент логопедического кабинета Logoust.
Буду помогать вам с рутиной — напоминания, заметки, типовые сообщения.
Возможности будут появляться постепенно — я напишу, когда станет что-то новое.
"""

already_welcomed = "Вы уже подключены. Если что-то нужно — пишите."

unknown_token = "Ссылка недействительна или уже использована. Обратитесь к администратору."

no_token = "Похоже, вы открыли бота не по приглашающей ссылке. Обратитесь к администратору."
```

`no_token` нужен на случай, если кто-то открыл бота прямой ссылкой `t.me/logoust_assistant_bot` без payload — `command.args` будет `None`.

Почему TOML, а не YAML или Python-модуль:

- `tomllib` — стандартная библиотека Python 3.11+, без новой зависимости.
- Multi-line strings из коробки (`"""..."""`).
- Без YAML-неоднозначностей с отступами и неявными типами.
- Удобнее редактировать вручную, чем `.py`: не надо думать про экранирование кавычек, нет риска синтаксической ошибки уронить весь бот.

## Библиотеки и зависимости (новые)

| Пакет                    | Зачем                                                                     |
| ------------------------ | ------------------------------------------------------------------------- |
| `aiogram>=3,<4`          | Async Telegram-бот: router, FSM, типизированные апдейты.                  |
| `sqlalchemy>=2.0,<3`     | ORM, async-поддержка.                                                     |
| `aiosqlite`              | Async-драйвер SQLite.                                                     |
| `alembic`                | Миграции (sync — это нормально).                                         |
| `pydantic-settings`      | Конфиг из `.env`/переменных окружения.                                    |
| `pytest-asyncio`         | Тесты async-кода. Уже частично подразумевается тестовым стеком.           |

`structlog` уже стоит — оставляем.

## Async-стратегия

Везде async по умолчанию: aiogram async, SQLAlchemy async (`AsyncSession`), CLI оборачивает корневую корутину в `asyncio.run(...)`. Alembic остаётся sync — `env.py` пользуется обычным engine, миграции выполняются как одношаговая команда.

Отказ от sync-репозитория + `asyncio.to_thread` — потому что иначе пришлось бы таскать два разных стиля сессий через слой сервисов, а aiogram всё равно требует async.

## Идемпотентность и гонки

Сценарий «два `/start` одной ссылки подряд»:

- В `services.invites.consume_invite` читаем запись по `invite_token` внутри транзакции (`SELECT ... WHERE invite_token=?`).
- Если `welcomed_at IS NOT NULL` — возвращаем `already_welcomed`, ничего не пишем.
- Иначе устанавливаем `telegram_chat_id`, `telegram_username`, `welcomed_at = now()` и коммитим.

В SQLite на write-уровне транзакции сериализуются, гонок «одновременная пометка двух разных специалистов одинаковым chat_id» в норме не будет. `UNIQUE`-индекс по `telegram_chat_id` подстрахует: если второй вызов попадёт на ту же запись, увидит `welcomed_at` и не дойдёт до записи; если каким-то чудом параллельный `/start <token>` придёт с тем же chat_id, но на другой токен — словим `IntegrityError`, и логируем как аномалию (этот кейс редкий и неприятный, но не разрушительный).

## Точки расширения, которые мы НЕ строим сейчас

- Команда `/help` — не вводим, чтобы приветствие не врало. Добавим в отдельном изменении вместе с первой реальной фичей.
- Inactivation/revocation токенов — отдельное изменение.
- Webhook-режим — отдельное изменение, когда понадобится прод-деплой.
- Список специалистов через CLI/UI — пока не нужно, в БД одна-две записи и админ помнит, кому отправлял ссылку.

## Что обновляем в документации в этом же PR

- `README.md` — раздел «Ключевые возможности», «Технологический стек» (новые библиотеки), «Команды разработки» (новая цель), «Быстрый старт» (новые env).
- `docs/features.md` — описание сценария онбординга.
- `docs/architecture.md` — упомянуть слой `bot/` и `cli/`.
- `docs/database.md` — таблица `specialists`.
- `docs/decisions/<date>_telegram_invite_onboarding.md` — почему deep-link, почему aiogram 3, почему TOML для текстов.
- После реализации: создать `openspec/specs/specialists/spec.md` с описанием текущего поведения.
