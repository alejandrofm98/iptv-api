"""Alembic env.py for iptv-api.

This file points to iptv-db as the single source of truth for database schema.
Legacy migrations in alembic/versions/ are kept as historical reference only.

See MIGRATION_GUIDE.md in iptv-db for the unified Alembic workflow.
"""

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# Import from iptv-db — the single source of truth
from iptv_db.engine import build_url
from iptv_db.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Build URL from env vars
DB_USER = os.getenv("PG_USER", "")
DB_PASSWORD = os.getenv("PG_PASSWORD", "")
DB_HOST = os.getenv("PG_HOST", "")
DB_PORT = os.getenv("PG_PORT", "5432")
DB_NAME = os.getenv("PG_DATABASE", "")
SQLALCHEMY_URL = build_url(DB_HOST, int(DB_PORT), DB_NAME, DB_USER, DB_PASSWORD, async_driver=True)

config.set_main_option("sqlalchemy.url", SQLALCHEMY_URL)
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
