---
globs: ["**/*.py"]
---

## File Structure
- Use absolute imports: `from src.bot.handlers.start import router`

## Type Annotations and Imports
- Use modern type syntax: `list[Specialist]`, `dict[str, str]`, `-> None`
- Use `collections.abc` for abstract types: `from collections.abc import AsyncIterator`
- For async generator helpers that yield nothing, annotate the return type as `AsyncGenerator[None]`

## Naming Conventions
- Functions and variables: `snake_case`
- Classes: `PascalCase`
- Constants: `UPPER_SNAKE_CASE` (rare, use settings instead)
- Database tables: lowercase plural in `__tablename__`

## Database Models (SQLAlchemy 2.0)
- Use `Mapped` type annotation for all columns (not `Column()`)
- Use `mapped_column()` with explicit SQLAlchemy types: `Integer`, `String(200)`, `Text`
- Use `lambda: datetime.now(UTC)` for datetime defaults (not `func.now()`)
- Always define `__tablename__` class variable (lowercase plural)
- Include `__repr__` method for debugging (one-line representation)
- Base class imported from `src.infrastructure.db` (not locally defined)

## Database Sessions
- Async-only: `AsyncSession` from `sqlalchemy.ext.asyncio`, created via `async_sessionmaker` in `src.infrastructure.db`.
- Use `async with session_factory() as session:` — let the context manager close the session, no `finally` blocks.
- Sessionmaker uses `expire_on_commit=False, autoflush=False`.
- Never share an `AsyncSession` across concurrent tasks — open a fresh one per handler/CLI invocation.

## Error Handling
- Use descriptive error messages with context: `f"Specialist with id {specialist_id} not found"`.
- Check for None/empty results before proceeding (no implicit truthiness).
- No bare except clauses — catch specific exceptions only.
- User-facing wording for the bot lives in `src/bot/messages.toml`, not in raised exceptions.

## Testing Guidelines
- Test files in `tests/` directory, prefixed with `test_`.
- Use pytest fixtures from `conftest.py` for the async session factory and the loaded `BotMessages`.
- For aiogram handlers, build the handler call manually with a fake `Message`/`CommandObject` rather than spinning up polling.
- Test both success and failure paths.
- Use descriptive test names: `test_consume_invite_marks_welcomed_and_returns_welcomed`.
- Always `await engine.dispose()` before removing a test DB file in fixtures —
  SQLAlchemy's connection pool holds open handles; without this you get
  `ResourceWarning` → test failure when `-W error` is active.

## Testing Pitfalls

- Module-level singletons (connection managers, caches) need a `clear()` method
  and an `autouse` fixture in `conftest.py` that calls it after each test.
- Before every commit: stage ALL modified files, not just new ones.
  Pre-commit hooks stash unstaged files — tests will fail against an incomplete state.

## Configuration
- Import settings from `src.config`
- Use environment variables for configuration
- Type checker: `ty` (not mypy) — configured in `[tool.ty]` in pyproject.toml

## Code Quality
- Line length: 88 characters (Ruff formatter handles wrapping automatically)
- Max nested blocks: 3 (Ruff `pylint.max-nested-blocks`)
- Max cyclomatic complexity: 8 (Ruff `mccabe.max-complexity`); extract helpers early
- All code must pass 100% test coverage

## Logging

- Use `logging.getLogger(__name__)` — one logger per module, named `logger`.
- Log business events
- Place logs where the relevant data is naturally available: service layer for business events (e.g. invite created, specialist welcomed), adapter layer (`bot/`, `cli/`) for input/output events.
- Log message = dot-separated event name: `specialist.invite_created`, `specialist.welcomed`, etc.
- Pass context via `extra={}`: always include `specialist_id` when available, plus the minimal identifiers needed to trace the event.

## Comments

- Documentation (docstrings, comments) must capture purpose and usage, not just repetition.

## Ruff Gotchas

- `FBT001` — boolean positional args: use keyword-only `*, flag: bool`
- `N806` — variable names in functions must be snake_case (no `TestSession`)
- `C901/PLR0912` — max complexity 8; extract helpers early, don't wait for lint to force it
- `SIM117` — nested `with` → combine: `with A() as a, B() as b:`
- `PLC0415` — all imports must be at module top level
