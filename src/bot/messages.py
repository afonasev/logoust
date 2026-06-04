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
class ClientsMessages:
    button: str
    menu_title: str
    list_active_title: str
    list_archived_title: str
    archive_title: str
    empty_active: str
    empty_archived: str
    status_active: str
    status_archived: str
    dash: str
    card: str
    ask_child_name: str
    ask_contact_name: str
    ask_phone: str
    ask_telegram: str
    added: str
    archived: str
    restored: str
    updated: str
    cancelled: str
    empty_required: str
    need_contact_channel: str
    edit_prompt: str
    not_found: str


@dataclass(frozen=True, slots=True)
class BotMessages:
    start: StartMessages
    clients: ClientsMessages


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
        clients=ClientsMessages(
            button=_require(data, "clients.button"),
            menu_title=_require(data, "clients.menu_title"),
            list_active_title=_require(data, "clients.list_active_title"),
            list_archived_title=_require(data, "clients.list_archived_title"),
            archive_title=_require(data, "clients.archive_title"),
            empty_active=_require(data, "clients.empty_active"),
            empty_archived=_require(data, "clients.empty_archived"),
            status_active=_require(data, "clients.status_active"),
            status_archived=_require(data, "clients.status_archived"),
            dash=_require(data, "clients.dash"),
            card=_require(data, "clients.card").strip(),
            ask_child_name=_require(data, "clients.ask_child_name"),
            ask_contact_name=_require(data, "clients.ask_contact_name"),
            ask_phone=_require(data, "clients.ask_phone"),
            ask_telegram=_require(data, "clients.ask_telegram"),
            added=_require(data, "clients.added"),
            archived=_require(data, "clients.archived"),
            restored=_require(data, "clients.restored"),
            updated=_require(data, "clients.updated"),
            cancelled=_require(data, "clients.cancelled"),
            empty_required=_require(data, "clients.empty_required"),
            need_contact_channel=_require(data, "clients.need_contact_channel"),
            edit_prompt=_require(data, "clients.edit_prompt"),
            not_found=_require(data, "clients.not_found"),
        ),
    )


DEFAULT_MESSAGES_PATH = Path(__file__).parent / "messages.toml"
