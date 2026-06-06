"""Maps a `template_key` to its default text inside the loaded message catalog.

The catalog of *what* is customizable lives in the domain (`CLIENT_TEMPLATES`);
this module is the bot-layer glue that knows *where* each default lives in
`messages.toml`. Keeping it here avoids leaking the BotMessages structure into the
services layer, which only ever receives the resolved default string.
"""

from collections.abc import Callable

from src.bot.messages import BotMessages

# Each key's default is read from the section that owns the feature; payment_reminder
# has no send point yet, so its default is parked in [templates.defaults].
_DEFAULTS: dict[str, Callable[[BotMessages], str]] = {
    "appt_reminder": lambda m: m.reminder.client_text,
    "notify_created": lambda m: m.schedule.notify_created,
    "notify_rescheduled": lambda m: m.schedule.notify_rescheduled,
    "notify_cancelled": lambda m: m.schedule.notify_cancelled,
    "notify_series_created": lambda m: m.schedule.notify_series_created,
    "notify_series_changed": lambda m: m.schedule.notify_series_changed,
    "notify_series_cancelled": lambda m: m.schedule.notify_series_cancelled,
    "payment_reminder": lambda m: m.templates.defaults["payment_reminder"].strip(),
    "invite_forward": lambda m: m.clients.invite_forward,
    "linked": lambda m: m.clients.linked,
}


def template_default(messages: BotMessages, key: str) -> str:
    """The out-of-the-box text for `key` from the message catalog."""
    return _DEFAULTS[key](messages)
