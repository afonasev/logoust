# Работа через Codex

Миграция от 2026-10-18. Исходный snapshot: `main`, `37c0b13`.
Изменены правила, skills, документация и маршрут полного pytest hook; логика бота, данные, зависимости и deployment не менялись.

## Карта переноса

| Источник Claude | Codex / решение |
| --- | --- |
| `CLAUDE.md` | Корневой `AGENTS.md`: цель проекта, архитектура, команды, рабочие договорённости |
| `.claude/rules/python.md` | `.agents/rules/python.md` |
| `.claude/rules/bot.md` | `.agents/rules/bot.md`, включая аудит успеха и ошибки доставки |
| `.claude/rules/docs.md` | `.agents/rules/docs.md` |
| `.claude/rules/openspec.md` | `.agents/rules/openspec.md` |
| Восемь `.claude/skills/openspec-*` | `.agents/skills/openspec-*`; имена и metadata происхождения сохранены |
| Восемь `.claude/commands/opsx/*` | Соответствующие `$openspec-*` skills; отдельные копии команд Codex не нужны |
| `.claude/skills/verify` | `.agents/skills/verify`: affected QA вместо автоматического full |
| `.claude/settings.json`, `effortLevel=high` | `.codex/config.toml`: `gpt-6.1-sol` / `medium` по решению человека от 2026-10-11; ограниченная разработка — Luna/medium через выбор в чате |
| `.claude/settings.json`, `permissions.allow` | Не переносится буквально: Claude Bash-allowlist не является конфигурацией Codex; используются права текущей среды, без расширения доступа из репозитория |
| `.claude/settings.json`, плагины | Выключенные superpowers/playwright/frontend-design/context7 не устанавливаются; включённый pyright-lsp — плагин Claude, проект продолжает использовать `ty` |
| `.claude/agents/quick-search.md` | Содержит только `model: haiku`, без задачи и инструкций; аналог не создавался |
| `.claude/skills/playwright-cli` | Без изменений перенесён в `docs/agent-reserve/playwright-cli/`, вне активных skills Codex; решение человека от 2026-10-19 |
| `.claudeignore` | Прямой перенос в выдуманный `.codexignore` не делался; `.gitignore` сохраняет исключения артефактов, `.env`, БД и бэкапов. `uv.lock` нужен для воспроизводимости и не исключается из анализа |
| `.claude/hooks/` | Был только ignored `__pycache__/effort_advisor.cpython-313.pyc`; исходника и подключения hook в settings нет. Устаревший кэш удалён при согласованной очистке |
| `.github/`, OpenSpec specs и архив | Сохранены; исторические ссылки Claude внутри архивов не переписываются |

Каталог `.agents/rules/` — справочники с обязательной маршрутизацией из `AGENTS.md`, а не автоматически поддерживаемый Codex аналог Claude globs.

## Исправленные противоречия

- Старый обязательный `make check` после каждой правки заменён affected QA. Полный набор — только по отдельной команде человека, согласно решению от 2026-10-10.
- В `.pre-commit-config.yaml` только pytest переведён в stage `manual`. Команда `uvx pre-commit run pytest --hook-stage manual --all-files` запускает полный набор и требует отдельного разрешения. Остальные hooks сохранены.
- Порог общего покрытия 100% в `pyproject.toml` сохранён. `--no-cov` применяется только к affected-прогону и не подтверждает общее покрытие.
- Правило «добавлять ALL modified files» уточнено: все файлы **текущей задачи**, без чужих изменений.
- AskUserQuestion/TodoWrite и `/opsx:*` заменены доступными средствами Codex и `$openspec-*`. Поле `compatibility` перенесено из frontmatter в тело skill для совместимости с локальным валидатором.
- В sync skill убран совет писать частичные MODIFIED requirements: локальное правило требует полный текст со всеми сохраняемыми сценариями для безопасного CLI archive.
- Обязательное уточнение любой детали реализации заменено уточнением существенной неопределённости; обычные решения остаются в согласованном scope.
- Требование немедленно сохранять уроки относится к правилам проекта в рамках задачи; глобальная память — только по прямой просьбе человека.

## Первый запуск

1. Открыть этот репозиторий в новом чате Codex. Правила `AGENTS.md` читаются при запуске; текущий чат не подтверждает их автоматическую загрузку в новом запуске.
2. В доверенном проекте Codex использует `.codex/config.toml`; явный выбор модели/effort человеком важнее defaults. Текущий чат и глобальные настройки не менялись.
3. Проверить наличие repo skills в селекторе. Codex обнаруживает `.agents/skills`; если интерфейс не обновился, перезапустить Codex. Это требует проверки интерфейса человеком, а не только синтаксиса файлов.
4. Для разработки: Python 3.13 и `uv`; `make init` устанавливает зависимости и Git hooks. Не запускать `make run`, миграции, full QA или deploy как часть подготовки чата.
5. Пример проверки Python-правки: `uv run pytest --no-cov tests/test_<affected>.py`, с конкретным набором по diff. Для правил/документации достаточно проверки форматов, ссылок и diff.

