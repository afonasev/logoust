from dataclasses import dataclass
from pathlib import Path
import tomllib
from typing import Any

from src.domain.reminder import ReminderStatus
from src.services.digest import DigestMessages


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
    btn_subscription_create: str
    btn_subscription_open: str
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
    tg_linked_badge: str
    invite_button: str
    invite_button_linked: str
    invite_forward: str
    linked: str
    link_unknown: str
    dnotify_title: str
    dnotify_line: str
    dnotify_cancel: str


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
    ask_regular: str
    btn_regular_yes: str
    btn_regular_no: str
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
    notify_ask: str
    notify_yes: str
    notify_no: str
    notify_created: str
    notify_rescheduled: str
    notify_cancelled: str
    notify_series_created: str
    notify_series_changed: str
    notify_series_cancelled: str
    notify_sent: str
    notify_failed: str
    notify_not_linked: str
    notify_skipped: str
    notify_when_ask: str
    notify_when_now: str
    notify_when_preset: str
    notify_when_custom: str
    notify_custom_time_ask: str
    notify_custom_cancel: str
    notify_deferred_queued: str
    notify_deferred_superseded: str
    notify_session_stale: str
    notify_deferred_failed: str


@dataclass(frozen=True, slots=True)
class RecurringMessages:
    button: str
    mark: str
    pick_weekday: str
    pick_move_date: str
    pick_time: str
    ask_custom_time: str
    bad_time: str
    ask_comment: str
    created: str
    not_found: str
    line: str
    # multi-slot creation wizard
    add_more: str
    btn_add_more: str
    btn_done: str
    # schedule card (screen 1)
    schedule_card: str
    rule_line: str
    empty_window: str
    occ_btn: str
    btn_configure: str
    btn_stop: str
    # single-meeting card (screen 2)
    meeting_card: str
    btn_move: str
    btn_skip: str
    btn_comment: str
    btn_to_schedule: str
    # per-slot configuration screen
    configure_title: str
    slot_btn: str
    slot_actions_title: str
    btn_slot_time: str
    btn_slot_day: str
    btn_slot_delete: str
    btn_add_day: str
    slot_removed: str
    edited: str
    # stop the whole schedule
    confirm_stop: str
    btn_confirm_stop: str
    stopped: str
    cancelled: str
    # single-occurrence skip / move / comment
    skip_confirm: str
    btn_confirm_skip: str
    skipped: str
    moved: str
    ask_occ_comment: str
    comment_set: str


@dataclass(frozen=True, slots=True)
class ReminderMessages:
    client_text: str
    btn_confirm: str
    btn_decline: str
    confirmed_toast: str
    declined_toast: str
    specialist_declined: str
    btn_open_appt: str
    confirmed_mark: str
    declined_mark: str
    card_confirmed: str

    def status_mark(self, status: ReminderStatus | None) -> str:
        """Leading label prefix for an occurrence's reminder status.

        Returns the mark plus a trailing space so it concatenates directly before
        a label; an empty string when there is no confirmed/declined response.
        """
        if status is ReminderStatus.CONFIRMED:
            return f"{self.confirmed_mark} "
        if status is ReminderStatus.DECLINED:
            return f"{self.declined_mark} "
        return ""


@dataclass(frozen=True, slots=True)
class SettingsMessages:
    button: str
    title: str
    btn_timezone: str
    btn_day_start: str
    btn_day_end: str
    btn_slot: str
    btn_working_days: str
    btn_reminder: str
    btn_reminder_on: str
    btn_reminder_off: str
    btn_reminder_time: str
    btn_reminder_now: str
    btn_digest: str
    btn_digest_on: str
    btn_digest_off: str
    btn_digest_time: str
    btn_digest_now: str
    btn_payment: str
    btn_payment_on: str
    btn_payment_off: str
    btn_payment_time: str
    btn_subscription_presets: str
    btn_deferred_time: str
    state_on: str
    state_off: str
    btn_back: str
    btn_cancel: str
    value_now: str
    pick_timezone: str
    pick_working_days: str
    ask_day_start: str
    ask_day_end: str
    ask_slot: str
    ask_reminder_time: str
    ask_digest_time: str
    ask_payment_time: str
    ask_subscription_presets: str
    ask_deferred_time: str
    no_working_days: str
    bad_time: str
    bad_slot: str
    bad_subscription_presets: str
    digest_now_empty: str
    digest_now_failed: str
    reminders_now_empty: str
    reminders_now_done: str
    saved: str
    not_found: str


