from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.infrastructure.db import Base, build_engine, build_session_factory


async def test_build_engine_and_session_factory(tmp_path):
    db_path = tmp_path / "smoke.db"
    engine = build_engine(f"sqlite+aiosqlite:///{db_path}")
    factory = build_session_factory(engine)
    assert isinstance(factory, async_sessionmaker)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with factory() as session:
        assert isinstance(session, AsyncSession)
    await engine.dispose()
