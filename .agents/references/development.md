## Команды и проверка

- Зависимости: `make init` (`uv sync` + установка pre-commit), без pip.
- Форматирование: `make format`; `make lint` запускает Ruff с `--fix` и меняет файлы.
- Читающие проверки: `uv run ruff check <files>`, `uv run ruff format --check <files>`, `make type-check` (`ty`).
- До запуска QA запиши baseline, affected-набор и причину выбора команд. Для локальной Python-правки: `uv run pytest --no-cov tests/test_<affected>.py`.
- Полный `make check` (format + lint + type-check + все тесты), `make test` и pytest без селектора — только по отдельной явной команде человека. Commit/merge/«доведи до конца» этого разрешения не дают.
- Общий coverage gate остаётся 100%; affected-запуск с `--no-cov` не доказывает общее покрытие. Не ослабляй assertions/seeds/timeouts ради green. Skipped/missing остаётся NotRun.
- Полный pytest pre-commit hook переведён в stage `manual`; `uvx pre-commit run pytest --hook-stage manual --all-files` также требует отдельного разрешения на full.
- `make run` / `make run-reload` применяют миграции и запускают реальный polling; не использовать как smoke-проверку.
- `make create-invite` меняет локальную БД; `make create-invite-prod` — production. Запуск — в явно запрошенном scope.
- `make deploy` меняет VPS, применяет миграции и перезапускает бота. Только по отдельной явной команде на выкатку.
- `make backup` / `make backup-prod` сохраняют локальную/серверную БД; не читать данные без необходимости.
- `make upgrade` меняет Python/deps/hooks; `make clean` удаляет общие кэши. Не запускать как обычную подготовку задачи.

## Skills и настройки Codex

- Repo skills: `.agents/skills/`; вызов `$openspec-new-change`, `$openspec-continue-change`, `$openspec-ff-change`, `$openspec-apply-change`, `$openspec-verify-change`, `$openspec-sync-specs`, `$openspec-archive-change`, `$openspec-explore`, `$verify`.
- Старые `/opsx:*`, AskUserQuestion и TodoWrite — интерфейсы Claude, а не команды Codex. Используй соответствующий skill и доступный инструмент или вопрос в чате.
- Default новых обычных чатов/координаторов — `gpt-6.1-sol` / `medium`; ограниченная разработка — `gpt-6-luna` / `medium` или выше. Явный выбор человека важнее; существующие чаты не перенастраивать.
- `.codex/config.toml` задаёт default `gpt-6.1-sol` / `medium`; выбор модели по типу задачи описан выше. Разрешение создавать чаты из этого не следует.
- Состояние переноса, ограничения и спорные legacy-настройки: `docs/codex-migration.md`.
