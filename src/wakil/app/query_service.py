"""Grounded question answering over the knowledge base."""

import json
from dataclasses import dataclass, field

from sqlalchemy import select

from wakil.app.search_service import SearchHit, search_workspace
from wakil.app.workspace_service import open_session
from wakil.config.settings import WorkspaceConfig
from wakil.llm.client import ModelClient
from wakil.llm.prompts import QUERY_SYSTEM_PROMPT, ContextBlock, build_query_prompt
from wakil.storage.schema import Memory, QueryRun, Source, Workspace, utcnow

# How much of each note/source to show the model.
NOTE_EXCERPT_CHARS = 2000
MAX_CONTEXT_BLOCKS = 12


@dataclass
class QueryResult:
    question: str
    answer: str
    contexts: list[ContextBlock] = field(default_factory=list)
    hits: list[SearchHit] = field(default_factory=list)
    query_run_id: int | None = None


def run_query(
    config: WorkspaceConfig,
    question: str,
    client: ModelClient,
    limit: int = 10,
    mode: str = "search",
) -> QueryResult:
    with open_session(config) as session:
        hits = search_workspace(session, config, question, limit=limit, mode=mode)
        contexts = _build_contexts(session, config, hits)

        run = QueryRun(
            workspace_id=session.scalar(
                select(Workspace.id).where(Workspace.root_path == str(config.root_path))
            ),
            query=question,
            status="started",
            metadata_json=json.dumps({"model": client.model, "mode": mode}),
        )
        session.add(run)
        session.flush()

        try:
            prompt = build_query_prompt(question, contexts)
            answer = client.complete(QUERY_SYSTEM_PROMPT, prompt)
            run.status = "completed"
            run.answer = answer
        except Exception:
            run.status = "error"
            session.commit()
            raise
        run.completed_at = utcnow()
        run.notes_used_json = json.dumps([c.ref for c in contexts if c.kind == "note"])
        run.memories_used_json = json.dumps([c.ref for c in contexts if c.kind == "memory"])
        run.sources_used_json = json.dumps([c.ref for c in contexts if c.kind == "source"])
        session.commit()
        return QueryResult(
            question=question,
            answer=answer,
            contexts=contexts,
            hits=hits,
            query_run_id=run.id,
        )


def _build_contexts(session, config: WorkspaceConfig, hits: list[SearchHit]) -> list[ContextBlock]:
    contexts: list[ContextBlock] = []
    for hit in hits[:MAX_CONTEXT_BLOCKS]:
        text = _load_text(session, config, hit)
        if text:
            contexts.append(ContextBlock(ref=hit.ref, kind=hit.kind, title=hit.title, text=text))
    return contexts


def _load_text(session, config: WorkspaceConfig, hit: SearchHit) -> str | None:
    if hit.kind == "note":
        path = config.root_path / hit.ref
        try:
            return path.read_text(encoding="utf-8", errors="replace")[:NOTE_EXCERPT_CHARS]
        except OSError:
            return hit.snippet or None
    if hit.kind == "memory":
        memory = session.get(Memory, int(hit.ref.split(":", 1)[1]))
        return memory.content if memory else None
    if hit.kind == "source":
        source = session.get(Source, int(hit.ref.split(":", 1)[1]))
        if source is None:
            return None
        if source.raw_text_path:
            try:
                raw = (config.root_path / source.raw_text_path).read_text(
                    encoding="utf-8", errors="replace"
                )
                return raw[:NOTE_EXCERPT_CHARS]
            except OSError:
                pass
        return source.title
    return None
