from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.message_template import TemplateViolation
from src.infrastructure.message_templates_repo import SqlAlchemyMessageTemplatesRepo
from src.services.message_templates import (
    reset_template,
    resolve_template,
    save_template_override,
)

_SP = 1
_KEY = "appt_reminder"
_DEFAULT = "Запись {date} в {time}."


async def test_resolve_returns_default_without_override(session: AsyncSession):
    repo = SqlAlchemyMessageTemplatesRepo(session)
    text = await resolve_template(repo, specialist_id=_SP, key=_KEY, default=_DEFAULT)
    assert text == _DEFAULT


async def test_resolve_returns_override_when_present(session: AsyncSession):
    repo = SqlAlchemyMessageTemplatesRepo(session)
    await save_template_override(
        repo, specialist_id=_SP, key=_KEY, body="Жду вас {date} {time}!"
    )
    text = await resolve_template(repo, specialist_id=_SP, key=_KEY, default=_DEFAULT)
    assert text == "Жду вас {date} {time}!"


async def test_save_rejects_invalid_and_does_not_store(session: AsyncSession):
    repo = SqlAlchemyMessageTemplatesRepo(session)
    violations = await save_template_override(
        repo, specialist_id=_SP, key=_KEY, body="без времени {date}"
    )
    assert [v.kind for v in violations] == [TemplateViolation.MISSING_REQUIRED]
    # Nothing was written — resolve still falls back to the default.
    assert (
        await resolve_template(repo, specialist_id=_SP, key=_KEY, default=_DEFAULT)
        == _DEFAULT
    )


async def test_save_valid_returns_no_violations(session: AsyncSession):
    repo = SqlAlchemyMessageTemplatesRepo(session)
    assert (
        await save_template_override(
            repo, specialist_id=_SP, key=_KEY, body="ок {date} {time}"
        )
        == []
    )


async def test_reset_removes_override_and_restores_default(session: AsyncSession):
    repo = SqlAlchemyMessageTemplatesRepo(session)
    await save_template_override(
        repo, specialist_id=_SP, key=_KEY, body="мой текст {date} {time}"
    )
    assert await reset_template(repo, specialist_id=_SP, key=_KEY) is True
    assert (
        await resolve_template(repo, specialist_id=_SP, key=_KEY, default=_DEFAULT)
        == _DEFAULT
    )


async def test_reset_without_override_is_noop(session: AsyncSession):
    repo = SqlAlchemyMessageTemplatesRepo(session)
    assert await reset_template(repo, specialist_id=_SP, key=_KEY) is False
