"""Alembic environment for wakil's per-workspace SQLite databases.

Invoked two ways: programmatically from `wakil.storage.database.init_db`
(which sets sqlalchemy.url per workspace), and via the repo-root
`alembic.ini` for development (autogenerating future revisions).
"""

from alembic import context
from sqlalchemy import engine_from_config, pool

from wakil.storage.schema import Base

config = context.config
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
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
    with connectable.connect() as connection:
        # render_as_batch: SQLite can't ALTER most things in place; batch
        # mode rebuilds the table when a future migration needs it.
        context.configure(
            connection=connection, target_metadata=target_metadata, render_as_batch=True
        )
        with context.begin_transaction():
            context.run_migrations()
    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
