# CLAUDE.md

We are developing a smart assistant for the Logoust speech therapy office. Our goal is to reduce the routine and the possibility of careless mistakes for the office specialist, as well as to provide a convenient interface for reminders and information provision to our clients.

## Golden rules for agents

- Answer, write documentation, and specs in Russian
- YAGNI. Best code = no code. No features we don't need now.
- Make every change as simple as possible. Touch minimal code.
- When unsure about implementation details, ALWAYS ask the developer.
- Never agree just to be nice. Honest technical judgment required.
- When compacting, always preserve the full list of modified files and any test commands.

## Commands

- `make init` — install deps and set up pre-commit hooks
- `make check` — format + lint + type-check + test; run before marking any task done
- `make run` — apply migrations and start the Telegram bot (long-polling)
- `make create_invite` — create a specialist invite and print the deep-link to stdout
- `make format` / `make lint` / `make type-check` / `make test` — individual steps
- `make upgrade` — upgrade Python, lockfile, pre-commit hooks
- `make clean` — remove caches and `__pycache__`
- Use `uv` for deps management — not `pip`

## Reasoning effort по фазам (spec-driven)

Базовый уровень проекта — `high` (закреплён в `.claude/settings.json` → `effortLevel`).
От него отклоняемся по фазе. Переключает уровень **человек** командой `/effort` вручную —
автопереключения нет.

| Фаза / тип работы                                                              | effort   |
| ------------------------------------------------------------------------------ | -------- |
| Спека (openspec): кросс-сервисная / critical / security                        | `max`    |
| Спека (openspec): обычная                                                      | `high`   |
| Ревью диффа (`/code-review`, `/security-review`)                               | `high`   |
| Реализация: гонки/конкурентность, миграции данных, внешние интеграции, необратимые операции, сложные алгоритмы | `high`   |
| Реализация по готовой спеке: рутинная                                          | `medium` |

`max` нельзя задать в `settings.json` (только через `/effort max` или env
`CLAUDE_CODE_EFFORT_LEVEL`), поэтому для critical-спек уровень поднимаем вручную.

## Rules and conventions

- If a rule or lesson emerges during development that should be preserved so we don't step on the same rake again, save it immediately to `.claude/rules/` under the relevant file type.
- Non-obvious code must have a comment explaining WHY, not WHAT. A comment is warranted when: the reason for the code is a hidden browser/platform constraint, a subtle invariant, a workaround for a specific bug, or behaviour that would surprise a competent reader. "Why" includes the cause, not just the intent — e.g. "bfcache restores the page without re-running DOMContentLoaded" rather than "refresh data on back navigation".
- When adding a new feature or changing the architecture, update `README.md`, the relevant files in `docs/`, and `openspec/specs/` in the same change.

File-type-specific rules in `.claude/rules/` load automatically (via `globs:` frontmatter) when editing matching files and must be followed:

- `python.md` — backend conventions (SQLAlchemy 2.0, async sessions, testing pitfalls, Ruff gotchas).
- `bot.md` — aiogram layer conventions; in particular, every client-facing message MUST be journalled via the `client_audit` funnel.
- `docs.md` — when and how to update `README.md` and `docs/`.
- `openspec.md` — when and how to update `openspec/specs/`.

`docs/` — technical documentation for developers and agents; index and navigation in `docs/README.md`.

Planning artifacts under `openspec/`: `changes/` — specs for in-flight work; `specs/<capability>/spec.md` — the living spec of current behaviour.

## Project Overview

## Architecture

Clean Architecture under `src/`:

- `domain/` — pure-Python entities and repository protocols. Imports nothing from SQLAlchemy or any adapter library.
- `services/` — use-case functions; depend on `domain` only.
- `infrastructure/` — SQLAlchemy ORM, async sessions, repository implementations, and other side-effectful adapters.
- `bot/` — aiogram dispatcher, routers, handlers, and the catalog of user-facing texts. Wires repositories into services for Telegram input.
- `cli/` — small admin entry points (e.g. `create_invite`) that wire services into the command line.

## Key Files

- `src/config.py` — settings (env-driven)

## Environment

Requires a `.env` file (gitignored) at the project root. Minimum required:

```
```
