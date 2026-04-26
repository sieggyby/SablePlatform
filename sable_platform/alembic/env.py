"""Alembic environment for SablePlatform (Postgres migrations).

Reads ``SABLE_DATABASE_URL`` for the connection string.  Falls back to
``alembic.ini``'s ``sqlalchemy.url`` if the env var is not set.

SQLite databases use the legacy SQL migration files in
``sable_platform/db/migrations/``.  Alembic is only for Postgres.
"""
from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine

from sable_platform.db.schema import metadata

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = metadata


def _get_url() -> str:
    """Resolve the database URL from env or alembic.ini."""
    return os.environ.get("SABLE_DATABASE_URL", config.get_main_option("sqlalchemy.url", ""))


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode — emit SQL without connecting."""
    context.configure(
        url=_get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode — connect and execute."""
    connectable = create_engine(_get_url())

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
