"""Phase B tests: Alembic wiring and the two entity-model column additions."""

import datetime as dt
from pathlib import Path

from sqlalchemy import inspect, select, text

from wakil.app.workspace_service import init_workspace, open_session
from wakil.config.settings import WorkspaceConfig
from wakil.storage.database import (
    BASELINE_REVISION,
    _head_revision,
    create_db_engine,
    init_db,
)
from wakil.storage.schema import Memory, Note, Relationship, User, Workspace


def _columns(engine, table: str) -> set[str]:
    return {column["name"] for column in inspect(engine).get_columns(table)}


def _revision(engine) -> str | None:
    with engine.connect() as connection:
        return connection.execute(text("SELECT version_num FROM alembic_version")).scalar()


_LEGACY_MEMORY_COLUMNS = (
    "id, workspace_id, user_id, memory_type, content, summary, source_id, note_id, "
    "confidence, state, importance, freshness, last_seen_at, metadata_json, "
    "created_at, updated_at"
)
_LEGACY_RELATIONSHIP_COLUMNS = (
    "id, workspace_id, subject_memory_id, predicate, object_memory_id, source_id, "
    "note_id, confidence, metadata_json, created_at"
)
_LEGACY_SOURCE_COLUMNS = (
    "id, workspace_id, source_type, title, origin, author, published_at, "
    "retrieved_at, content_hash, raw_text_path, status, metadata_json, "
    "created_at, updated_at"
)


def _rewind_to_legacy(engine) -> None:
    """Rebuild memories/relationships/sources without the Phase B/C columns
    and drop the version table, simulating a wakil.db written before Alembic
    existed. (SQLite can't DROP COLUMN on FK-referenced columns, hence the
    rebuild.)"""
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE alembic_version"))
        for table, columns in (
            ("memories", _LEGACY_MEMORY_COLUMNS),
            ("relationships", _LEGACY_RELATIONSHIP_COLUMNS),
            ("sources", _LEGACY_SOURCE_COLUMNS),
        ):
            connection.execute(
                text(f"CREATE TABLE {table}_legacy AS SELECT {columns} FROM {table}")
            )
            connection.execute(text(f"DROP TABLE {table}"))
            connection.execute(text(f"ALTER TABLE {table}_legacy RENAME TO {table}"))


def test_fresh_database_is_stamped_at_head(tmp_path: Path):
    engine = create_db_engine(tmp_path / "fresh.db")
    init_db(engine)

    assert _revision(engine) == _head_revision()
    assert "event_date" in _columns(engine, "memories")
    assert {"subject_note_id", "object_note_id"} <= _columns(engine, "relationships")


def test_legacy_database_is_stamped_and_upgraded(tmp_path: Path):
    # Build a database in the pre-Alembic shape: current tables, then strip
    # the new columns and the version table to simulate an old wakil.db.
    engine = create_db_engine(tmp_path / "legacy.db")
    init_db(engine)
    _rewind_to_legacy(engine)
    assert "event_date" not in _columns(engine, "memories")
    assert "subject_note_id" not in _columns(engine, "relationships")
    assert "git_branch" not in _columns(engine, "sources")

    init_db(engine)  # the upgrade path an existing workspace takes

    assert _revision(engine) == _head_revision()
    assert "event_date" in _columns(engine, "memories")
    assert {"subject_note_id", "object_note_id"} <= _columns(engine, "relationships")
    assert {"git_branch", "git_pr_url"} <= _columns(engine, "sources")


def test_legacy_database_data_survives_migration(tmp_path: Path, kb_path: Path):
    init_workspace(kb_path)
    config = WorkspaceConfig.load(kb_path)
    with open_session(config) as session:
        ws = session.scalar(select(Workspace.id))
        user = session.scalar(select(User.id))
        session.add(Memory(workspace_id=ws, user_id=user, memory_type="fact", content="kept"))
        session.commit()

    # Rewind the database to the pre-Alembic shape, data intact.
    engine = create_db_engine(config.database_path)
    _rewind_to_legacy(engine)

    with open_session(config) as session:  # init_db runs the migration here
        memory = session.scalar(select(Memory))
        assert memory.content == "kept"
        assert memory.event_date is None
        memory.event_date = dt.date(2026, 7, 9)
        session.commit()


