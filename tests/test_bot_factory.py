from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession

from src.bot.bot_factory import build_bot

# A syntactically valid token keeps Bot.__init__ from rejecting it; no network
# call happens at construction time, so the value is otherwise irrelevant.
_FAKE_TOKEN = "123456:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"


async def test_build_bot_without_proxy_uses_direct_session():
    bot = build_bot(_FAKE_TOKEN)
    try:
        assert isinstance(bot.session, AiohttpSession)
        assert bot.session.proxy is None
    finally:
        await bot.session.close()


async def test_build_bot_with_proxy_configures_session():
    proxy = "socks5://127.0.0.1:1080"
    bot = build_bot(_FAKE_TOKEN, proxy)
    try:
        assert isinstance(bot, Bot)
        assert isinstance(bot.session, AiohttpSession)
        assert bot.session.proxy == proxy
    finally:
        await bot.session.close()
