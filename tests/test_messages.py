from pathlib import Path

import pytest

from src.bot.messages import (
    DEFAULT_MESSAGES_PATH,
    BotMessages,
    load_messages,
)


def test_load_default_messages():
    messages = load_messages(DEFAULT_MESSAGES_PATH)
    assert isinstance(messages, BotMessages)
    assert "Logoust" in messages.start.welcome
    assert messages.start.already_welcomed
    assert messages.start.unknown_token
    assert messages.start.no_token


def test_missing_key_raises(tmp_path: Path):
    bad = tmp_path / "messages.toml"
    bad.write_text(
        '[start]\nwelcome = "hi"\nalready_welcomed = "ok"\nunknown_token = "no"\n',
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match=r"missing key 'start\.no_token'"):
        load_messages(bad)


def test_missing_section_raises(tmp_path: Path):
    bad = tmp_path / "messages.toml"
    bad.write_text("", encoding="utf-8")
    with pytest.raises(RuntimeError, match=r"missing key 'start\.welcome'"):
        load_messages(bad)
