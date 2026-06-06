from dataclasses import dataclass
from unittest.mock import AsyncMock

from aiogram.exceptions import TelegramForbiddenError
from aiogram.filters import CommandObject
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.bot.handlers.start import (
    build_router,
    extract_client_token,
    extract_token,
    make_start_handler,
    make_token_handler,
)
from src.bot.messages import BotMessages
from src.domain.audit import AuditEvent, AuditKind, DeliveryStatus
from src.domain.client import ClientStatus
from src.infrastructure.audit_repo import SqlAlchemyAuditRepo
from src.infrastructure.clients_repo import SqlAlchemyClientsRepo
from src.infrastructure.specialists_repo import SqlAlchemySpecialistsRepo
from src.services.clients import NewClient, add_client, create_client_invite
from src.services.invites import create_invite


async def _client_token(session: AsyncSession, *, specialist_id: int = 1) -> str:
    repo = SqlAlchemyClientsRepo(session)
    client = await add_client(
        repo,
        NewClient(
            specialist_id=specialist_id,
            child_name="Маша",
            contact_name="Мама",
            contact_telegram="masha",
        ),
    )
    assert client.id is not None
    invited = await create_client_invite(
        repo, client_id=client.id, specialist_id=specialist_id
    )
    assert invited is not None
    assert invited.invite_token is not None
    return invited.invite_token


@dataclass(slots=True)
class FakeUser:
    id: int
    username: str | None = None


_DEFAULT_USER = FakeUser(id=42, username="ivanov")


def _fake_message(*, user: FakeUser | None = _DEFAULT_USER) -> AsyncMock:
    msg = AsyncMock()
    msg.from_user = user
    msg.answer = AsyncMock()
    return msg


def _cmd(args: str | None) -> CommandObject:
    return CommandObject(prefix="/", command="start", args=args)


async def test_no_token_branch(
    messages: BotMessages,
    session_factory: async_sessionmaker[AsyncSession],
):
    handler = make_start_handler(messages, session_factory)
    msg = _fake_message()
    await handler(msg, _cmd(None))
    msg.answer.assert_awaited_once_with(messages.start.no_token)


async def test_unknown_token_branch(
    messages: BotMessages,
    session_factory: async_sessionmaker[AsyncSession],
):
    handler = make_start_handler(messages, session_factory)
    msg = _fake_message()
    await handler(msg, _cmd("nope"))
    msg.answer.assert_awaited_once_with(messages.start.unknown_token)


async def test_welcomed_branch(
    messages: BotMessages,
    session_factory: async_sessionmaker[AsyncSession],
    session: AsyncSession,
):
    repo = SqlAlchemySpecialistsRepo(session)
    specialist = await create_invite(repo)

    handler = make_start_handler(messages, session_factory)
    msg = _fake_message()
    await handler(msg, _cmd(specialist.invite_token))
    msg.answer.assert_awaited_once()
    assert msg.answer.await_args.args[0] == messages.start.welcome


async def test_already_welcomed_branch(
    messages: BotMessages,
    session_factory: async_sessionmaker[AsyncSession],
    session: AsyncSession,
):
    repo = SqlAlchemySpecialistsRepo(session)
    specialist = await create_invite(repo)

    handler = make_start_handler(messages, session_factory)
    first = _fake_message()
    await handler(first, _cmd(specialist.invite_token))
    assert first.answer.await_args.args[0] == messages.start.welcome

    second = _fake_message()
    await handler(second, _cmd(specialist.invite_token))
    assert second.answer.await_args.args[0] == messages.start.already_welcomed


async def test_handler_skips_when_no_from_user(
    messages: BotMessages,
    session_factory: async_sessionmaker[AsyncSession],
):
    handler = make_start_handler(messages, session_factory)
    msg = _fake_message(user=None)
    await handler(msg, _cmd("anything"))
    msg.answer.assert_not_awaited()


def test_build_router_registers_start_handler(
    messages: BotMessages,
    session_factory: async_sessionmaker[AsyncSession],
):
    router = build_router(messages, session_factory)
    assert router.name == "start"
    assert len(router.message.handlers) == 2


