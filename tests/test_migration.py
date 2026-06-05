from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, inspect, text


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

    engine = create_engine(sync_url)
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

    engine = create_engine(sync_url)
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
    }
    assert columns == expected

    index_names = {i["name"] for i in insp.get_indexes("clients")}
    assert "ix_clients_specialist_status" in index_names

    command.downgrade(cfg, "base")
    insp = inspect(engine)
    assert "clients" not in insp.get_table_names()
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
    engine = create_engine(sync_url)
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
    assert appt_columns == {
        "id",
        "specialist_id",
        "client_id",
        "starts_at",
        "comment",
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
