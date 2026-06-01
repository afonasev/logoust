# Tasks: онбординг специалиста через invite-ссылку

Порядок ниже не догма — это разумная последовательность, в которой каждый шаг закрывает понятный кусок. Все шаги выполняются в одном PR.

## 1. Инфраструктура и зависимости

- [x] Добавить в `pyproject.toml` зависимости: `aiogram>=3,<4`, `sqlalchemy>=2.0,<3`, `aiosqlite`, `alembic`, `pydantic-settings`. В dev-группу: `pytest-asyncio`.
- [x] Запустить `uv lock` и проверить, что lockfile валиден.
- [x] Создать `src/config.py` на pydantic-settings с переменными из таблицы в `design.md`. Поле `TELEGRAM_BOT_TOKEN` — `SecretStr`.
- [x] Создать `.env.example` (в git) с пустыми обязательными ключами.

## 2. БД и миграции

- [x] Создать `src/infrastructure/db.py`: async-engine, async-sessionmaker, declarative `Base`.
- [x] Инициализировать Alembic: `alembic init alembic`, поправить `env.py` под наш `Base` и `DATABASE_URL` (с переключением на sync-URL для CLI миграций).
- [x] Написать миграцию `0001_initial.py`: таблица `specialists` (см. `design.md`), уникальные индексы.
- [x] Тест: применить миграцию на временную in-memory или временный файл, проверить структуру (`PRAGMA table_info`).

## 3. Domain

- [x] `src/domain/specialist.py`: dataclass-сущность `Specialist` и `Protocol` репозитория (`add`, `find_by_token`, `mark_welcomed`).
- [x] Только stdlib, никаких импортов из SQLAlchemy / aiogram.

## 4. Infrastructure: репозиторий

- [x] `src/infrastructure/specialists_repo.py`: ORM-модель `SpecialistORM` (mapped_column), реализация репозитория поверх `AsyncSession`.
- [x] Маппинг ORM ↔ domain через простой helper `to_domain` (без сторонних библиотек).
- [x] Тесты: добавить специалиста, найти по токену, пометить welcomed; убедиться, что повторная пометка не падает, но не меняет `welcomed_at`.

## 5. Services

- [x] `src/services/invites.py`:
  - [x] `create_invite(repo) -> Specialist` — генерит `secrets.token_urlsafe(16)`, создаёт запись.
  - [x] `ConsumeResult` (enum: `WELCOMED`, `ALREADY_WELCOMED`, `UNKNOWN_TOKEN`).
  - [x] `consume_invite(repo, token, chat_id, username) -> ConsumeResult` — идемпотентно по `welcomed_at`.
- [x] Логирование: `specialist.invite_created`, `specialist.welcomed`, `specialist.invite_replayed`, `specialist.invite_unknown`. `extra={"specialist_id": ..., "token_prefix": token[:6]}`.
- [x] Тесты на все три ветки `ConsumeResult` + успешный `create_invite`.

## 6. Каталог текстов

- [x] `src/bot/messages.toml` с черновиком из `design.md`.
- [x] `src/bot/messages.py`: frozen dataclass `BotMessages` + `load_messages(path) -> BotMessages`. Если ключа нет — `RuntimeError("messages.toml: missing key '<...>'")` на старте.
- [x] Тест: загрузка валидного файла; отсутствие ключа → понятная ошибка.

## 7. Bot

- [x] `src/bot/dispatcher.py`: `build_dispatcher(messages, session_factory) -> Dispatcher`, подключение единственного роутера.
- [x] `src/bot/handlers/start.py`: хендлер `CommandStart(deep_link=True)`.
  - Достаёт `command.args` как токен.
  - Открывает сессию, зовёт `consume_invite`.
  - Шлёт нужный текст по `ConsumeResult` (или `no_token` при пустом payload).
- [x] Тесты хендлера: подделанный `Message`/`CommandObject`, мокнутая фабрика сессий — проверяем все четыре ветки.

## 8. Entrypoint и Makefile

- [x] `src/__main__.py`: загружает `settings`, инициализирует логирование, создаёт `Bot`, `session_factory`, `messages`, `Dispatcher`. Запускает `dp.start_polling(bot)`.
- [x] `src/cli/create_invite.py`: `async def main()` — открывает сессию, вызывает сервис, печатает `https://t.me/{settings.TELEGRAM_BOT_USERNAME}?start={token}`. `if __name__ == "__main__": asyncio.run(main())`.
- [x] `Makefile`: добавить цель `create_invite` (через `uv run python -m src.cli.create_invite`). Цель `run` уже корректна (`alembic upgrade head && uv run python -m src`).

## 9. Тесты и качество

- [x] `tests/conftest.py`: фикстуры async-сессии на временную SQLite-БД, фикстура `Messages`.
- [x] Покрытие 100%, иначе CI красный. Не добавлять `# pragma: no cover` без причины.
- [x] `make check` зелёный.

## 10. Документация

- [x] `README.md`: обновить разделы из таблицы в `design.md`.
- [x] `docs/features.md`: новый раздел «Онбординг специалиста через invite-ссылку».
- [x] `docs/architecture.md`: упомянуть `src/bot/` и `src/cli/` как adapter-слои.
- [x] `docs/database.md`: таблица `specialists`, схема.
- [x] `docs/decisions/<YYYY-MM-DD>_telegram_invite_onboarding.md`: почему deep-link / aiogram 3 / TOML для текстов.

## 11. После реализации

- [x] Создать `openspec/specs/specialists/spec.md` с описанием текущего поведения (онбординг через invite).
- [x] `openspec validate specialists`.
- [x] Архивировать изменение: `openspec archive add-specialist-invite-onboarding`.