def test_extract_token_from_bare_code():
    assert extract_token("tqHQOW3p8fPsmbEN3xDoIg") == "tqHQOW3p8fPsmbEN3xDoIg"


def test_extract_token_from_deep_link():
    link = "https://t.me/logoust_test?start=tqHQOW3p8fPsmbEN3xDoIg"
    assert extract_token(link) == "tqHQOW3p8fPsmbEN3xDoIg"


def test_extract_token_strips_surrounding_whitespace():
    assert extract_token("  tqHQOW3p8fPsmbEN3xDoIg\n") == "tqHQOW3p8fPsmbEN3xDoIg"


def test_extract_token_rejects_plain_message():
    assert extract_token("привет, как дела?") is None


def test_extract_token_rejects_wrong_length():
    assert extract_token("short") is None


async def test_token_handler_welcomes_on_pasted_code(
    messages: BotMessages,
    session_factory: async_sessionmaker[AsyncSession],
    session: AsyncSession,
):
    repo = SqlAlchemySpecialistsRepo(session)
    specialist = await create_invite(repo)

    handler = make_token_handler(messages, session_factory)
    msg = _fake_message()
    msg.text = specialist.invite_token
    await handler(msg)
    msg.answer.assert_awaited_once()
    assert msg.answer.await_args.args[0] == messages.start.welcome


async def test_token_handler_welcomes_on_pasted_link(
    messages: BotMessages,
    session_factory: async_sessionmaker[AsyncSession],
    session: AsyncSession,
):
    repo = SqlAlchemySpecialistsRepo(session)
    specialist = await create_invite(repo)

    handler = make_token_handler(messages, session_factory)
    msg = _fake_message()
    msg.text = f"https://t.me/logoust_test?start={specialist.invite_token}"
    await handler(msg)
    msg.answer.assert_awaited_once()
    assert msg.answer.await_args.args[0] == messages.start.welcome


async def test_token_handler_ignores_plain_message(
    messages: BotMessages,
    session_factory: async_sessionmaker[AsyncSession],
):
    handler = make_token_handler(messages, session_factory)
    msg = _fake_message()
    msg.text = "привет"
    await handler(msg)
    msg.answer.assert_not_awaited()


async def test_token_handler_skips_when_no_from_user(
    messages: BotMessages,
    session_factory: async_sessionmaker[AsyncSession],
):
    handler = make_token_handler(messages, session_factory)
    msg = _fake_message(user=None)
    msg.text = "tqHQOW3p8fPsmbEN3xDoIg"
    await handler(msg)
    msg.answer.assert_not_awaited()


async def test_token_handler_skips_when_no_text(
    messages: BotMessages,
    session_factory: async_sessionmaker[AsyncSession],
):
    handler = make_token_handler(messages, session_factory)
    msg = _fake_message()
    msg.text = None
    await handler(msg)
    msg.answer.assert_not_awaited()


# --- client onboarding (cli_-prefixed deep-link) --------------------------------


def test_extract_client_token_from_bare_prefixed_code():
    assert (
        extract_client_token("cli_tqHQOW3p8fPsmbEN3xDoIg") == "tqHQOW3p8fPsmbEN3xDoIg"
    )


def test_extract_client_token_from_deep_link():
    link = "https://t.me/logoust_test?start=cli_tqHQOW3p8fPsmbEN3xDoIg"
    assert extract_client_token(link) == "tqHQOW3p8fPsmbEN3xDoIg"


def test_extract_client_token_rejects_specialist_token():
    # A bare specialist token has no cli_ prefix.
    assert extract_client_token("tqHQOW3p8fPsmbEN3xDoIg") is None


def test_extract_client_token_rejects_malformed_prefixed():
    assert extract_client_token("cli_short") is None


