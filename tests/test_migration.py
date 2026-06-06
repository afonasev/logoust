from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import NullPool, create_engine, inspect, text


@pytest.fixture
def alembic_config(tmp_path: Path) -> tuple[Config, str]:
    db_path = tmp_path / "migration.db"
    sync_url = f"sqlite:///{db_path}"
    project_root = Path(__file__).resolve().parent.parent
    cfg = Config(str(project_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(project_root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", sync_url)
    return cfg, sync_url


def test_initial_migration_creates_specialists_table(
    alembic_config: tuple[Config, str],
    monkeypatch,
):
    cfg, sync_url = alembic_config
    async_url = sync_url.replace("sqlite:", "sqlite+aiosqlite:", 1)

    from src.config import settings

    monkeypatch.setattr(settings, "DATABASE_URL", async_url)

    command.upgrade(cfg, "head")

    engine = create_engine(sync_url, poolclass=NullPool)
    insp = inspect(engine)
    columns = {c["name"]: c for c in insp.get_columns("specialists")}
    expected = {
        "id",
        "invite_token",
        "telegram_chat_id",
        "telegram_username",
        "welcomed_at",
        "created_at",
        "timezone",
        "day_start",
        "day_end",
        "slot_minutes",
        "working_days",
        # Added by 0007; this test runs to head.
        "reminder_enabled",
        "reminder_time",
        "reminder_last_run_on",
        # Added by 0011 (morning digest settings).
        "morning_notify_enabled",
        "morning_notify_time",
        "morning_notify_last_run_on",
        # Added by 0008, replaced by 0010 (subscription_default → subscription_presets).
        "subscription_presets",
    }
    assert set(columns) == expected

    index_names = {i["name"] for i in insp.get_indexes("specialists")}
    assert "ix_specialists_invite_token" in index_names
    assert "ix_specialists_telegram_chat_id" in index_names

    with engine.connect() as conn:
        rows = conn.execute(text("PRAGMA table_info(specialists)")).fetchall()
    not_null_columns = {row[1] for row in rows if row[3] == 1}
    assert "invite_token" in not_null_columns
    assert "created_at" in not_null_columns

    command.downgrade(cfg, "base")
    insp = inspect(engine)
    assert "specialists" not in insp.get_table_names()
    engine.dispose()


def test_clients_migration_creates_clients_table(
    alembic_config: tuple[Config, str],
    monkeypatch,
):
    cfg, sync_url = alembic_config
    async_url = sync_url.replace("sqlite:", "sqlite+aiosqlite:", 1)

    from src.config import settings

    monkeypatch.setattr(settings, "DATABASE_URL", async_url)

    command.upgrade(cfg, "head")

    engine = create_engine(sync_url, poolclass=NullPool)
    insp = inspect(engine)
    columns = {c["name"] for c in insp.get_columns("clients")}
    expected = {
        "id",
        "specialist_id",
        "child_name",
        "contact_name",
        "contact_phone",
        "contact_telegram",
        "extra_contacts",
        "note",
        "status",
        "archived_at",
        "created_at",
        "updated_at",
        "invite_token",
        "telegram_chat_id",
        "linked_at",
    }
    assert columns == expected

    index_names = {i["name"] for i in insp.get_indexes("clients")}
    assert "ix_clients_specialist_status" in index_names

    command.downgrade(cfg, "base")
    insp = inspect(engine)
    assert "clients" not in insp.get_table_names()
    engine.dispose()


def test_client_telegram_link_migration_adds_columns(
    alembic_config: tuple[Config, str],
    monkeypatch,
):
    cfg, sync_url = alembic_config
    async_url = sync_url.replace("sqlite:", "sqlite+aiosqlite:", 1)

    from src.config import settings

    monkeypatch.setattr(settings, "DATABASE_URL", async_url)

    # Stop before 0005 and seed a client that predates the telegram link columns.
    command.upgrade(cfg, "0004")
    engine = create_engine(sync_url, poolclass=NullPool)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO specialists (invite_token, created_at) "
                "VALUES ('tok', '2026-05-01')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO clients "
                "(specialist_id, child_name, contact_name, status, "
                "created_at, updated_at) "
                "VALUES (1, 'Маша', 'Мама', 'active', '2026-05-01', '2026-05-01')"
            )
        )

    command.upgrade(cfg, "head")

    insp = inspect(engine)
    client_columns = {c["name"] for c in insp.get_columns("clients")}
    assert {"invite_token", "telegram_chat_id", "linked_at"} <= client_columns
    index_names = {i["name"] for i in insp.get_indexes("clients")}
    assert "ix_clients_invite_token" in index_names

    # Existing client backfills to NULL — valid "not invited yet" state.
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT invite_token, telegram_chat_id, linked_at "
                "FROM clients WHERE child_name = 'Маша'"
            )
        ).one()
    assert row == (None, None, None)

    command.downgrade(cfg, "0004")
    insp = inspect(engine)
    client_columns = {c["name"] for c in insp.get_columns("clients")}
    assert "invite_token" not in client_columns
    engine.dispose()


