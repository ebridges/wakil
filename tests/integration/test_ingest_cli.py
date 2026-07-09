import json
from pathlib import Path

from typer.testing import CliRunner

from wakil.cli.main import app

runner = CliRunner()

MODEL_JSON = json.dumps(
    {
        "title": "Meeting Notes",
        "summary": "A short meeting about routing.",
        "key_points": ["Routing prototype approved"],
        "memories": [{"type": "decision", "content": "Prototype approved.", "confidence": 0.9}],
        "relationships": [],
        "proposed_note": None,
    }
)


class FakeClient:
    model = "fake-model"

    def complete(self, system, prompt, max_tokens=8192):
        return MODEL_JSON


def _init(kb_path: Path) -> Path:
    runner.invoke(app, ["init", str(kb_path)])
    transcript = kb_path / "meeting.txt"
    transcript.write_text("We approved the routing prototype.\n")
    return transcript


def test_ingest_transcript_with_yes(kb_path: Path, monkeypatch):
    monkeypatch.setattr("wakil.llm.client.resolve_client", lambda: FakeClient())
    transcript = _init(kb_path)

    result = runner.invoke(
        app, ["ingest", "transcript", str(transcript), "--path", str(kb_path), "--yes"]
    )
    assert result.exit_code == 0, result.output
    assert "Ingest preview" in result.output
    assert "candidate memories" in result.output
    assert list((kb_path / "sources" / "transcripts").glob("*.md"))


def test_ingest_declining_writes_nothing(kb_path: Path, monkeypatch):
    monkeypatch.setattr("wakil.llm.client.resolve_client", lambda: None)
    transcript = _init(kb_path)

    result = runner.invoke(
        app, ["ingest", "transcript", str(transcript), "--path", str(kb_path)], input="n\n"
    )
    assert result.exit_code == 0
    assert "nothing was written" in result.output
    # The fixture KB ships one transcript; declining must not add another.
    assert [p.name for p in (kb_path / "sources" / "transcripts").iterdir()] == ["notitle.md"]


def test_ingest_without_provider_warns_but_ingests(kb_path: Path, monkeypatch):
    monkeypatch.setattr("wakil.llm.client.resolve_client", lambda: None)
    transcript = _init(kb_path)

    result = runner.invoke(
        app, ["ingest", "transcript", str(transcript), "--path", str(kb_path), "--yes"]
    )
    assert result.exit_code == 0
    assert "No model provider configured" in result.output
    assert list((kb_path / "sources" / "transcripts").glob("*.md"))


def test_ingest_duplicate_reports_and_exits(kb_path: Path, monkeypatch):
    monkeypatch.setattr("wakil.llm.client.resolve_client", lambda: None)
    transcript = _init(kb_path)

    runner.invoke(app, ["ingest", "transcript", str(transcript), "--path", str(kb_path), "--yes"])
    result = runner.invoke(
        app, ["ingest", "transcript", str(transcript), "--path", str(kb_path), "--yes"]
    )
    assert result.exit_code == 0
    assert "Already ingested" in result.output


def test_ingest_missing_file_fails(kb_path: Path, monkeypatch):
    monkeypatch.setattr("wakil.llm.client.resolve_client", lambda: None)
    runner.invoke(app, ["init", str(kb_path)])
    result = runner.invoke(
        app, ["ingest", "transcript", str(kb_path / "nope.txt"), "--path", str(kb_path), "--yes"]
    )
    assert result.exit_code == 1
    assert "Ingest failed" in result.output
