from collections.abc import Awaitable, Callable
import logging
import re

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import CommandObject, CommandStart
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.bot.client_audit import record_client_message
from src.bot.deeplink import CLIENT_TOKEN_PREFIX
from src.bot.handlers.clients import build_main_keyboard
from src.bot.keyboard_refresh import keyboard_version
from src.bot.messages import BotMessages
from src.domain.audit import AuditEvent, DeliveryStatus
from src.domain.client import Client
from src.infrastructure.clients_repo import SqlAlchemyClientsRepo
from src.infrastructure.message_templates_repo import SqlAlchemyMessageTemplatesRepo
from src.infrastructure.specialists_repo import SqlAlchemySpecialistsRepo
from src.services.clients import link_client_by_token
from src.services.invites import ConsumeResult, consume_invite
from src.services.message_templates import resolve_template

logger = logging.getLogger(__name__)

StartHandler = Callable[[Message, CommandObject], Awaitable[None]]
TokenHandler = Callable[[Message], Awaitable[None]]

# Invite token = secrets.token_urlsafe(16) -> exactly 22 URL-safe characters.
# Accept both a bare code and a full pasted deep-link (.../?start=<token>) so a
# specialist can connect by copying either the link or just the code.
_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{22}")
_START_PARAM = "start="


def extract_client_token(text: str) -> str | None:
    """Bare client token from a `cli_`-prefixed code or deep-link, else None."""
    candidate = text.strip()
    if _START_PARAM in candidate:
        candidate = candidate.split(_START_PARAM, 1)[1].split("&", 1)[0].strip()
    if not candidate.startswith(CLIENT_TOKEN_PREFIX):
        return None
    bare = candidate[len(CLIENT_TOKEN_PREFIX) :]
    match = _TOKEN_RE.fullmatch(bare)
    return match.group(0) if match else None


def extract_token(text: str) -> str | None:
    """Pull an invite token out of arbitrary text, or None if it is not token-like.

    Accepts a bare token and a deep-link of the form
    `https://t.me/<bot>?start=<token>`. The strict length/alphabet match keeps the
    fallback handler from reacting to ordinary chat messages.
    """
    candidate = text.strip()
    if _START_PARAM in candidate:
        candidate = candidate.split(_START_PARAM, 1)[1].split("&", 1)[0].strip()
    match = _TOKEN_RE.fullmatch(candidate)
    return match.group(0) if match else None


