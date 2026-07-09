"""SQLite engine and session helpers."""

from pathlib import Path

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from wakil.storage.fts import ensure_fts
from wakil.storage.schema import Base


def create_db_engine(db_path: Path) -> Engine:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return create_engine(f"sqlite:///{db_path}")


def init_db(engine: Engine) -> None:
    Base.metadata.create_all(engine)
    ensure_fts(engine)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)
