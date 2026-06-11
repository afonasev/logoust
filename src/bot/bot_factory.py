from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession


def build_bot(token: str, proxy_url: str | None = None) -> Bot:
    """Build the Bot, routing Telegram API traffic through a proxy when set.

    Some hosting providers filter Telegram's API IP ranges, which leaves the
    default direct session hanging on `get_me()` and crash-looping the bot.
    Pointing the session at an external proxy that resolves api.telegram.org on
    its own side sidesteps the block and survives Telegram rotating its IPs.
    `proxy_url` uses an http/socks4/socks5 scheme and relies on `aiohttp-socks`.
    """
    session = AiohttpSession(proxy=proxy_url) if proxy_url else None
    return Bot(token=token, session=session)
