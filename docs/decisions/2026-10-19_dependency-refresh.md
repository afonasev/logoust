# Обновление зависимостей — 2026-10-19

## Задача и границы

По команде человека обновлены зависимости приложения, разработки и pre-commit hooks. Baseline — `main`, `610b37c`; рабочая ветка — `codex/update-dependencies-20261019`. Python остаётся 3.13, проверка выполнена на CPython 3.13.11/macOS arm64. Рабочая БД, secrets и production не использовались; бот и deploy не запускались.

## Решение

`uv lock --upgrade` обновил все пакеты в разрешённых диапазонах. Ruff закреплён на 0.17.1 и синхронизирован с hook. `ty==0.0.89` добавлен в dev-группу: раньше `uv run ty` использовал глобальную alpha-версию, не описанную в проекте. Остальные уже актуальные пакеты сохранены.

Pydantic остаётся на 2.13.5 и pydantic-core на 2.46.5: aiogram 3.31.0 требует `pydantic>=2.4.1,<2.14`. Более новые 2.14.1/2.50.1 нельзя принудительно устанавливать поверх этого контракта. `uv tree --outdated` показывает только эти два связанных ограничения.

Pre-commit: pre-commit-hooks v6.0.0 уже актуален; validate-pyproject обновлён 0.25 → 0.26, uv hooks 0.11.2 → 0.13.1, Ruff v0.15.8 → v0.17.1. Сам pre-commit 4.7.0 актуален. Глобальные инструменты и Python не обновлялись.

### Версии lockfile

| Пакет | До | После |
| --- | --- | --- |
| aiogram | 3.28.2 | 3.31.0 |
| aiohappyeyeballs | 2.6.2 | 2.7.1 |
| aiohttp | 3.13.5 | 3.14.5 |
| alembic | 1.18.4 | 1.20.0 |
| annotated-types | 0.7.0 | 0.8.0 |
| anyio | 4.13.0 | 4.15.1 |
| certifi | 2026.5.20 | 2026.10.18 |
| coverage | 7.14.1 | 7.16.2 |
| greenlet | 3.5.1 | 3.5.6 |
| idna | 3.16 | 3.20 |
| iniconfig | 2.3.0 | 2.3.1 |
| mako | 1.3.12 | 1.4.3 |
| markupsafe | 3.0.3 | 3.0.4 |
| multidict | 6.7.1 | 7.1.0 |
| packaging | 26.2 | 26.3 |
| pluggy | 1.6.0 | 1.7.0 |
| propcache | 0.5.2 | 0.5.4 |
| pydantic | 2.13.4 | 2.13.5 |
| pydantic-core | 2.46.4 | 2.46.5 |
| pydantic-settings | 2.14.1 | 2.16.0 |
| pygments | 2.20.0 | 2.21.0 |
| pytest | 9.0.3 | 9.1.1 |
| python-dotenv | 1.2.2 | 1.2.4 |
| ruff | 0.15.8 | 0.17.1 |
| sqlalchemy | 2.0.50 | 2.1.5 |
| structlog | 25.5.0 | 26.1.0 |
| ty | отсутствовал | 0.0.89 |
| typing-extensions | 4.15.0 | 4.16.0 |
| typing-inspection | 0.4.2 | 0.4.4 |
| watchfiles | 1.2.0 | 1.3.0 |
| yarl | 1.24.2 | 1.25.1 |

## Совместимость без изменения продуктовых контрактов

- SQLAlchemy 2.1 больше не устанавливает greenlet по умолчанию; проект уже объявляет greenlet напрямую. `autoflush=False` сохраняется.
- Для DML rowcount уточнён тип результата через `cast(CursorResult, result)`; для nullable UTC-хелперов добавлены overloads. Алгоритмы запросов и их порядок не менялись.
- Уточнены типы форматтера логов, аргумента processor, persisted slot id и тестовых клавиатур. Намеренные mypy-подавления в тестовых doubles перенесены в точные имена правил ty; лишние подавления удалены.
- Ruff с прежними `ALL`/`preview=true` потребовал имена правил и `ruff: ignore`. Выполнена механическая миграция исходных подавлений и эквивалентных ignore-настроек; `unsafe-fixes=true` сохранён. Autouse fixture очистки root logger сохранена с локальным исключением нового stylistic-правила: отключать её означало бы менять изоляцию тестов.
- Глобальные настройки ty, pytest, coverage и все существующие assertions сохранены. Изменения Python преимущественно относятся к комментариям и аннотациям, без новой пользовательской функциональности; OpenSpec behaviour не меняется.

## Проверки и свидетельства

Минимально достаточный набор расширен до всего pytest, потому что одновременно обновлены общие runtime-зависимости, SQLAlchemy, aiogram, logging и тестовый стек. Текущие правила допускают full для неигрового приложения в согласованном scope.

Первый прогон `uv run --locked pytest tests -n auto`: 868 passed, но итог **FAIL** — coverage 99.97% вместо 100%. Новый coverage обнаружил непроверенную раннюю ветку `_consumption_keyboard` для пустого отчёта. Добавлен тест, который требует `None` для пустого отчёта; существующие assertions не ослаблены.

Итоговый `uv run --locked pytest tests -n auto`: **869 passed**, statement/branch coverage **100.00%**, 21.78 s. Проверены временные SQLite миграции и репозитории в штатном suite; рабочая БД не затронута.

`uv run --locked ruff check`, `uv run --locked ruff format --check`, `uv run --locked ty check`, `git diff --check` — PASS после адаптации imports. Структурное сравнение с baseline подтвердило сохранность всех прежних assert-выражений, настроек pytest/coverage/ty и Ruff unsafe/preview. Полный pre-commit по всем отслеживаемым файлам — PASS. Первый hook-прогон исправил отсутствовавший финальный newline в alembic/README и завершился FAIL; после сохранения этого форматирования повторный прогон прошёл.

Нормализация AST всех src-модулей подтвердила неизменность управляющих конструкций и выражений относительно baseline после удаления аннотаций, overloads, импортов и identity cast. Машинная сводка: [QA evidence](../qa/2026-10-19-dependency-refresh.json).

Непроверенные gates: Linux/VPS runtime, Telegram-доставка с реальным токеном, человеческая приёмка и deployment. Их отсутствие не подменяется локальным PASS.

Источники: [обновление lockfile uv](https://docs.astral.sh/uv/concepts/projects/sync/), [SQLAlchemy 2.1 migration](https://docs.sqlalchemy.org/en/21/changelog/migration_21.html), [aiogram 3.31.0 changelog](https://docs.aiogram.dev/en/v3.31.0/changelog.html), [синтаксис подавлений ty](https://docs.astral.sh/ty/suppression/).


## Совмещение с PR #7 перед merge

Перед merge PR #8 в main появился PR #7 (main 7261c1b): распознавание /start и обновление клавиатуры. Ветка зависимостей совмещена с этим snapshot. Единственный текстовый конфликт в src/bot/handlers/start.py разрешён сохранением актуального helper _send_client_confirmation и его аудита; новые noqa в start.py и keyboard_refresh.py мигрированы к эквивалентным Ruff names.

Ruff, formatter, ty и сравнение runtime AST с main 7261c1b — PASS. Повторный полный pytest: **891 passed**, **100.00% statement/branch coverage**, 43.86 s. Полный повтор обоснован изменением исходного snapshot: общие зависимости теперь проверены вместе с новой клавиатурой и дополнительными сценариями /start. Рабочая БД и production не затронуты.