def test_appointments_migration_creates_table_and_backfills_settings(
    alembic_config: tuple[Config, str],
    monkeypatch,
):
    cfg, sync_url = alembic_config
    async_url = sync_url.replace("sqlite:", "sqlite+aiosqlite:", 1)

    from src.config import settings

    monkeypatch.setattr(settings, "DATABASE_URL", async_url)

    # Stop before 0003 and seed a specialist that predates the schedule settings.
    command.upgrade(cfg, "0002")
    engine = create_engine(sync_url, poolclass=NullPool)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO specialists (invite_token, created_at) "
                "VALUES ('tok', '2026-05-01')"
            )
        )

    command.upgrade(cfg, "head")

    insp = inspect(engine)
    appt_columns = {c["name"] for c in insp.get_columns("appointments")}
    # series_id/origin_date are added later by 0006; this test runs to head.
    assert appt_columns == {
        "id",
        "specialist_id",
        "client_id",
        "starts_at",
        "comment",
        "series_id",
        "origin_date",
        "created_at",
        "updated_at",
    }
    index_names = {i["name"] for i in insp.get_indexes("appointments")}
    assert "ix_appointments_specialist_starts" in index_names
    assert "ix_appointments_client_starts" in index_names

    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT timezone, day_start, day_end, slot_minutes "
                "FROM specialists WHERE invite_token = 'tok'"
            )
        ).one()
    assert row == ("Asia/Yekaterinburg", "09:00", "20:00", 60)

    command.downgrade(cfg, "0002")
    insp = inspect(engine)
    assert "appointments" not in insp.get_table_names()
    specialist_columns = {c["name"] for c in insp.get_columns("specialists")}
    assert "timezone" not in specialist_columns
    engine.dispose()


def test_recurring_migration_adds_tables_and_appointment_columns(
    alembic_config: tuple[Config, str],
    monkeypatch,
):
    cfg, sync_url = alembic_config
    async_url = sync_url.replace("sqlite:", "sqlite+aiosqlite:", 1)

    from src.config import settings

    monkeypatch.setattr(settings, "DATABASE_URL", async_url)

    # Stop before 0006 and seed a one-off appointment that predates the series cols.
    command.upgrade(cfg, "0005")
    engine = create_engine(sync_url, poolclass=NullPool)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO specialists (invite_token, created_at) "
                "VALUES ('tok', '2026-05-01')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO clients "
                "(specialist_id, child_name, contact_name, status, "
                "created_at, updated_at) "
                "VALUES (1, 'Маша', 'Мама', 'active', '2026-05-01', '2026-05-01')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO appointments "
                "(specialist_id, client_id, starts_at, created_at, updated_at) "
                "VALUES (1, 1, '2026-05-02 09:00', '2026-05-01', '2026-05-01')"
            )
        )

    command.upgrade(cfg, "head")

    insp = inspect(engine)
    assert "recurring_appointments" in insp.get_table_names()
    assert "recurring_exceptions" in insp.get_table_names()
    appt_columns = {c["name"] for c in insp.get_columns("appointments")}
    assert {"series_id", "origin_date"} <= appt_columns
    index_names = {i["name"] for i in insp.get_indexes("appointments")}
    assert "uq_appointments_series_origin" in index_names
    recurring_indexes = {i["name"] for i in insp.get_indexes("recurring_appointments")}
    assert "ix_recurring_specialist_active" in recurring_indexes

    # The pre-existing one-off appointment backfills both columns to NULL.
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT series_id, origin_date FROM appointments")
        ).one()
    assert row == (None, None)

    command.downgrade(cfg, "0005")
    insp = inspect(engine)
    assert "recurring_appointments" not in insp.get_table_names()
    assert "recurring_exceptions" not in insp.get_table_names()
    appt_columns = {c["name"] for c in insp.get_columns("appointments")}
    assert "series_id" not in appt_columns
    engine.dispose()


