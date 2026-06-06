from collections.abc import Awaitable, Callable

from aiogram.types import InlineKeyboardMarkup, Message

# A builder turns a return target into a ready-to-send screen. It receives the
# specialist id and the raw `back` callback string (e.g. "clients:card:5~...") and
# parses whatever it needs from it; it returns the same (text, keyboard) pair the
# target's own handler would render.
NavBuilder = Callable[[int, str], Awaitable[tuple[str, InlineKeyboardMarkup]]]


class Navigator:
    """Re-opens a `back` target by callback prefix, so an action living in one
    router (e.g. deleting an appointment in `schedule`) can render a menu owned by
    another (e.g. the client card in `clients`) without re-dispatching a synthetic
    callback through the dispatcher. Builders are registered by callback prefix; an
    unknown or empty target falls back to today's schedule.
    """

    def __init__(self, builders: dict[str, NavBuilder], fallback: NavBuilder) -> None:
        self._builders = builders
        self._fallback = fallback

    async def render(
        self, specialist_id: int, back: str
    ) -> tuple[str, InlineKeyboardMarkup]:
        for prefix, builder in self._builders.items():
            if back == prefix or back.startswith(prefix):
                return await builder(specialist_id, back)
        return await self._fallback(specialist_id, back)

    async def open_after_action(
        self,
        message: Message,
        *,
        result_text: str,
        back: str,
        specialist_id: int,
        edit: bool,
    ) -> None:
        """Post-action navigation: leave a standalone result message in the chat
        history, then open the origin menu as the freshest screen — never a
        dead-end screen whose only button is "Back".

        `edit=True` (callback flows) turns the now-stale card into the result
        message, dropping its dead buttons; `edit=False` (a flow finished by a typed
        message) sends the result as a new message, since the bot's own card is not
        the message in hand and cannot be edited here.
        """
        if edit:
            await message.edit_text(result_text)
        else:
            await message.answer(result_text)
        text, keyboard = await self.render(specialist_id, back)
        await message.answer(text, reply_markup=keyboard)
