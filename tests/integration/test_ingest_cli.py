import json
from pathlib import Path

from typer.testing import CliRunner

from wakil.cli.main import app

runner = CliRunner()

EXTRACTION_JSON = json.dumps(
    {
        "title": "Meeting Notes",
        "summary": "A short meeting about routing.",
        "key_points": ["Routing prototype approved"],
        "memories": [{"type": "decision", "content": "Prototype approved.", "confidence": 0.9}],
        "relationships": [],
        "proposed_note": {
            "path": "meetings/2026/2026-07-09-routing.md",
            "markdown": (
                "---\ntype: meeting\ntitle: Routing\ndate: 2026-07-09\n"
                "created: 2026-07-09\n---\n\n# Routing\n"
            ),
        },
    }
)

RESOLUTION_JSON = json.dumps(
    {
        "entities": [
            {
                "name": "Dana Prieto",
                "entity_type": "person",
                "action": "create",
                "confidence": 0.9,
                "proposed_frontmatter": {"status": "active"},
            }
        ]
    }
)

BAD_RESOLUTION_JSON = json.dumps(
    {"entities": [{"name": "The Guild", "entity_type": "guild", "action": "create"}]}
)


class FakeClient:
    """Extraction then resolution, one scripted payload per call."""

    model = "fake-model"

    def __init__(self, payloads=(EXTRACTION_JSON, RESOLUTION_JSON)):
        self.queue = list(payloads)

    def complete(self, system, prompt, max_tokens=8192):
        assert self.queue, "FakeClient ran out of scripted responses"
        return self.queue.pop(0)


def _init(kb_path: Path) -> Path:
    runner.invoke(app, ["init", str(kb_path)])
    transcript = kb_path / "meeting.txt"
    transcript.write_text("We approved the routing prototype.\n")
    return transcript


def _capture(kb_path: Path, transcript: Path, *extra: str):
    # These fixtures aren't git repos; --local skips the (now default-on)
    # branch/commit/PR landing so these tests stay focused on capture/
    # enrichment mechanics. Git landing itself is covered in test_git_cli.py.
    return runner.invoke(
        app,
        ["-w", str(kb_path), "ingest", "transcript", str(transcript), "--yes", "--local", *extra],
    )


def test_capture_needs_no_model(kb_path: Path, monkeypatch):
    # Even with no provider configured, capture works silently.
    monkeypatch.setattr("wakil.llm.client.resolve_client", lambda: None)
    transcript = _init(kb_path)

    result = _capture(kb_path, transcript)
    assert result.exit_code == 0, result.output
    assert "Capture preview" in result.output
    assert "Captured source #1" in result.output
    assert "wakil enrich 1" in result.output
    assert "No model provider" not in result.output
    assert list((kb_path / "sources" / "transcripts").glob("2*.md"))


def test_capture_declining_writes_nothing(kb_path: Path):
    transcript = _init(kb_path)
    result = runner.invoke(
        app, ["-w", str(kb_path), "ingest", "transcript", str(transcript)], input="n\n"
    )
    assert result.exit_code == 0
    assert "nothing was written" in result.output
    assert [p.name for p in (kb_path / "sources" / "transcripts").iterdir()] == ["notitle.md"]


def test_capture_duplicate_reports(kb_path: Path):
    transcript = _init(kb_path)
    _capture(kb_path, transcript)
    result = _capture(kb_path, transcript)
    assert result.exit_code == 0
    assert "Already ingested" in result.output


def test_capture_with_context_shows_in_preview(kb_path: Path):
    transcript = _init(kb_path)
    result = _capture(kb_path, transcript, "--context", "Attendees: Jane Doe, Bob (Acme).")
    assert result.exit_code == 0
    assert "Context: Attendees" in result.output.replace("\n", " ")


def test_enrich_flow(kb_path: Path, monkeypatch):
    monkeypatch.setattr("wakil.llm.client.resolve_client", lambda: FakeClient())
    transcript = _init(kb_path)
    _capture(kb_path, transcript)

    result = runner.invoke(app, ["-w", str(kb_path), "enrich", "1", "--yes", "--local"])
    assert result.exit_code == 0, result.output
    assert "Enrichment preview" in result.output
    assert "Entity resolution" in result.output  # decisions shown before confirm
    assert "Dana Prieto" in result.output
    assert "candidate memories" in result.output
    assert (kb_path / "meetings" / "2026" / "2026-07-09-routing.md").exists()
    assert (kb_path / "people" / "dana-prieto.md").exists()  # stub created

    # Second run refuses without --force.
    result = runner.invoke(app, ["-w", str(kb_path), "enrich", "1", "--yes", "--local"])
    assert result.exit_code == 1
    assert "already enriched" in result.output


def test_enrich_blocked_on_schema_gap(kb_path: Path, monkeypatch):
    # An entity type with no schema is a hard stop: preview shows the gap,
    # nothing is written, even with --yes.
    monkeypatch.setattr(
        "wakil.llm.client.resolve_client",
        lambda: FakeClient((EXTRACTION_JSON, BAD_RESOLUTION_JSON)),
    )
    transcript = _init(kb_path)
    _capture(kb_path, transcript)

    result = runner.invoke(app, ["-w", str(kb_path), "enrich", "1", "--yes", "--local"])
    assert result.exit_code == 1
    assert "failed validation" in result.output
    assert "guild" in result.output
    assert not (kb_path / "meetings" / "2026" / "2026-07-09-routing.md").exists()


def test_enrich_requires_provider(kb_path: Path, monkeypatch):
    monkeypatch.setattr("wakil.llm.client.resolve_client", lambda: None)
    transcript = _init(kb_path)
    _capture(kb_path, transcript)

    result = runner.invoke(app, ["-w", str(kb_path), "enrich", "1", "--yes", "--local"])
    assert result.exit_code == 1
    assert "needs a model provider" in result.output


def test_enrich_unknown_source(kb_path: Path, monkeypatch):
    monkeypatch.setattr("wakil.llm.client.resolve_client", lambda: FakeClient())
    runner.invoke(app, ["init", str(kb_path)])
    result = runner.invoke(app, ["-w", str(kb_path), "enrich", "42", "--yes", "--local"])
    assert result.exit_code == 1
    assert "No source with id" in result.output


def test_capture_missing_file_fails(kb_path: Path):
    runner.invoke(app, ["init", str(kb_path)])
    result = runner.invoke(
        app, ["-w", str(kb_path), "ingest", "transcript", str(kb_path / "nope.txt"), "--yes"]
    )
    assert result.exit_code == 1
    assert "Ingest failed" in result.output
