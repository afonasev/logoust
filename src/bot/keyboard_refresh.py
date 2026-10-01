import hashlib
import logging

from aiogram import Bot
from aiogram.client.session.middlewares.base import (
    BaseRequestMiddleware,
    NextRequestMiddlewareType,
)
from aiogram.methods import SendMessage, TelegramMethod
from aiogram.methods.base import Response, TelegramType
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.bot.handlers.clients import build_main_keyboard
from src.bot.messages import BotMessages
from src.infrastructure.specialists_repo import SqlAlchemySpecialistsRepo

logger = logging.getLogger(__name__)


def keyboard_version(messages: BotMessages) -> str:
    """Детерминированный хеш текущего набора подписей кнопок `build_main_keyboard`.

    Меняется при добавлении/удалении/переименовании любой кнопки и неизменен при
    перезапуске без правок меню — это и есть «версия клавиатуры», которую сверяем
    против последней доставленной специалисту.
    """
    keyboard = build_main_keyboard(messages)
    labels = "\n".join(button.text for row in keyboard.keyboard for button in row)
    # sha256 здесь нужен для стабильного хеша строки между перезапусками, не ради
    # безопасности (usedforsecurity=False снимает ложный флаг линтера).
    digest = hashlib.sha256(labels.encode("utf-8"), usedforsecurity=False)
    return digest.hexdigest()[:32]


class KeyboardRefreshMiddleware(BaseRequestMiddleware):
    """Подкладывает свежую постоянную клавиатуру в исходящее сообщение
    специалисту, чья сохранённая версия набора кнопок устарела, и фиксирует
    новую версию только после успешной отправки.

    Постоянную клавиатуру нельзя «отредактировать» — её обновляет лишь новое
    сообщение, несущее актуальный `reply_markup` (жёсткое ограничение Telegram),
    поэтому перехватываем исходящий `SendMessage` без своего `reply_markup`.
    """

    def __init__(
        self,
        messages: BotMessages,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._messages = messages
        self._session_factory = session_factory
        # Состав кнопок постоянен в пределах жизни процесса — считаем версию один раз.
        self._version = keyboard_version(messages)

    async def __call__(
        self,
        make_request: NextRequestMiddlewareType[TelegramType],
        bot: Bot,
        method: TelegramMethod[TelegramType],
    ) -> Response[TelegramType]:
        specialist_id = await self._stale_specialist_id(method)
        if specialist_id is not None:
            # method — это мутабельная модель SendMessage; подкладываем markup.
            method.reply_markup = build_main_keyboard(self._messages)  # type: ignore[attr-defined]
        response = await make_request(bot, method)
        # Бампим версию только после успешной отправки: при ошибке доставки
        # исключение уйдёт выше до этой строки, и обновление повторится позже.
        if specialist_id is not None:
            async with self._session_factory() as session:
                await SqlAlchemySpecialistsRepo(session).set_keyboard_version(
                    specialist_id, self._version
                )
            logger.info(
                "specialist.keyboard_refreshed",
                extra={"specialist_id": specialist_id},
            )
        return response

    async def _stale_specialist_id(
        self, method: TelegramMethod[TelegramType]
    ) -> int | None:
        # Только новое сообщение без своего markup может нести постоянную
        # клавиатуру: свою разметку других ответов мы не трогаем (там кнопки inline).
        if not isinstance(method, SendMessage) or method.reply_markup is not None:
            return None
        chat_id = method.chat_id
        if not isinstance(chat_id, int):  # отправка по @username — пропускаем
            return None
        async with self._session_factory() as session:
            specialist = await SqlAlchemySpecialistsRepo(session).find_by_chat_id(
                chat_id
            )
        if specialist is None or specialist.keyboard_version == self._version:
            return None
        assert specialist.id is not None  # noqa: S101 — persisted specialists have id
        return specialist.id
