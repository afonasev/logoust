# Logoust Assistant

Smart-помощник логопедического кабинета Logoust. Снижает рутину специалиста и даёт удобный канал напоминаний и информации для клиентов.

## Ключевые возможности

- **Онбординг специалиста через invite-ссылку.** Администратор создаёт одноразовое приглашение командой `make create_invite`, передаёт специалисту Telegram-ссылку `https://t.me/<bot>?start=<token>`. Подключиться можно двумя способами: перейти по ссылке (бот получит `/start <token>`) либо вставить в чат с ботом голый код или ссылку целиком — бот сам извлечёт токен. По первому валидному токену бот привязывает Telegram-аккаунт к записи и отправляет приветствие. Повторное подключение безопасно — приветствие не дублируется.

## Технологический стек

- **Python 3.13**, async-first.
- **aiogram 3** — Telegram-бот в режиме long-polling.
- **SQLAlchemy 2.0** (async) + **aiosqlite** — ORM и драйвер. По умолчанию SQLite.
- **Alembic** — миграции (sync, отдельный URL).
- **pydantic-settings** — конфиг из `.env`.
- **structlog** — структурированные логи.
- **pytest** + **pytest-asyncio** + **pytest-cov** — тесты, требование 100% покрытия.
- **Ruff** — форматтер и линтер; **ty** — type-checker.
- **uv** — менеджер зависимостей и виртуального окружения.

## Архитектура

Clean Architecture под `src/`:

- `domain/` — pure-Python сущности и протоколы репозиториев.
- `services/` — use-case-функции, зависят только от `domain/`.
- `infrastructure/` — SQLAlchemy ORM, async-сессии, реализации репозиториев.
- `bot/` — aiogram dispatcher, роутеры, хендлеры и каталог пользовательских текстов.
- `cli/` — административные точки входа (например, `create_invite`).

Развёрнутая документация в [`docs/`](./docs/README.md).

## Быстрый старт

1. Установить **Python 3.13** и **uv**.
2. Скопировать `.env.example` в `.env` и заполнить:
   - `TELEGRAM_BOT_TOKEN` — токен бота из BotFather (обязательная).
   - `TELEGRAM_BOT_USERNAME` — username бота без `@` (обязательная). Нужен CLI для печати deep-link.
   - `DATABASE_URL` — по умолчанию `sqlite+aiosqlite:///./logoust.db`.
3. `make init` — установить зависимости и pre-commit-хуки.
4. `make create_invite` — создать приглашение и получить deep-link для специалиста.
5. `make run` — применить миграции и запустить бота.

## Команды разработки

- `make init` — установить зависимости и pre-commit-хуки.
- `make run` — `alembic upgrade head` + запуск бота в long-polling.
- `make create_invite` — создать приглашение специалиста и напечатать deep-link.
- `make check` — формат + линт + type-check + тесты (требуется до commit).
- `make format` / `make lint` / `make type-check` / `make test` — отдельные шаги.
- `make upgrade` — обновить Python, lockfile, pre-commit.
- `make clean` — удалить кэши и `__pycache__`.

## Структура проекта

```
src/
  domain/          # сущности, протоколы
  services/        # use-cases
  infrastructure/  # ORM, async-сессии, репозитории
  bot/             # aiogram-приложение и каталог текстов
  cli/             # точки входа администратора
alembic/           # миграции
docs/              # техническая документация
openspec/          # планы изменений и living-спеки
tests/             # pytest-сьют, 100% покрытие
```
