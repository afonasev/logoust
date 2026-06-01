import asyncio
import sys

from src.config import settings
from src.infrastructure.db import build_engine, build_session_factory
from src.infrastructure.specialists_repo import SqlAlchemySpecialistsRepo
from src.logging_setup import setup_logging
from src.services.invites import create_invite


async def main() -> None:
    setup_logging()
    engine = build_engine(settings.DATABASE_URL)
    session_factory = build_session_factory(engine)
    try:
        async with session_factory() as session:
            repo = SqlAlchemySpecialistsRepo(session)
            specialist = await create_invite(repo)
    finally:
        await engine.dispose()
    url = (
        f"https://t.me/{settings.TELEGRAM_BOT_USERNAME}?start={specialist.invite_token}"
    )
    sys.stdout.write(url + "\n")


if __name__ == "__main__":
    asyncio.run(main())
