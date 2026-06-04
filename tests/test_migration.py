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