@dataclass(frozen=True, slots=True)
class PaymentMessages:
    alert: str
    btn_send: str
    sent: str
    not_delivered: str
    not_linked: str


@dataclass(frozen=True, slots=True)
class SubscriptionsMessages:
    button: str
    list_active_title: str
    list_active_empty: str
    list_closed_title: str
    list_closed_empty: str
    list_row_active: str
    list_row_closed: str
    btn_closed: str
    btn_active: str
    closed_note: str
    card: str
    create_prompt: str
    extend_prompt: str
    bad_meetings: str
    created: str
    extended: str
    decremented: str
    nothing_to_decrement: str
    close_confirm: str
    closed: str
    not_found: str
    cancelled: str
    btn_decrement: str
    btn_extend: str
    btn_close: str
    btn_confirm_close: str
    btn_cancel: str
    btn_back_client: str


@dataclass(frozen=True, slots=True)
class WindowsMessages:
    button: str
    title: str
    day_header: str
    empty_day: str
    no_working_days: str


@dataclass(frozen=True, slots=True)
class TemplatesMessages:
    btn_open: str
    title: str
    btn_edit: str
    btn_reset: str
    required_mark: str
    no_placeholders: str
    edit_prompt: str
    saved: str
    reset_done: str
    reset_noop: str
    err_empty: str
    err_malformed: str
    err_disallowed: str
    err_missing: str
    # Keyed by template_key — labels shown in the list, defaults with no home section.
    labels: dict[str, str]
    defaults: dict[str, str]


@dataclass(frozen=True, slots=True)
class AuditMessages:
    button: str
    title: str
    empty: str
    line_message: str
    line_action: str
    client_suffix: str
    status_sent: str
    status_failed: str
    action_icon: str
    btn_prev: str
    btn_next: str
    # Keyed by AuditEvent slug — display label for each journalled event.
    events: dict[str, str]


