"""SQLite engine, session helpers, and schema migration.

Every workspace has its own SQLite database, so migration runs when the
database is opened rather than as a global step. Three cases in init_db:

- fresh database: create_all() builds the current schema, then it's stamped
  at the Alembic head (no migrations replayed);
- pre-Alembic database (tables exist, no alembic_version): stamped at the
  baseline revision, then upgraded to head;
- versioned database: upgraded to head only when it's actually behind.

This touches only wakil's own operational store under .wakil/ — never the
Markdown knowledge base.
"""

from functools import lru_cache
from pathlib import Path

from alembic import command
from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from wakil.storage.fts import ensure_fts
from wakil.storage.schema import Base

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
BASELINE_REVISION = "0001"


def create_db_engine(db_path: Path) -> Engine:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return create_engine(f"sqlite:///{db_path}")


def _alembic_config(engine: Engine) -> AlembicConfig:
    config = AlembicConfig()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("sqlalchemy.url", engine.url.render_as_string(hide_password=False))
    return config


@lru_cache(maxsize=1)
def _head_revision() -> str:
    head = ScriptDirectory(str(MIGRATIONS_DIR)).get_current_head()
    if head is None:
        raise RuntimeError(f"No migration head found in {MIGRATIONS_DIR}")
    return head


def _current_revision(engine: Engine) -> str | None:
    with engine.connect() as connection:
        return connection.execute(text("SELECT version_num FROM alembic_version")).scalar()


def init_db(engine: Engine) -> None:
    tables = set(inspect(engine).get_table_names())
    if "alembic_version" in tables:
        if _current_revision(engine) != _head_revision():
            command.upgrade(_alembic_config(engine), "head")
    elif tables:
        # Pre-Alembic database from an earlier wakil version.
        config = _alembic_config(engine)
        command.stamp(config, BASELINE_REVISION)
        command.upgrade(config, "head")
    else:
        Base.metadata.create_all(engine)
        command.stamp(_alembic_config(engine), "head")
    ensure_fts(engine)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)