def test_appointment_reminders_migration_adds_table_and_columns(
    alembic_config: tuple[Config, str],
    monkeypatch,
):
    cfg, sync_url = alembic_config
    async_url = sync_url.replace("sqlite:", "sqlite+aiosqlite:", 1)

    from src.config import settings

    monkeypatch.setattr(settings, "DATABASE_URL", async_url)

    # Stop before 0007 and seed a specialist that predates the reminder settings.
    command.upgrade(cfg, "0006")
    engine = create_engine(sync_url, poolclass=NullPool)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO specialists (invite_token, created_at) "
                "VALUES ('tok', '2026-05-01')"
            )
        )

    command.upgrade(cfg, "head")

    insp = inspect(engine)
    assert "appointment_reminders" in insp.get_table_names()
    reminder_columns = {c["name"] for c in insp.get_columns("appointment_reminders")}
    assert reminder_columns == {
        "id",
        "specialist_id",
        "client_id",
        "starts_at",
        "series_id",
        "origin_date",
        "status",
        "sent_at",
        "responded_at",
    }
    uniques = {
        u["name"]: u["column_names"]
        for u in insp.get_unique_constraints("appointment_reminders")
    }
    assert uniques.get("uq_reminder_occurrence") == [
        "specialist_id",
        "client_id",
        "starts_at",
    ]
    specialist_columns = {c["name"] for c in insp.get_columns("specialists")}
    assert {
        "reminder_enabled",
        "reminder_time",
        "reminder_last_run_on",
    } <= specialist_columns

    # The pre-existing specialist backfills to enabled at noon (opt-out default).
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT reminder_enabled, reminder_time, reminder_last_run_on "
                "FROM specialists WHERE invite_token = 'tok'"
            )
        ).one()
    assert row == (1, "12:00", None)

    command.downgrade(cfg, "0006")
    insp = inspect(engine)
    assert "appointment_reminders" not in insp.get_table_names()
    specialist_columns = {c["name"] for c in insp.get_columns("specialists")}
    assert "reminder_enabled" not in specialist_columns
    engine.dispose()


def test_subscriptions_migration_adds_table_and_backfills_default(
    alembic_config: tuple[Config, str],
    monkeypatch,
):
    cfg, sync_url = alembic_config
    async_url = sync_url.replace("sqlite:", "sqlite+aiosqlite:", 1)

    from src.config import settings

    monkeypatch.setattr(settings, "DATABASE_URL", async_url)

    # Stop before 0008 and seed a specialist that predates subscription_default.
    command.upgrade(cfg, "0007")
    engine = create_engine(sync_url, poolclass=NullPool)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO specialists (invite_token, created_at) "
                "VALUES ('tok', '2026-05-01')"
            )
        )

    # 0008 in isolation: 0010 later replaces subscription_default with presets.
    command.upgrade(cfg, "0008")

    insp = inspect(engine)
    assert "subscriptions" in insp.get_table_names()
    sub_columns = {c["name"] for c in insp.get_columns("subscriptions")}
    assert sub_columns == {
        "id",
        "client_id",
        "specialist_id",
        "purchased",
        "remaining",
        "status",
        "created_at",
        "closed_at",
    }
    index_names = {i["name"] for i in insp.get_indexes("subscriptions")}
    assert "ix_subscriptions_client_status" in index_names
    specialist_columns = {c["name"] for c in insp.get_columns("specialists")}
    assert "subscription_default" in specialist_columns

    # The pre-existing specialist backfills to the start default of 8.
    with engine.connect() as conn:
        value = conn.execute(
            text(
                "SELECT subscription_default FROM specialists WHERE invite_token = 'tok'"
            )
        ).scalar_one()
    assert value == 8

    command.downgrade(cfg, "0007")
    insp = inspect(engine)
    assert "subscriptions" not in insp.get_table_names()
    specialist_columns = {c["name"] for c in insp.get_columns("specialists")}
    assert "subscription_default" not in specialist_columns
    engine.dispose()