@dataclass(frozen=True, slots=True)
class BotMessages:
    start: StartMessages
    clients: ClientsMessages
    schedule: ScheduleMessages
    recurring: RecurringMessages
    reminder: ReminderMessages
    settings: SettingsMessages
    payment: PaymentMessages
    digest: DigestMessages
    subscriptions: SubscriptionsMessages
    windows: WindowsMessages
    templates: TemplatesMessages
    audit: AuditMessages


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
            btn_subscription_create=_require(data, "clients.btn_subscription_create"),
            btn_subscription_open=_require(data, "clients.btn_subscription_open"),
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
            tg_linked_badge=_require(data, "clients.tg_linked_badge"),
            invite_button=_require(data, "clients.invite_button"),
            invite_button_linked=_require(data, "clients.invite_button_linked"),
            invite_forward=_require(data, "clients.invite_forward").strip(),
            linked=_require(data, "clients.linked"),
            link_unknown=_require(data, "clients.link_unknown"),
            dnotify_title=_require(data, "clients.dnotify_title"),
            dnotify_line=_require(data, "clients.dnotify_line"),
            dnotify_cancel=_require(data, "clients.dnotify_cancel"),
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
            ask_regular=_require(data, "schedule.ask_regular"),
            btn_regular_yes=_require(data, "schedule.btn_regular_yes"),
            btn_regular_no=_require(data, "schedule.btn_regular_no"),
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
            notify_ask=_require(data, "schedule.notify_ask"),
            notify_yes=_require(data, "schedule.notify_yes"),
            notify_no=_require(data, "schedule.notify_no"),
            notify_created=_require(data, "schedule.notify_created"),
            notify_rescheduled=_require(data, "schedule.notify_rescheduled"),
            notify_cancelled=_require(data, "schedule.notify_cancelled"),
            notify_series_created=_require(data, "schedule.notify_series_created"),
            notify_series_changed=_require(data, "schedule.notify_series_changed"),
            notify_series_cancelled=_require(data, "schedule.notify_series_cancelled"),
            notify_sent=_require(data, "schedule.notify_sent"),
            notify_failed=_require(data, "schedule.notify_failed"),
            notify_not_linked=_require(data, "schedule.notify_not_linked"),
            notify_skipped=_require(data, "schedule.notify_skipped"),
            notify_when_ask=_require(data, "schedule.notify_when_ask"),
            notify_when_now=_require(data, "schedule.notify_when_now"),
            notify_when_preset=_require(data, "schedule.notify_when_preset"),
            notify_when_custom=_require(data, "schedule.notify_when_custom"),
            notify_custom_time_ask=_require(data, "schedule.notify_custom_time_ask"),
            notify_custom_cancel=_require(data, "schedule.notify_custom_cancel"),
            notify_deferred_queued=_require(data, "schedule.notify_deferred_queued"),
            notify_deferred_superseded=_require(
                data, "schedule.notify_deferred_superseded"
            ),
            notify_session_stale=_require(data, "schedule.notify_session_stale"),
            notify_deferred_failed=_require(data, "schedule.notify_deferred_failed"),
        ),
        recurring=RecurringMessages(
            button=_require(data, "recurring.button"),
            mark=_require(data, "recurring.mark"),
            pick_weekday=_require(data, "recurring.pick_weekday"),
            pick_move_date=_require(data, "recurring.pick_move_date"),
            pick_time=_require(data, "recurring.pick_time"),
            ask_custom_time=_require(data, "recurring.ask_custom_time"),
            bad_time=_require(data, "recurring.bad_time"),
            ask_comment=_require(data, "recurring.ask_comment"),
            created=_require(data, "recurring.created"),
            not_found=_require(data, "recurring.not_found"),
            line=_require(data, "recurring.line"),
            add_more=_require(data, "recurring.add_more"),
            btn_add_more=_require(data, "recurring.btn_add_more"),
            btn_done=_require(data, "recurring.btn_done"),
            schedule_card=_require(data, "recurring.schedule_card").strip(),
            rule_line=_require(data, "recurring.rule_line"),
            empty_window=_require(data, "recurring.empty_window"),
            occ_btn=_require(data, "recurring.occ_btn"),
            btn_configure=_require(data, "recurring.btn_configure"),
            btn_stop=_require(data, "recurring.btn_stop"),
            meeting_card=_require(data, "recurring.meeting_card").strip(),
            btn_move=_require(data, "recurring.btn_move"),
            btn_skip=_require(data, "recurring.btn_skip"),
            btn_comment=_require(data, "recurring.btn_comment"),
            btn_to_schedule=_require(data, "recurring.btn_to_schedule"),
            configure_title=_require(data, "recurring.configure_title"),
            slot_btn=_require(data, "recurring.slot_btn"),
            slot_actions_title=_require(data, "recurring.slot_actions_title"),
            btn_slot_time=_require(data, "recurring.btn_slot_time"),
            btn_slot_day=_require(data, "recurring.btn_slot_day"),
            btn_slot_delete=_require(data, "recurring.btn_slot_delete"),
            btn_add_day=_require(data, "recurring.btn_add_day"),
            slot_removed=_require(data, "recurring.slot_removed"),
            edited=_require(data, "recurring.edited"),
            confirm_stop=_require(data, "recurring.confirm_stop"),
            btn_confirm_stop=_require(data, "recurring.btn_confirm_stop"),
            stopped=_require(data, "recurring.stopped"),
            cancelled=_require(data, "recurring.cancelled"),
            skip_confirm=_require(data, "recurring.skip_confirm"),
            btn_confirm_skip=_require(data, "recurring.btn_confirm_skip"),
            skipped=_require(data, "recurring.skipped"),
            moved=_require(data, "recurring.moved"),
            ask_occ_comment=_require(data, "recurring.ask_occ_comment"),
            comment_set=_require(data, "recurring.comment_set"),
        ),
        reminder=ReminderMessages(
            client_text=_require(data, "reminder.client_text").strip(),
            btn_confirm=_require(data, "reminder.btn_confirm"),
            btn_decline=_require(data, "reminder.btn_decline"),
            confirmed_toast=_require(data, "reminder.confirmed_toast"),
            declined_toast=_require(data, "reminder.declined_toast"),
            specialist_declined=_require(data, "reminder.specialist_declined"),
            btn_open_appt=_require(data, "reminder.btn_open_appt"),
            confirmed_mark=_require(data, "reminder.confirmed_mark"),
            declined_mark=_require(data, "reminder.declined_mark"),
            card_confirmed=_require(data, "reminder.card_confirmed"),
        ),
        settings=SettingsMessages(
            button=_require(data, "settings.button"),
            title=_require(data, "settings.title").strip(),
            btn_timezone=_require(data, "settings.btn_timezone"),
            btn_day_start=_require(data, "settings.btn_day_start"),
            btn_day_end=_require(data, "settings.btn_day_end"),
            btn_slot=_require(data, "settings.btn_slot"),
            btn_working_days=_require(data, "settings.btn_working_days"),
            btn_reminder=_require(data, "settings.btn_reminder"),
            btn_reminder_on=_require(data, "settings.btn_reminder_on"),
            btn_reminder_off=_require(data, "settings.btn_reminder_off"),
            btn_reminder_time=_require(data, "settings.btn_reminder_time"),
            btn_reminder_now=_require(data, "settings.btn_reminder_now"),
            btn_digest=_require(data, "settings.btn_digest"),
            btn_digest_on=_require(data, "settings.btn_digest_on"),
            btn_digest_off=_require(data, "settings.btn_digest_off"),
            btn_digest_time=_require(data, "settings.btn_digest_time"),
            btn_digest_now=_require(data, "settings.btn_digest_now"),
            btn_payment=_require(data, "settings.btn_payment"),
            btn_payment_on=_require(data, "settings.btn_payment_on"),
            btn_payment_off=_require(data, "settings.btn_payment_off"),
            btn_payment_time=_require(data, "settings.btn_payment_time"),
            btn_subscription_presets=_require(
                data, "settings.btn_subscription_presets"
            ),
            btn_deferred_time=_require(data, "settings.btn_deferred_time"),
            state_on=_require(data, "settings.state_on"),
            state_off=_require(data, "settings.state_off"),
            btn_back=_require(data, "settings.btn_back"),
            btn_cancel=_require(data, "settings.btn_cancel"),
            value_now=_require(data, "settings.value_now"),
            pick_timezone=_require(data, "settings.pick_timezone"),
            pick_working_days=_require(data, "settings.pick_working_days"),
            ask_day_start=_require(data, "settings.ask_day_start"),
            ask_day_end=_require(data, "settings.ask_day_end"),
            ask_slot=_require(data, "settings.ask_slot"),
            ask_reminder_time=_require(data, "settings.ask_reminder_time"),
            ask_digest_time=_require(data, "settings.ask_digest_time"),
            ask_payment_time=_require(data, "settings.ask_payment_time"),
            ask_subscription_presets=_require(
                data, "settings.ask_subscription_presets"
            ),
            ask_deferred_time=_require(data, "settings.ask_deferred_time"),
            no_working_days=_require(data, "settings.no_working_days"),
            bad_time=_require(data, "settings.bad_time"),
            bad_slot=_require(data, "settings.bad_slot"),
            bad_subscription_presets=_require(
                data, "settings.bad_subscription_presets"
            ),
            digest_now_empty=_require(data, "settings.digest_now_empty"),
            digest_now_failed=_require(data, "settings.digest_now_failed"),
            reminders_now_empty=_require(data, "settings.reminders_now_empty"),
            reminders_now_done=_require(data, "settings.reminders_now_done"),
            saved=_require(data, "settings.saved"),
            not_found=_require(data, "settings.not_found"),
        ),
        payment=PaymentMessages(
            alert=_require(data, "payment.alert").strip(),
            btn_send=_require(data, "payment.btn_send"),
            sent=_require(data, "payment.sent"),
            not_delivered=_require(data, "payment.not_delivered"),
            not_linked=_require(data, "payment.not_linked"),
        ),
        digest=DigestMessages(
            title=_require(data, "digest.title"),
            line=_require(data, "digest.line"),
            comment_suffix=_require(data, "digest.comment_suffix"),
            dash=_require(data, "digest.dash"),
        ),
        subscriptions=SubscriptionsMessages(
            button=_require(data, "subscriptions.button"),
            list_active_title=_require(data, "subscriptions.list_active_title"),
            list_active_empty=_require(data, "subscriptions.list_active_empty"),
            list_closed_title=_require(data, "subscriptions.list_closed_title"),
            list_closed_empty=_require(data, "subscriptions.list_closed_empty"),
            list_row_active=_require(data, "subscriptions.list_row_active"),
            list_row_closed=_require(data, "subscriptions.list_row_closed"),
            btn_closed=_require(data, "subscriptions.btn_closed"),
            btn_active=_require(data, "subscriptions.btn_active"),
            closed_note=_require(data, "subscriptions.closed_note"),
            card=_require(data, "subscriptions.card").strip(),
            create_prompt=_require(data, "subscriptions.create_prompt"),
            extend_prompt=_require(data, "subscriptions.extend_prompt"),
            bad_meetings=_require(data, "subscriptions.bad_meetings"),
            created=_require(data, "subscriptions.created"),
            extended=_require(data, "subscriptions.extended"),
            decremented=_require(data, "subscriptions.decremented"),
            nothing_to_decrement=_require(data, "subscriptions.nothing_to_decrement"),
            close_confirm=_require(data, "subscriptions.close_confirm"),
            closed=_require(data, "subscriptions.closed"),
            not_found=_require(data, "subscriptions.not_found"),
            cancelled=_require(data, "subscriptions.cancelled"),
            btn_decrement=_require(data, "subscriptions.btn_decrement"),
            btn_extend=_require(data, "subscriptions.btn_extend"),
            btn_close=_require(data, "subscriptions.btn_close"),
            btn_confirm_close=_require(data, "subscriptions.btn_confirm_close"),
            btn_cancel=_require(data, "subscriptions.btn_cancel"),
            btn_back_client=_require(data, "subscriptions.btn_back_client"),
        ),
        windows=WindowsMessages(
            button=_require(data, "windows.button"),
            title=_require(data, "windows.title"),
            day_header=_require(data, "windows.day_header"),
            empty_day=_require(data, "windows.empty_day"),
            no_working_days=_require(data, "windows.no_working_days"),
        ),
        templates=TemplatesMessages(
            btn_open=_require(data, "templates.btn_open"),
            title=_require(data, "templates.title").strip(),
            btn_edit=_require(data, "templates.btn_edit"),
            btn_reset=_require(data, "templates.btn_reset"),
            required_mark=_require(data, "templates.required_mark"),
            no_placeholders=_require(data, "templates.no_placeholders"),
            edit_prompt=_require(data, "templates.edit_prompt").strip(),
            saved=_require(data, "templates.saved"),
            reset_done=_require(data, "templates.reset_done"),
            reset_noop=_require(data, "templates.reset_noop"),
            err_empty=_require(data, "templates.err_empty"),
            err_malformed=_require(data, "templates.err_malformed"),
            err_disallowed=_require(data, "templates.err_disallowed"),
            err_missing=_require(data, "templates.err_missing"),
            labels=_require(data, "templates.labels"),
            defaults=_require(data, "templates.defaults"),
        ),
        audit=AuditMessages(
            button=_require(data, "audit.button"),
            title=_require(data, "audit.title"),
            empty=_require(data, "audit.empty"),
            line_message=_require(data, "audit.line_message"),
            line_action=_require(data, "audit.line_action"),
            client_suffix=_require(data, "audit.client_suffix"),
            status_sent=_require(data, "audit.status_sent"),
            status_failed=_require(data, "audit.status_failed"),
            action_icon=_require(data, "audit.action_icon"),
            btn_prev=_require(data, "audit.btn_prev"),
            btn_next=_require(data, "audit.btn_next"),
            events=_require(data, "audit.events"),
        ),
    )


DEFAULT_MESSAGES_PATH = Path(__file__).parent / "messages.toml"
