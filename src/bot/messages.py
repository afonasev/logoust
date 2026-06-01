from dataclasses import dataclass
from pathlib import Path
import tomllib
from typing import Any


@dataclass(frozen=True, slots=True)
class StartMessages:
    welcome: str
    already_welcomed: str
    unknown_token: str
    no_token: str


@dataclass(frozen=True, slots=True)
class BotMessages:
    start: StartMessages


def _require(data: dict[str, Any], path: str) -> Any:
    keys = path.split(".")
    cursor: Any = data
    for key in keys:
        if not isinstance(cursor, dict) or key not in cursor:
            msg = f"messages.toml: missing key '{path}'"
            raise RuntimeError(msg)
        cursor = cursor[key]
    return cursor


def load_messages(path: Path) -> BotMessages:
    with path.open("rb") as fp:
        data = tomllib.load(fp)
    return BotMessages(
        start=StartMessages(
            welcome=_require(data, "start.welcome").strip(),
            already_welcomed=_require(data, "start.already_welcomed"),
            unknown_token=_require(data, "start.unknown_token"),
            no_token=_require(data, "start.no_token"),
        ),
    )


DEFAULT_MESSAGES_PATH = Path(__file__).parent / "messages.toml"