def test_subscription_presets_migration_replaces_default(
    alembic_config: tuple[Config, str],
    monkeypatch,
):
    cfg, sync_url = alembic_config
    async_url = sync_url.replace("sqlite:", "sqlite+aiosqlite:", 1)

    from src.config import settings

    monkeypatch.setattr(settings, "DATABASE_URL", async_url)

    # Seed a specialist at 0009 (still has subscription_default) before 0010 runs.
    command.upgrade(cfg, "0009")
    engine = create_engine(sync_url, poolclass=NullPool)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO specialists (invite_token, created_at) "
                "VALUES ('tok', '2026-05-01')"
            )
        )

    command.upgrade(cfg, "0010")
    insp = inspect(engine)
    specialist_columns = {c["name"] for c in insp.get_columns("specialists")}
    assert "subscription_presets" in specialist_columns
    assert "subscription_default" not in specialist_columns
    with engine.connect() as conn:
        value = conn.execute(
            text(
                "SELECT subscription_presets FROM specialists WHERE invite_token = 'tok'"
            )
        ).scalar_one()
    assert value == "4,8,12"

    command.downgrade(cfg, "0009")
    insp = inspect(engine)
    specialist_columns = {c["name"] for c in insp.get_columns("specialists")}
    assert "subscription_presets" not in specialist_columns
    assert "subscription_default" in specialist_columns
    engine.dispose()


def test_morning_digest_migration_adds_columns_and_backfills(
    alembic_config: tuple[Config, str],
    monkeypatch,
):
    cfg, sync_url = alembic_config
    async_url = sync_url.replace("sqlite:", "sqlite+aiosqlite:", 1)

    from src.config import settings

    monkeypatch.setattr(settings, "DATABASE_URL", async_url)

    # Stop before 0011 and seed a specialist that predates the digest settings.
    command.upgrade(cfg, "0010")
    engine = create_engine(sync_url, poolclass=NullPool)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO specialists (invite_token, created_at) "
                "VALUES ('tok', '2026-05-01')"
            )
        )

    command.upgrade(cfg, "head")

    insp = inspect(engine)
    specialist_columns = {c["name"] for c in insp.get_columns("specialists")}
    assert {
        "morning_notify_enabled",
        "morning_notify_time",
        "morning_notify_last_run_on",
    } <= specialist_columns

    # The pre-existing specialist backfills to enabled at 10:00 (opt-out default).
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT morning_notify_enabled, morning_notify_time, "
                "morning_notify_last_run_on "
                "FROM specialists WHERE invite_token = 'tok'"
            )
        ).one()
    assert row == (1, "10:00", None)

    command.downgrade(cfg, "0010")
    insp = inspect(engine)
    specialist_columns = {c["name"] for c in insp.get_columns("specialists")}
    assert "morning_notify_enabled" not in specialist_columns
    engine.dispose()


def test_audit_log_migration_creates_table_and_index(
    alembic_config: tuple[Config, str],
    monkeypatch,
):
    cfg, sync_url = alembic_config
    async_url = sync_url.replace("sqlite:", "sqlite+aiosqlite:", 1)

    from src.config import settings

    monkeypatch.setattr(settings, "DATABASE_URL", async_url)

    command.upgrade(cfg, "head")

    engine = create_engine(sync_url, poolclass=NullPool)
    insp = inspect(engine)
    assert "audit_log" in insp.get_table_names()
    columns = {c["name"] for c in insp.get_columns("audit_log")}
    assert columns == {
        "id",
        "specialist_id",
        "created_at",
        "kind",
        "event",
        "client_id",
        "text",
        "status",
        "error",
    }
    index_names = {i["name"] for i in insp.get_indexes("audit_log")}
    assert "ix_audit_specialist_created" in index_names

    command.downgrade(cfg, "0011")
    insp = inspect(engine)
    assert "audit_log" not in insp.get_table_names()
    engine.dispose()


def test_working_days_migration_adds_column_and_backfills(
    alembic_config: tuple[Config, str],
    monkeypatch,
):
    cfg, sync_url = alembic_config
    async_url = sync_url.replace("sqlite:", "sqlite+aiosqlite:", 1)

    from src.config import settings

    monkeypatch.setattr(settings, "DATABASE_URL", async_url)

    # Stop before 0004 and seed a specialist that predates working_days.
    command.upgrade(cfg, "0003")
    engine = create_engine(sync_url, poolclass=NullPool)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO specialists (invite_token, created_at) "
                "VALUES ('tok', '2026-05-01')"
            )
        )

    command.upgrade(cfg, "head")

    insp = inspect(engine)
    specialist_columns = {c["name"] for c in insp.get_columns("specialists")}
    assert "working_days" in specialist_columns
    with engine.connect() as conn:
        value = conn.execute(
            text("SELECT working_days FROM specialists WHERE invite_token = 'tok'")
        ).scalar_one()
    assert value == "0,1,2,3,4"

    command.downgrade(cfg, "0003")
    insp = inspect(engine)
    specialist_columns = {c["name"] for c in insp.get_columns("specialists")}
    assert "working_days" not in specialist_columns
    engine.dispose()
