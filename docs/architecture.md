# Архитектура

Clean Architecture: домен в центре, инфраструктура и адаптеры по краям. Веб-слой (HTTP) не планируется — единственный канал общения со специалистом это Telegram.

## Слои

```
src/
├── config.py                 # pydantic-settings: env → settings
├── logging_setup.py          # structlog + logging
├── __main__.py               # entrypoint: запускает aiogram-бота
│
├── domain/                   # сущности, протоколы репозиториев
│   ├── specialist.py
│   └── client.py
│
├── services/                 # use-cases, depend on domain
│   ├── invites.py
│   └── clients.py
│
├── infrastructure/           # ORM, async-сессии, репозитории
│   ├── db.py
│   ├── specialists_repo.py
│   └── clients_repo.py
│
├── bot/                      # adapter-слой: aiogram dispatcher + хендлеры
│   ├── dispatcher.py
│   ├── handlers/
│   │   ├── start.py
│   │   └── clients.py
│   ├── messages.py
│   └── messages.toml
│
└── cli/                      # adapter-слой: административные команды
    └── create_invite.py
```

## Правила зависимостей

- `domain/` импортирует только stdlib.
- `services/` импортирует `domain/`.
- `infrastructure/` импортирует `domain/` (+ SQLAlchemy).
- `bot/`, `cli/` — adapter-слои. Импортируют `services/`, `infrastructure/`, `domain/`, `config`. Никогда наоборот.

`bot/` и `cli/` — две «двери» в приложение: пользовательский Telegram-канал и административный shell. Обе через `services/` дёргают одну и ту же бизнес-логику.

Бизнес-правила (валидация минимума клиента, нормализация телефона/Telegram) живут в `domain/`/`services/`, а не в хендлерах — чтобы не зависеть от способа ввода. Это держит дверь открытой для второго адаптера (например, Telegram Mini App) поверх тех же use-cases без переписывания логики.

Авторизация в `bot/`-канале — через aiogram inner-middleware `SpecialistMiddleware` (`bot/handlers/clients.py`): резолвит специалиста по `chat_id` и инжектит `specialist_id` в хендлеры, отсекая неонбординнутых.

## Поток данных: онбординг

```
admin@shell ──► make create_invite ──► cli/create_invite.main()
                                        │
                                        ▼
                                services.invites.create_invite(repo)
                                        │
                                        ▼
                                SQLite (specialists)
                                        │
                                        ▼
                                stdout: deep-link URL


specialist@telegram ──► /start <token> ──► bot/handlers/start.handle_start
                                            │
                                            ▼
                                    services.invites.consume_invite(repo, token, chat_id, username)
                                            │
                                            ▼
                                    SQLite (specialists) — атомарное обновление
                                            │
                                            ▼
                                    bot отправляет текст из messages.toml
```

Async везде: aiogram async, SQLAlchemy `AsyncSession`, CLI оборачивает корневую корутину через `asyncio.run`. Alembic остаётся sync (отдельный URL без `+aiosqlite`).

## Конфигурация

`src/config.py` — `pydantic-settings` поверх `.env`.

| Переменная             | Тип   | Дефолт                                | Назначение |
| ---------------------- | ----- | ------------------------------------- | ---------- |
| `TELEGRAM_BOT_TOKEN`   | `SecretStr` | — (обязательная)                 | Токен бота из BotFather. |
| `TELEGRAM_BOT_USERNAME`| `str` | — (обязательная)                      | Username бота без `@`, нужен CLI для deep-link. |
| `DATABASE_URL`         | `str` | `sqlite+aiosqlite:///./logoust.db`    | Async-URL. Alembic читает sync-вариант. |
| `LOG_FORMAT`           | `str` | `text`                                | `text` или `json`. |
| `LOG_LEVEL`            | `str` | `INFO`                                | Стандартные уровни logging. |
| `LOG_FILE_ENABLED`     | `bool`| `false`                               | Включает файловый handler с ротацией по полуночи UTC. |
| `LOG_DIR`              | `str` | `./logs`                              | Куда писать файлы логов. |
| `LOG_FILE_BACKUP_DAYS` | `int` | `7`                                   | Сколько дней хранить ротированные файлы. |

## Каталог текстов

Все пользовательские тексты — в `src/bot/messages.toml`. Загрузчик `src/bot/messages.py` читает файл один раз при старте через `tomllib`, кладёт в frozen-dataclass `BotMessages`. Отсутствие обязательного ключа → `RuntimeError` на старте, до приёма апдейтов.
