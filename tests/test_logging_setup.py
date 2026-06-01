import logging
import logging.handlers
from pathlib import Path

import pytest

from src.logging_setup import clean_record, setup_logging


def test_clean_record_strips_ip_prefix():
    out = clean_record(None, "info", {"event": "127.0.0.1:8080 - GET /"})
    assert out["event"] == "GET /"


def test_clean_record_removes_color_message():
    out = clean_record(None, "info", {"event": "ok", "color_message": "x"})
    assert "color_message" not in out
    assert out["event"] == "ok"


def test_clean_record_passes_non_string_event_through():
    out = clean_record(None, "info", {"event": 42})
    assert out["event"] == 42


def test_setup_logging_text_format(monkeypatch):
    from src.config import settings

    monkeypatch.setattr(settings, "LOG_FORMAT", "text")
    monkeypatch.setattr(settings, "LOG_FILE_ENABLED", False)
    monkeypatch.setattr(settings, "LOG_LEVEL", "INFO")

    setup_logging()
    root = logging.getLogger()
    assert any(isinstance(h, logging.StreamHandler) for h in root.handlers)
    assert root.level == logging.INFO

    for uvi in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        log = logging.getLogger(uvi)
        assert log.handlers == []
        assert log.propagate is True


def test_setup_logging_json_format(monkeypatch):
    from src.config import settings

    monkeypatch.setattr(settings, "LOG_FORMAT", "json")
    monkeypatch.setattr(settings, "LOG_FILE_ENABLED", False)
    setup_logging()
    assert logging.getLogger().handlers


def test_setup_logging_with_file_handler(monkeypatch, tmp_path: Path):
    from src.config import settings

    monkeypatch.setattr(settings, "LOG_FORMAT", "text")
    monkeypatch.setattr(settings, "LOG_FILE_ENABLED", True)
    monkeypatch.setattr(settings, "LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr(settings, "LOG_FILE_BACKUP_DAYS", 7)

    setup_logging()
    root = logging.getLogger()
    file_handlers = [
        h
        for h in root.handlers
        if isinstance(h, logging.handlers.TimedRotatingFileHandler)
    ]
    assert len(file_handlers) == 1
    assert (tmp_path / "logs").exists()
    for h in file_handlers:
        h.close()


@pytest.fixture(autouse=True)
def _reset_logging_after_test():
    yield
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
        h.close()