def test_baseline_revision_is_the_chain_root():
    assert BASELINE_REVISION == "0001"
    assert _head_revision() != BASELINE_REVISION  # 0002 exists past the anchor


def test_sqlite_pragmas_configured_for_concurrency(tmp_path: Path):
    engine = create_db_engine(tmp_path / "pragma.db")
    init_db(engine)
    with engine.connect() as connection:
        assert connection.execute(text("PRAGMA journal_mode")).scalar() == "wal"
        assert connection.execute(text("PRAGMA busy_timeout")).scalar() == 30_000


def test_concurrent_writer_waits_instead_of_failing_immediately(tmp_path: Path):
    """A second connection's write must wait (busy_timeout) rather than
    immediately raising 'database is locked' while the first holds the
    write lock -- proves the PRAGMA changes actual behavior, not just its
    own reported value. Two engines simulate two separate `wakil`
    processes writing to the same wakil.db."""
    import threading
    import time

    db_path = tmp_path / "concurrent.db"
    engine_a = create_db_engine(db_path)
    init_db(engine_a)
    engine_b = create_db_engine(db_path)

    events: dict[str, float] = {}

    def hold_write_lock() -> None:
        with engine_a.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.execute(text("BEGIN IMMEDIATE"))
            events["locked_at"] = time.monotonic()
            time.sleep(0.5)
            conn.execute(text("COMMIT"))
            events["released_at"] = time.monotonic()

    holder = threading.Thread(target=hold_write_lock)
    holder.start()
    while "locked_at" not in events:
        time.sleep(0.01)

    with engine_b.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(text("BEGIN IMMEDIATE"))  # blocks here until engine_a commits
        events["second_acquired_at"] = time.monotonic()
        conn.execute(text("COMMIT"))
    holder.join()

    # The second writer waited for the first to release rather than
    # erroring immediately with "database is locked".
    assert events["second_acquired_at"] >= events["released_at"] - 0.05


def test_init_db_is_idempotent_and_cheap_when_current(tmp_path: Path):
    engine = create_db_engine(tmp_path / "idem.db")
    init_db(engine)
    revision = _revision(engine)
    init_db(engine)
    init_db(engine)
    assert _revision(engine) == revision


def test_event_date_roundtrip(kb_path: Path):
    init_workspace(kb_path)
    config = WorkspaceConfig.load(kb_path)
    with open_session(config) as session:
        ws = session.scalar(select(Workspace.id))
        user = session.scalar(select(User.id))
        session.add(
            Memory(
                workspace_id=ws,
                user_id=user,
                memory_type="event",
                content="Kickoff happened.",
                event_date=dt.date(2026, 7, 1),
            )
        )
        session.commit()
        stored = session.scalar(select(Memory).where(Memory.memory_type == "event"))
        assert stored.event_date == dt.date(2026, 7, 1)


def test_note_to_note_relationship_roundtrip(kb_path: Path):
    init_workspace(kb_path)
    config = WorkspaceConfig.load(kb_path)
    with open_session(config) as session:
        ws = session.scalar(select(Workspace.id))
        user = session.scalar(select(User.id))
        notes = list(session.scalars(select(Note).limit(2)))
        memory = Memory(workspace_id=ws, user_id=user, memory_type="fact", content="x")
        session.add(memory)
        session.flush()
        session.add(
            Relationship(
                workspace_id=ws,
                subject_memory_id=memory.id,
                predicate="mentions",
                object_memory_id=memory.id,
                subject_note_id=notes[0].id,
                object_note_id=notes[1].id,
            )
        )
        session.commit()

        # Backlinks as a live query, per entity-model.md.
        backlinks = list(
            session.scalars(select(Relationship).where(Relationship.object_note_id == notes[1].id))
        )
        assert len(backlinks) == 1
        assert backlinks[0].subject_note_id == notes[0].id
