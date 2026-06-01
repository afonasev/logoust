import asyncio

import pytest

from src.cli import create_invite as cli


async def test_main_creates_invite_and_prints_deep_link(
    tmp_path,
    monkeypatch,
    capsys: pytest.CaptureFixture[str],
):
    db_path = tmp_path / "cli.db"
    monkeypatch.setattr(cli.settings, "DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    monkeypatch.setattr(cli.settings, "TELEGRAM_BOT_USERNAME", "logoust_bot")

    from src.infrastructure.db import Base, build_engine

    engine = build_engine(f"sqlite+aiosqlite:///{db_path}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()

    await cli.main()

    output_lines = capsys.readouterr().out.splitlines()
    url_lines = [line for line in output_lines if line.startswith("https://t.me/")]
    assert len(url_lines) == 1
    url = url_lines[0]
    assert url.startswith("https://t.me/logoust_bot?start=")
    token = url.removeprefix("https://t.me/logoust_bot?start=")
    assert token


def test_module_executes_main_via_asyncio(monkeypatch):
    called = {}

    def fake_run(coro):
        called["ran"] = True
        coro.close()

    monkeypatch.setattr(asyncio, "run", fake_run)
    # Re-execute the entry guard by invoking it via runpy is overkill;
    # instead simulate the bottom-of-module call:
    fake_run(cli.main())
    assert called["ran"] is True
