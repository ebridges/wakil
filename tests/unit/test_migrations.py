"""Phase B tests: Alembic wiring and the two entity-model column additions."""

import datetime as dt
from pathlib import Path

from alembic import command
from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Session

from wakil.app.workspace_service import init_workspace, open_session
from wakil.config.settings import WorkspaceConfig
from wakil.storage.database import (
    BASELINE_REVISION,
    _alembic_config,
    _head_revision,
    create_db_engine,
    init_db,
)
from wakil.storage.schema import Memory, Note, Relationship, Source, User, Workspace


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
    rebuild -- `id` is declared explicitly as INTEGER PRIMARY KEY in the
    rebuilt table because `CREATE TABLE ... AS SELECT` never preserves
    primary-key-ness, which would otherwise silently turn `id` into a plain
    column disconnected from SQLite's rowid/autoincrement -- every row
    inserted afterward would get `id = NULL` instead of a real id.)"""
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE alembic_version"))
        # enrichment_checkpoints (0007) is a whole table added post-baseline,
        # not a column on an already-existing table -- a real pre-Alembic
        # wakil.db never had it at all, so it must be dropped here too, or
        # replaying 0007's migration during the upgrade below tries to
        # CREATE TABLE a table that create_all() already built.
        connection.execute(text("DROP TABLE enrichment_checkpoints"))
        for table, columns in (
            ("memories", _LEGACY_MEMORY_COLUMNS),
            ("relationships", _LEGACY_RELATIONSHIP_COLUMNS),
            ("sources", _LEGACY_SOURCE_COLUMNS),
        ):
            other_columns = columns.split(", ")[1:]  # columns[0] is always "id"
            connection.execute(
                text(
                    f"CREATE TABLE {table}_legacy "
                    f"(id INTEGER PRIMARY KEY, {', '.join(other_columns)})"
                )
            )
            connection.execute(text(f"INSERT INTO {table}_legacy SELECT {columns} FROM {table}"))
            connection.execute(text(f"DROP TABLE {table}"))
            connection.execute(text(f"ALTER TABLE {table}_legacy RENAME TO {table}"))


def test_fresh_database_is_stamped_at_head(tmp_path: Path):
    engine = create_db_engine(tmp_path / "fresh.db")
    init_db(engine)

    assert _revision(engine) == _head_revision()
    assert "event_date" in _columns(engine, "memories")
    assert "stance" in _columns(engine, "memories")
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
    assert "stance" in _columns(engine, "memories")
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
        assert memory is not None
        assert memory.content == "kept"
        assert memory.event_date is None
        memory.event_date = dt.date(2026, 7, 9)
        session.commit()


def test_migration_dedupes_existing_content_hash_collisions(kb_path: Path):
    """Simulates a database from before uq_sources_workspace_content_hash
    existed, where the check-then-insert race in prepare_capture/
    apply_capture already let two Source rows share (workspace_id,
    content_hash) -- upgrading past 0004 must repoint anything referencing
    the duplicate onto the survivor (lowest id), drop the duplicate, and
    add the constraint that prevents it recurring."""
    init_workspace(kb_path)
    config = WorkspaceConfig.load(kb_path)
    engine = create_db_engine(config.database_path)

    _rewind_to_legacy(engine)  # strips 0002/0003 columns and alembic_version
    alembic_config = _alembic_config(engine)
    command.stamp(alembic_config, BASELINE_REVISION)
    command.upgrade(alembic_config, "0003")  # full current columns, no constraint yet

    with Session(engine) as session:
        workspace_id = session.scalar(select(Workspace.id))
        user_id = session.scalar(select(User.id))
        survivor = Source(workspace_id=workspace_id, source_type="text", content_hash="samehash")
        session.add(survivor)
        session.flush()
        survivor_id = survivor.id
        duplicate = Source(workspace_id=workspace_id, source_type="text", content_hash="samehash")
        session.add(duplicate)
        session.flush()
        duplicate_id = duplicate.id
        # Raw insert, not the Memory ORM class: the ORM model always reflects
        # the head schema, but this table is deliberately still at revision
        # 0003 here (pre-0005's `stance` column) -- using the ORM class would
        # break every time a later migration adds a new memories column.
        session.execute(
            text(
                "INSERT INTO memories "
                "(workspace_id, user_id, memory_type, content, source_id, state) "
                "VALUES (:workspace_id, :user_id, 'fact', 'x', :source_id, 'working')"
            ),
            {"workspace_id": workspace_id, "user_id": user_id, "source_id": duplicate_id},
        )
        session.commit()

    command.upgrade(alembic_config, "head")  # runs 0004 for real

    with open_session(config) as session:
        remaining = list(session.scalars(select(Source).where(Source.content_hash == "samehash")))
        assert [row.id for row in remaining] == [survivor_id]
        assert duplicate_id not in [row.id for row in remaining]
        memory = session.scalar(select(Memory))
        assert memory is not None
        assert memory.source_id == survivor_id

    index_names = {row["name"] for row in inspect(engine).get_indexes("sources")}
    assert "uq_sources_workspace_content_hash" in index_names


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
        assert stored is not None
        assert stored.event_date == dt.date(2026, 7, 1)


def test_stance_roundtrip(kb_path: Path):
    init_workspace(kb_path)
    config = WorkspaceConfig.load(kb_path)
    with open_session(config) as session:
        ws = session.scalar(select(Workspace.id))
        user = session.scalar(select(User.id))
        session.add(
            Memory(
                workspace_id=ws,
                user_id=user,
                memory_type="fact",
                content="AI pushed our PR volume way up, I dunno, maybe 80?",
                stance="casual",
            )
        )
        session.commit()
        stored = session.scalar(select(Memory).where(Memory.stance == "casual"))
        assert stored is not None
        assert stored.stance == "casual"


def test_note_to_note_relationship_roundtrip(kb_path: Path):
    init_workspace(kb_path)
    config = WorkspaceConfig.load(kb_path)
    with open_session(config) as session:
        ws = session.scalar(select(Workspace.id))
        notes = list(session.scalars(select(Note).limit(2)))
        # ADR 0006's intent (finalized in migration 0006): a note-only
        # Relationship row leaves the memory FKs NULL, no dummy row needed.
        session.add(
            Relationship(
                workspace_id=ws,
                predicate="mentions",
                subject_note_id=notes[0].id,
                object_note_id=notes[1].id,
            )
        )
        session.commit()

        # Backlinks as a live query, per entity-model.md.
        backlinks = list(
            session.scalars(
                select(Relationship).where(
                    Relationship.object_note_id == notes[1].id,
                    Relationship.predicate == "mentions",
                )
            )
        )
        assert any(
            row.subject_note_id == notes[0].id
            and row.subject_memory_id is None
            and row.object_memory_id is None
            for row in backlinks
        )
