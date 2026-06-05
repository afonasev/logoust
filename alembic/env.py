from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from src.config import settings
import src.infrastructure.appointments_repo  # noqa: F401  # register ORM models
import src.infrastructure.clients_repo  # noqa: F401  # register ORM models
from src.infrastructure.db import Base
import src.infrastructure.specialists_repo  # noqa: F401  # register ORM models

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _sync_database_url(async_url: str) -> str:
    return async_url.replace("+aiosqlite", "")


config.set_main_option("sqlalchemy.url", _sync_database_url(settings.DATABASE_URL))

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    try:
        with connectable.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                render_as_batch=True,
            )

            with context.begin_transaction():
                context.run_migrations()
    finally:
        # Dispose so the DBAPI connection is closed promptly instead of waiting on
        # GC — otherwise back-to-back migration tests in one process surface a
        # `ResourceWarning` that `-W error` turns into a failure.
        connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