async def _consume_and_reply(
    message: Message,
    token: str,
    messages: BotMessages,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Apply the token and reply to the specialist based on the onboarding result.

    The caller must ensure `message.from_user` is not None.
    """
    assert message.from_user is not None  # ruff: ignore[assert] — guaranteed by caller
    async with session_factory() as session:
        repo = SqlAlchemySpecialistsRepo(session)
        result = await consume_invite(
            repo,
            token,
            chat_id=message.from_user.id,
            username=message.from_user.username,
        )

    if result is ConsumeResult.WELCOMED:
        await _send_specialist_keyboard(
            message, messages.start.welcome, messages, session_factory
        )
    elif result is ConsumeResult.ALREADY_WELCOMED:
        await _send_specialist_keyboard(
            message, messages.start.already_welcomed, messages, session_factory
        )
    else:
        await message.answer(messages.start.unknown_token)


async def _link_client_and_reply(
    message: Message,
    token: str,
    messages: BotMessages,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Bind the client's Telegram to a card by token, then reply to the client.

    The caller must ensure `message.from_user` is not None.
    """
    assert message.from_user is not None  # ruff: ignore[assert] — guaranteed by caller
    async with session_factory() as session:
        client = await link_client_by_token(
            SqlAlchemyClientsRepo(session),
            token,
            chat_id=message.from_user.id,
            username=message.from_user.username,
        )
        if client is None:
            await message.answer(messages.clients.link_unknown)
            return
        # The confirmation can be customized by the owning specialist.
        text = await resolve_template(
            SqlAlchemyMessageTemplatesRepo(session),
            specialist_id=client.specialist_id,
            key="linked",
            default=messages.clients.linked,
        )
    await _send_client_confirmation(
        message, client, text, AuditEvent.WELCOME, session_factory
    )


async def _send_client_confirmation(
    message: Message,
    client: Client,
    text: str,
    event: AuditEvent,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    assert client.id is not None  # ruff: ignore[assert] — client is persisted
    status, error = DeliveryStatus.SENT, None
    try:
        await message.answer(text)
    except (TelegramForbiddenError, TelegramBadRequest) as exc:
        status, error = DeliveryStatus.FAILED, str(exc)
    await record_client_message(
        session_factory,
        specialist_id=client.specialist_id,
        client_id=client.id,
        event=event,
        text=text,
        status=status,
        error=error,
    )


async def _send_specialist_keyboard(
    message: Message,
    text: str,
    messages: BotMessages,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await message.answer(text, reply_markup=build_main_keyboard(messages))
    assert message.from_user is not None  # ruff: ignore[assert] — checked by caller
    async with session_factory() as session:
        repo = SqlAlchemySpecialistsRepo(session)
        specialist = await repo.find_by_chat_id(message.from_user.id)
        # Использованный инвайт может повторить другой аккаунт. Ответ
        # постороннему не означает доставку клавиатуры владельцу приглашения.
        if specialist is None:
            return
        assert specialist.id is not None  # ruff: ignore[assert] — persisted specialist
        await repo.set_keyboard_version(specialist.id, keyboard_version(messages))
    logger.info("specialist.keyboard_refreshed", extra={"specialist_id": specialist.id})


async def _greet_known_or_no_token(
    message: Message,
    messages: BotMessages,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Bare `/start`: recognise the sender by chat_id and reply by role.

    Specialist → re-send the welcome with a fresh keyboard and record the current
    version; else a linked client → confirm; else the unchanged `no_token`. No new
    specialist rows are ever created. The caller ensures `message.from_user` is set.
    """
    assert message.from_user is not None  # ruff: ignore[assert] — guaranteed by caller
    chat_id = message.from_user.id
    async with session_factory() as session:
        specialist = await SqlAlchemySpecialistsRepo(session).find_by_chat_id(chat_id)
    if specialist is not None:
        assert specialist.id is not None  # ruff: ignore[assert] — persisted specialists have id
        await _send_specialist_keyboard(
            message, messages.start.already_welcomed, messages, session_factory
        )
        return
    async with session_factory() as session:
        client = await SqlAlchemyClientsRepo(session).find_by_chat_id(chat_id)
    if client is not None:
        await _send_client_confirmation(
            message,
            client,
            messages.start.already_linked,
            AuditEvent.CONNECTION_CONFIRMED,
            session_factory,
        )
        return
    await message.answer(messages.start.no_token)


def make_start_handler(
    messages: BotMessages,
    session_factory: async_sessionmaker[AsyncSession],
) -> StartHandler:
    async def handle_start(message: Message, command: CommandObject) -> None:
        if message.from_user is None:
            return

        token = command.args
        if not token:
            await _greet_known_or_no_token(message, messages, session_factory)
            return

        if token.startswith(CLIENT_TOKEN_PREFIX):
            bare = token[len(CLIENT_TOKEN_PREFIX) :]
            await _link_client_and_reply(message, bare, messages, session_factory)
            return

        await _consume_and_reply(message, token, messages, session_factory)

    return handle_start


def make_token_handler(
    messages: BotMessages,
    session_factory: async_sessionmaker[AsyncSession],
) -> TokenHandler:
    async def handle_token(message: Message) -> None:
        if message.from_user is None or message.text is None:
            return

        # Try the client token first: a `cli_`-prefixed payload is unambiguous and
        # must not fall through to specialist onboarding.
        client_token = extract_client_token(message.text)
        if client_token is not None:
            await _link_client_and_reply(
                message, client_token, messages, session_factory
            )
            return

        token = extract_token(message.text)
        if token is None:
            # Not token/link-like: stay silent so we do not answer
            # "invalid link" to every chat message.
            return

        await _consume_and_reply(message, token, messages, session_factory)

    return handle_token


def build_router(
    messages: BotMessages,
    session_factory: async_sessionmaker[AsyncSession],
) -> Router:
    router = Router(name="start")
    router.message.register(
        make_start_handler(messages, session_factory), CommandStart()
    )
    # Fallback for a pasted code/link. Registered after CommandStart so that
    # `/start <token>` is handled by the normal onboarding, not as bare text.
    router.message.register(make_token_handler(messages, session_factory), F.text)
    return router
