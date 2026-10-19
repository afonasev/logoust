# Проверка specialist-keyboard-refresh — 19 октября 2026

## Снимок и scope

Baseline: `main@b4c74b2`. Реализация в ветке `codex/specialist-keyboard-finish`: перенесена только часть клавиатуры из `656e288`, без прокси. Наработка прокси отложена в `codex/telegram-proxy-deferred@1c4bf06`; исходная ветка не изменялась.

Исправлены аудит ответа подключённому клиенту (`connection_confirmed`, sent/failed), запись версии при явной доставке клавиатуры, повтор неудавшегося исходящего запроса. Зафиксировано выбранное поведение: inline-разметка сохраняется, обновление ждёт подходящего сообщения; `/start` всегда даёт немедленное обновление. Живые specs синхронизированы, change не архивирован.

## Минимальный набор и результаты

Поведение затрагивает `/start`, исходящие запросы Bot, ORM клиентов/специалистов, миграцию `0017`, каталог сообщений и аудит. Соседние проверки приглашений включены из-за общего пути онбординга.

```sh
uv run pytest --no-cov tests/test_start_handler.py tests/test_keyboard_refresh.py tests/test_clients_repo.py tests/test_messages.py tests/test_migration.py tests/test_audit_handlers.py tests/test_audit_repo.py tests/test_audit_service.py tests/test_dispatcher.py tests/test_invites_service.py
```

Результат: **106 passed, 5.49 s**. Включены реальная цепочка middleware сессии aiogram с подменённым транспортом (без сети), сохранение inline-разметки, успешный повтор того же запроса после ошибки, replay приглашения посторонним аккаунтом, аудит sent/Forbidden/BadRequest и upgrade/downgrade временной БД.

- Ruff check и format --check: PASS для 15 изменённых Python-файлов (список берётся из diff baseline).
- `uv run ty check`: PASS.
- `openspec validate specialist-keyboard-refresh --type change`: PASS.
- `openspec validate <name> --type spec`: PASS для `specialists`, `clients`, `audit`, `specialist-keyboard-refresh`.
- `git diff HEAD --check`: PASS.

## Найденные сбои и исправления

- Первая команда включала отсутствующий `tests/test_main.py`: тесты не запускались (NotRun). Селектор исправлен на существующий `tests/test_dispatcher.py`.
- Расширенный набор сначала дал 105 PASS / 1 FAIL: Alembic fileConfig отключал логгеры и ломал последующий caplog-тест приглашений. Отдельный набор приглашений дал 6 PASS. Миграционная фикстура теперь восстанавливает исходные disabled-флаги логгеров; тот же расширенный набор дал 106 PASS. Assertions сохранены.
- Ошибки Ruff и ty при разработке исправлены; итоговые проверки прошли.

## Границы доказательств

Полный pytest и общее 100% покрытие не проверялись; affected-прогон не заменяет coverage gate. Polling, рабочая/production-БД, деплой и реальный Telegram не запускались. Человеческая проверка в Telegram после отдельной выкладки остаётся открытой. Миграция нужна при выкладке, а не для текущего процесса разработки.

Документация aiogram для middleware: https://docs.aiogram.dev/en/latest/api/session/middleware.html. Структура sendMessage подтверждена установленной aiogram 3.28.2 и целевым тестом без сетевого транспорта.
