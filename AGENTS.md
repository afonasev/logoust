# logoust_assistant — рабочие правила

## Working boundaries

- Preserve scope, product contracts and others' files/processes. Questions/reviews are read-only; create an owned code worktree only before authorized edits when isolation is needed.
- Select minimal affected checks from recorded baseline/diff/dependencies. Full needs separate human approval for this run, even at merge/release. Preserve test contracts and FAIL/NotRun; never infer human/device acceptance from QA.
- Explicit model/effort wins. Authorized new chats: Sol 6.1/medium; bounded work/subagents: Luna/medium or higher. Astra needs a specific complex decision or explicit choice. No implied delegation/message permission.
- Show real visual alternatives before implementation; concepts do not authorize publication.
- Before QA, delivery or finalization read `.agents/references/execution-policy.md`; it includes preservation/cleanup and reporting. Report branch + SHA and integration separately. Read `.agents/references/portability.md` on a new device or missing dependency. Other references below load only on their stated trigger.

## Проект и архитектура

Logoust Assistant — Telegram-помощник логопедического кабинета: расписание, клиенты, абонементы и напоминания.
Python 3.13, `uv`, aiogram, SQLAlchemy 2.0, SQLite; технический индекс — `docs/README.md`.

- `src/domain/` — чистые сущности и протоколы, без SQLAlchemy и адаптеров.
- `src/services/` — use-cases; зависят только от domain.
- `src/infrastructure/` — ORM, сессии, репозитории и внешние эффекты.
- `src/bot/` и `src/cli/` — входные адаптеры, связывающие сервисы с репозиториями.
- Конфигурация — `src/config.py`; тексты бота — `src/bot/messages.toml`.

## Рабочие договорённости

- Отвечай, пиши документацию и новые спецификации на русском.
- YAGNI: минимальное достаточное изменение без незапрошенных возможностей.
- Давай честную техническую оценку. Уточняй существенную неопределённость требований; обычные детали реализации решай самостоятельно в согласованном scope.
- Комментируй неочевидную причину, ограничение или инвариант, а не пересказывай код.
- При сжатии контекста сохраняй scope, список изменённых файлов, baseline, команды и результаты проверок, незакрытые вопросы.
- Новые правила проекта сохраняй в соответствующий файл `.agents/rules/`, если это относится к текущей задаче. Глобальную память меняй только по прямой просьбе человека.
- Сохраняй чужую работу, локальные `.env`, БД, бэкапы и процессы. Не печатай токены и персональные данные.
- В коммит включай все файлы текущей задачи, но не чужие изменения. Перед финалом выполни `.agents/references/task-cleanup.md`, если он доступен; укажи ветку, commit и состояние интеграции.
- Для чтения и обсуждения используй текущий checkout; отдельный worktree создавай только для разрешённых изменений, если нужна изоляция. Подагенты и новые чаты — только при отдельной авторизации.

## Какие правила читать

Codex не загружает Claude `globs` автоматически. Перед соответствующей работой **обязательно прочитай**:

| Изменения | Правила |
| --- | --- |
| Любой Python, включая tests и alembic | `.agents/rules/python.md` |
| `src/bot/**` | `.agents/rules/bot.md` — аудит обеих веток отправки клиенту |
| README, docs, код, зависимости, Makefile, миграции | `.agents/rules/docs.md` — триггеры обновления документации |
| Python, `src/bot/messages.toml`, спецификации | `.agents/rules/openspec.md` — контрактные изменения и формат specs |

Не переписывай README/docs/specs механически: обновляй по триггерам соответствующих правил.
`openspec/changes/` хранит изменения в работе, `openspec/specs/` — текущее поведение. Обычный вопрос не разрешает создание planning-артефактов.

Перед локальным запуском/QA или изменением зависимостей читай `.agents/references/development.md` (команды, coverage, pre-commit и окружение).