async def test_start_links_client_on_cli_token(
    messages: BotMessages,
    session_factory: async_sessionmaker[AsyncSession],
    session: AsyncSession,
):
    token = await _client_token(session)
    handler = make_start_handler(messages, session_factory)
    msg = _fake_message()
    await handler(msg, _cmd(f"cli_{token}"))
    msg.answer.assert_awaited_once_with(messages.clients.linked)


async def test_link_confirmation_records_message_sent(
    messages: BotMessages,
    session_factory: async_sessionmaker[AsyncSession],
    session: AsyncSession,
):
    token = await _client_token(session)
    handler = make_start_handler(messages, session_factory)
    await handler(_fake_message(), _cmd(f"cli_{token}"))
    async with session_factory() as s:
        rows = await SqlAlchemyAuditRepo(s).list_for_specialist(1, limit=10, offset=0)
    assert [(r.kind, r.event, r.status) for r in rows] == [
        (AuditKind.MESSAGE, AuditEvent.WELCOME, DeliveryStatus.SENT)
    ]


async def test_link_confirmation_delivery_failure_records_failed(
    messages: BotMessages,
    session_factory: async_sessionmaker[AsyncSession],
    session: AsyncSession,
):
    token = await _client_token(session)
    handler = make_start_handler(messages, session_factory)
    msg = _fake_message()
    msg.answer.side_effect = TelegramForbiddenError(method=None, message="blocked")  # type: ignore[arg-type]
    await handler(msg, _cmd(f"cli_{token}"))  # the failure must not propagate
    async with session_factory() as s:
        rows = await SqlAlchemyAuditRepo(s).list_for_specialist(1, limit=10, offset=0)
    assert rows[0].status is DeliveryStatus.FAILED
    assert rows[0].error


async def test_start_unknown_cli_token(
    messages: BotMessages,
    session_factory: async_sessionmaker[AsyncSession],
):
    handler = make_start_handler(messages, session_factory)
    msg = _fake_message()
    await handler(msg, _cmd("cli_unknowntokenunknowna"))
    msg.answer.assert_awaited_once_with(messages.clients.link_unknown)


async def test_token_handler_links_client_on_pasted_cli_link(
    messages: BotMessages,
    session_factory: async_sessionmaker[AsyncSession],
    session: AsyncSession,
):
    token = await _client_token(session)
    handler = make_token_handler(messages, session_factory)
    msg = _fake_message()
    msg.text = f"https://t.me/logoust_test?start=cli_{token}"
    await handler(msg)
    msg.answer.assert_awaited_once_with(messages.clients.linked)


async def test_specialist_token_still_works_alongside_clients(
    messages: BotMessages,
    session_factory: async_sessionmaker[AsyncSession],
    session: AsyncSession,
):
    # A specialist token (no cli_ prefix) must still onboard the specialist.
    specialist = await create_invite(SqlAlchemySpecialistsRepo(session))
    handler = make_start_handler(messages, session_factory)
    msg = _fake_message()
    await handler(msg, _cmd(specialist.invite_token))
    assert msg.answer.await_args.args[0] == messages.start.welcome


async def test_one_account_links_two_client_cards(
    messages: BotMessages,
    session_factory: async_sessionmaker[AsyncSession],
    session: AsyncSession,
):
    repo = SqlAlchemyClientsRepo(session)
    tokens = []
    for name in ["Маша", "Петя"]:
        client = await add_client(
            repo,
            NewClient(
                specialist_id=1,
                child_name=name,
                contact_name="Мама",
                contact_telegram="x",
            ),
        )
        assert client.id is not None
        invited = await create_client_invite(repo, client_id=client.id, specialist_id=1)
        assert invited is not None
        assert invited.invite_token is not None
        tokens.append(invited.invite_token)

    handler = make_start_handler(messages, session_factory)
    for token in tokens:
        msg = _fake_message(user=FakeUser(id=900))
        await handler(msg, _cmd(f"cli_{token}"))
        msg.answer.assert_awaited_once_with(messages.clients.linked)

    bound = {
        c.telegram_chat_id for c in await repo.list_by_status(1, ClientStatus.ACTIVE)
    }
    assert bound == {900}
