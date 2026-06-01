import logging
import logging.handlers
from pathlib import Path
import re
import sys
from typing import Any

import structlog

from src.config import settings

_IP_PREFIX_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}(?::\d+)? - ")

_UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")


def clean_record(
    _logger: Any, _method: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    event_dict.pop("color_message", None)
    if isinstance(event := event_dict.get("event"), str):
        event_dict["event"] = _IP_PREFIX_RE.sub("", event)
    return event_dict


def _shared_processors(*, json: bool = False) -> list[structlog.types.Processor]:
    procs: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.ExtraAdder(),
        structlog.processors.TimeStamper(fmt="iso" if json else "%H:%M:%S", utc=json),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    if json:
        procs.insert(1, structlog.stdlib.add_logger_name)
    return procs


def _dev_renderer() -> structlog.dev.ConsoleRenderer:
    base = structlog.dev.ConsoleRenderer(colors=True, pad_event_to=0)
    col_map = {c.key: c for c in base.columns}
    lf = col_map["level"].formatter
    col_map["level"] = structlog.dev.Column(
        key="level",
        formatter=structlog.dev.LogLevelColumnFormatter(
            level_styles=lf.level_styles,
            reset_style=lf.reset_style,
            width=0,
        ),
    )
    ordered = [
        c
        for k in ["timestamp", "level", "event", ""]
        if (c := col_map.get(k)) is not None
    ]
    return structlog.dev.ConsoleRenderer(columns=ordered)


def _renderer(format_: str) -> structlog.types.Processor:
    if format_ == "json":
        return structlog.processors.JSONRenderer()
    return _dev_renderer()


def _build_formatter(format_: str) -> logging.Formatter:
    is_json = format_ == "json"
    return structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=_shared_processors(json=is_json),
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            clean_record,
            _renderer(format_),
        ],
    )


def _build_handlers(formatter: logging.Formatter) -> list[logging.Handler]:
    handlers: list[logging.Handler] = []
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    handlers.append(stream_handler)
    if settings.LOG_FILE_ENABLED:
        log_dir = Path(settings.LOG_DIR)
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.TimedRotatingFileHandler(
            log_dir / "app.log",
            when="midnight",
            backupCount=settings.LOG_FILE_BACKUP_DAYS,
            encoding="utf-8",
            utc=True,
        )
        file_handler.setFormatter(formatter)
        handlers.append(file_handler)
    return handlers


def setup_logging() -> None:
    is_json = settings.LOG_FORMAT == "json"
    structlog.configure(
        processors=[
            *_shared_processors(json=is_json),
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=False,
    )

    formatter = _build_formatter(settings.LOG_FORMAT)
    handlers = _build_handlers(formatter)

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    for handler in handlers:
        root.addHandler(handler)
    root.setLevel(settings.LOG_LEVEL)

    for name in _UVICORN_LOGGERS:
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True
