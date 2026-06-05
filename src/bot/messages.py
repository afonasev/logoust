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
    archive_confirm: str
    archived: str
    restored: str
    updated: str
    cancelled: str
    empty_required: str
    need_contact_channel: str
    edit_prompt: str
    not_found: str


@dataclass(frozen=True, slots=True)
class ScheduleMessages:
    button: str
    dash: str
    day_title: str
    day_empty: str
    week_title: str
    week_empty: str
    history_title: str
    history_empty: str
    client_future_empty: str
    client_history_title: str
    client_history_empty: str
    day_header: str
    line: str
    line_full: str
    comment_suffix: str
    pick_date: str
    pick_time: str
    ask_custom_time: str
    bad_time: str
    ask_comment: str
    past_date: str
    created: str
    rescheduled: str
    deleted: str
    not_found: str
    confirm_delete: str
    card: str
    btn_add: str
    btn_client_history: str
    btn_reschedule: str
    btn_delete: str
    btn_confirm_delete: str
    btn_other_time: str
    btn_history: str
    btn_today: str
    btn_week: str


@dataclass(frozen=True, slots=True)
class SettingsMessages:
    button: str
    title: str
    btn_timezone: str
    btn_day_start: str
    btn_day_end: str
    btn_slot: str
    btn_working_days: str
    btn_back: str
    pick_timezone: str
    pick_working_days: str
    ask_day_start: str
    ask_day_end: str
    ask_slot: str
    no_working_days: str
    bad_time: str
    bad_slot: str
    saved: str
    not_found: str


@dataclass(frozen=True, slots=True)
class WindowsMessages:
    button: str
    title: str
    day_header: str
    empty_day: str
    no_working_days: str


@dataclass(frozen=True, slots=True)
class BotMessages:
    start: StartMessages
    clients: ClientsMessages
    schedule: ScheduleMessages
    settings: SettingsMessages
    windows: WindowsMessages


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
            archive_confirm=_require(data, "clients.archive_confirm"),
            archived=_require(data, "clients.archived"),
            restored=_require(data, "clients.restored"),
            updated=_require(data, "clients.updated"),
            cancelled=_require(data, "clients.cancelled"),
            empty_required=_require(data, "clients.empty_required"),
            need_contact_channel=_require(data, "clients.need_contact_channel"),
            edit_prompt=_require(data, "clients.edit_prompt"),
            not_found=_require(data, "clients.not_found"),
        ),
        schedule=ScheduleMessages(
            button=_require(data, "schedule.button"),
            dash=_require(data, "schedule.dash"),
            day_title=_require(data, "schedule.day_title"),
            day_empty=_require(data, "schedule.day_empty"),
            week_title=_require(data, "schedule.week_title"),
            week_empty=_require(data, "schedule.week_empty"),
            history_title=_require(data, "schedule.history_title"),
            history_empty=_require(data, "schedule.history_empty"),
            client_future_empty=_require(data, "schedule.client_future_empty"),
            client_history_title=_require(data, "schedule.client_history_title"),
            client_history_empty=_require(data, "schedule.client_history_empty"),
            day_header=_require(data, "schedule.day_header"),
            line=_require(data, "schedule.line"),
            line_full=_require(data, "schedule.line_full"),
            comment_suffix=_require(data, "schedule.comment_suffix"),
            pick_date=_require(data, "schedule.pick_date"),
            pick_time=_require(data, "schedule.pick_time"),
            ask_custom_time=_require(data, "schedule.ask_custom_time"),
            bad_time=_require(data, "schedule.bad_time"),
            ask_comment=_require(data, "schedule.ask_comment"),
            past_date=_require(data, "schedule.past_date"),
            created=_require(data, "schedule.created"),
            rescheduled=_require(data, "schedule.rescheduled"),
            deleted=_require(data, "schedule.deleted"),
            not_found=_require(data, "schedule.not_found"),
            confirm_delete=_require(data, "schedule.confirm_delete"),
            card=_require(data, "schedule.card").strip(),
            btn_add=_require(data, "schedule.btn_add"),
            btn_client_history=_require(data, "schedule.btn_client_history"),
            btn_reschedule=_require(data, "schedule.btn_reschedule"),
            btn_delete=_require(data, "schedule.btn_delete"),
            btn_confirm_delete=_require(data, "schedule.btn_confirm_delete"),
            btn_other_time=_require(data, "schedule.btn_other_time"),
            btn_history=_require(data, "schedule.btn_history"),
            btn_today=_require(data, "schedule.btn_today"),
            btn_week=_require(data, "schedule.btn_week"),
        ),
        settings=SettingsMessages(
            button=_require(data, "settings.button"),
            title=_require(data, "settings.title").strip(),
            btn_timezone=_require(data, "settings.btn_timezone"),
            btn_day_start=_require(data, "settings.btn_day_start"),
            btn_day_end=_require(data, "settings.btn_day_end"),
            btn_slot=_require(data, "settings.btn_slot"),
            btn_working_days=_require(data, "settings.btn_working_days"),
            btn_back=_require(data, "settings.btn_back"),
            pick_timezone=_require(data, "settings.pick_timezone"),
            pick_working_days=_require(data, "settings.pick_working_days"),
            ask_day_start=_require(data, "settings.ask_day_start"),
            ask_day_end=_require(data, "settings.ask_day_end"),
            ask_slot=_require(data, "settings.ask_slot"),
            no_working_days=_require(data, "settings.no_working_days"),
            bad_time=_require(data, "settings.bad_time"),
            bad_slot=_require(data, "settings.bad_slot"),
            saved=_require(data, "settings.saved"),
            not_found=_require(data, "settings.not_found"),
        ),
        windows=WindowsMessages(
            button=_require(data, "windows.button"),
            title=_require(data, "windows.title"),
            day_header=_require(data, "windows.day_header"),
            empty_day=_require(data, "windows.empty_day"),
            no_working_days=_require(data, "windows.no_working_days"),
        ),
    )


DEFAULT_MESSAGES_PATH = Path(__file__).parent / "messages.toml"
