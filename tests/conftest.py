import os

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("TELEGRAM_BOT_USERNAME", "test_bot")

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.bot.messages import (
    DEFAULT_MESSAGES_PATH,
    BotMessages,
    load_messages,
)

# Import every ORM module so Base.metadata.create_all sees all tables and can
# resolve cross-table foreign keys (appointments → clients/specialists).
import src.infrastructure.appointments_repo  # noqa: F401
import src.infrastructure.clients_repo  # noqa: F401
from src.infrastructure.db import (
    Base,
    build_engine,
    build_session_factory,
)
import src.infrastructure.specialists_repo  # noqa: F401


@pytest.fixture
async def session_factory(
    tmp_path: Path,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    db_path = tmp_path / "test.db"
    engine = build_engine(f"sqlite+aiosqlite:///{db_path}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = build_session_factory(engine)
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest.fixture
async def session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with session_factory() as s:
        yield s


@pytest.fixture
def messages() -> BotMessages:
    return load_messages(DEFAULT_MESSAGES_PATH)
