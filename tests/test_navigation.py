from typing import Any
from unittest.mock import AsyncMock

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from src.bot.navigation import Navigator


def _kb(label: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=label, callback_data=label)]]
    )


def _navigator(calls: list[tuple[str, str]]) -> Navigator:
    # Each builder records (name, back) so a test can see which one fired, and
    # returns a recognisable screen.
    def builder(name: str):
        async def _b(  # noqa: RUF029
            specialist_id: int, back: str
        ) -> tuple[str, InlineKeyboardMarkup]:
            calls.append((name, back))
            return f"{name}:{specialist_id}", _kb(name)

        return _b

    return Navigator(
        builders={
            "sched:day_view:": builder("day"),
            "recur:card:": builder("series"),
            "clients:card:": builder("client"),
        },
        fallback=builder("today"),
    )


async def test_render_dispatches_by_prefix():
    calls: list[tuple[str, str]] = []
    nav = _navigator(calls)
    text, _ = await nav.render(7, "clients:card:5~clients:active:0")
    assert text == "client:7"
    assert calls == [("client", "clients:card:5~clients:active:0")]


async def test_render_unknown_target_falls_back_to_today():
    calls: list[tuple[str, str]] = []
    nav = _navigator(calls)
    text, _ = await nav.render(7, "")
    assert text == "today:7"
    assert calls == [("today", "")]


async def test_open_after_action_edits_card_then_opens_menu():
    nav = _navigator([])
    message = AsyncMock()
    await nav.open_after_action(
        message,
        result_text="готово",
        back="recur:card:3:2026-06-10",
        specialist_id=1,
        edit=True,
    )
    # Callback flow: the stale card is edited into the result (no keyboard)...
    message.edit_text.assert_awaited_once_with("готово")
    # ...then the menu is sent as a fresh message below it.
    answer_args = message.answer.await_args
    assert answer_args.args[0] == "series:1"
    assert answer_args.kwargs["reply_markup"] is not None


async def test_open_after_action_answers_result_when_not_editing():
    nav = _navigator([])
    message = AsyncMock()
    await nav.open_after_action(
        message,
        result_text="готово",
        back="sched:day_view:2026-06-10",
        specialist_id=1,
        edit=False,
    )
    # Message flow (typed time): the result is a new message, not an edit.
    message.edit_text.assert_not_awaited()
    texts: list[Any] = [c.args[0] for c in message.answer.await_args_list]
    assert texts == ["готово", "day:1"]