Примеры вызова: `$verify`, `$openspec-explore`, `$openspec-new-change <имя>`, `$openspec-apply-change <имя>`. Архивирование change — отдельный запрос, `$openspec-archive-change <имя>`.

## Согласованная очистка — 2026-10-19

По решению человека удалены `CLAUDE.md`, `.claude/`, `.claudeignore`, включая пустой quick-search, дубли команд/rules/skills и старые настройки. Их исходники доступны в Git в ветке `codex/migrate-claude-config`, коммит `ca28173`. Удалена устаревшая строка `.claude/worktrees/` из `.gitignore`; каталог worktrees был пуст, из hooks удалён только оставшийся Python-кэш.

Playwright skill и все девять references без изменений перенесены в [резерв](./agent-reserve/playwright-cli/SKILL.md), вне `.agents/skills/`. Резерв не подключён и не является действующими правилами. Перед возможной активацией нужно адаптировать Claude tool permissions, глобальную установку и `close-all`/`kill-all`, чтобы не затрагивать чужие сессии.

Проверка очистки относительно `ca28173`: все 10 файлов резерва побайтово совпадают с исходниками, локальные ссылки вне примеров корректны, legacy-пути отсутствуют. Активные 9 skills, конфигурация Codex, исходники, тесты и зависимости не изменились. `git diff --check` пройден; полный pytest и запуск бота не требовались.

В ходе после миграции все девять repo skills уже появились в каталоге доступных навыков этой сессии Codex. Это подтверждает обнаружение skills средой, но не является проверкой их исполнения или интерфейса нового desktop-чата.

В репозитории не найдены MCP-конфиги `.mcp.json`, settings.local или подключённые source hooks Claude. Глобальные настройки Claude вне репозитория, учётные данные, глобальные Codex plugins/MCP и системная конфигурация не менялись.

## Проверка миграции

Для этой задачи выбран структурный QA от baseline `37c0b13`: парсинг TOML/YAML, проверка manifest всех перенесённых skills, доступности локальных ссылок, отсутствия недоступных Claude tool-вызовов в активных skills, `git diff --check`, pre-commit по затронутым файлам. Изменение Python — только ссылка внутри docstring; исполняемый AST должен остаться равным baseline.

Полный pytest, реальный polling, миграции БД, production-команды и deploy в этот scope не входят. End-to-end исполнение всех OpenSpec workflows не выполняется: оно создаёт/архивирует изменения вне задачи миграции.

Официальная документация OpenAI: [AGENTS.md](https://learn.chatgpt.com/docs/agent-configuration/agents-md), [skills](https://learn.chatgpt.com/docs/build-skills), [project config](https://learn.chatgpt.com/docs/config-file/config-basic).

Результаты на snapshot миграции:

- Все 9 repo skills прошли `skill-creator/scripts/quick_validate.py`.
- TOML/YAML, manual stage pytest, ссылки и отсутствие Claude tool-вызовов в активных skills проверены; исполняемый AST `client_audit.py` совпадает с baseline.
- `uvx pre-commit validate-config`, `git diff --check`, `codex features list` — PASS. Последняя команда проверяет запуск CLI с конфигурацией, но не подтверждает интерфейс нового desktop-чата.
- `uvx pre-commit run` по staged-файлам: secret/large-file/conflict/format/YAML/TOML проверки, Ruff и `ty` — PASS. pytest не запускался; отсутствие Python-тестов в этом отчёте не является runtime-приёмкой.
- Установленный Git hook ссылался на уже отсутствующий cached Python. `uvx pre-commit install` восстановил локальный launcher; сам launcher не хранится в Git.
- Обнаруженные версии: Codex CLI 0.162.1, OpenSpec 1.10.0, uv 0.9.18, pre-commit 4.7.0. Обновление инструментов и зависимостей не проводилось.
- Чужие ignored `.env`, БД, бэкапы и кэши сохранены. Временные worktree, серверы и фоновые процессы задачи не создавались.

Дополнительные настройки для обсуждения без изменений в этой миграции: `unsafe-fixes = true` у Ruff вместе с автоматическим `--fix` в hook/Makefile допускает небезопасные автоисправления; `ty` игнорирует `unresolved-import` и `invalid-key`. Они существовали до миграции; их изменение требует отдельного решения и affected QA.
