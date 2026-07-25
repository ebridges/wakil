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

CAPTURE_METADATA_JSON = json.dumps(
    {
        "title": "2026-07-09 Fake Capture Title",
        "abstract": "A fake abstract for CLI capture tests, roughly the length a real one "
        "would be, useful for retrieval without being a full summary.",
    }
)


class FakeClient:
    """Extraction then resolution, one scripted payload per call."""

    model = "fake-model"

    def __init__(self, payloads=(EXTRACTION_JSON, RESOLUTION_JSON)):
        self.queue = list(payloads)

    def complete(self, system, prompt, max_tokens=8192):
        assert self.queue, "FakeClient ran out of scripted responses"
        return self.queue.pop(0)


class FakeCaptureClient(FakeClient):
    """The capture-time title/abstract call: one scripted CaptureMetadata payload."""

    def __init__(self, payloads=(CAPTURE_METADATA_JSON,)):
        super().__init__(payloads)


def _client_queue(monkeypatch, *clients):
    """Patch resolve_client to hand back each client in order, one per call —
    capture and enrich each resolve a client independently, so a test doing
    both needs a distinct scripted client for each."""
    it = iter(clients)
    monkeypatch.setattr("wakil.llm.client.resolve_client", lambda: next(it))


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


def test_capture_requires_a_model_provider(kb_path: Path, monkeypatch):
    # Capture-time title/abstract generation (docs/adr/0010) means capture
    # now needs a model provider, same as enrich.
    monkeypatch.setattr("wakil.llm.client.resolve_client", lambda: None)
    transcript = _init(kb_path)

    result = _capture(kb_path, transcript)
    assert result.exit_code == 1
    assert "needs a model provider" in result.output
    assert [p.name for p in (kb_path / "sources" / "transcripts").iterdir()] == ["notitle.md"]


def test_capture_writes_raw_file_and_reports_source(kb_path: Path, monkeypatch):
    _client_queue(monkeypatch, FakeCaptureClient())
    transcript = _init(kb_path)

    result = _capture(kb_path, transcript)
    assert result.exit_code == 0, result.output
    assert "Capture preview" in result.output
    assert "Captured source #1" in result.output
    assert "wakil enrich 1" in result.output
    assert list((kb_path / "sources" / "transcripts").glob("2*.md"))


def test_capture_declining_writes_nothing(kb_path: Path, monkeypatch):
    _client_queue(monkeypatch, FakeCaptureClient())
    transcript = _init(kb_path)
    result = runner.invoke(
        app, ["-w", str(kb_path), "ingest", "transcript", str(transcript)], input="n\n"
    )
    assert result.exit_code == 0
    assert "nothing was written" in result.output
    assert [p.name for p in (kb_path / "sources" / "transcripts").iterdir()] == ["notitle.md"]


def test_capture_duplicate_reports(kb_path: Path, monkeypatch):
    _client_queue(monkeypatch, FakeCaptureClient(), FakeCaptureClient())
    transcript = _init(kb_path)
    _capture(kb_path, transcript)
    result = _capture(kb_path, transcript)
    assert result.exit_code == 0
    assert "Already ingested" in result.output


def test_capture_with_context_shows_in_preview(kb_path: Path, monkeypatch):
    _client_queue(monkeypatch, FakeCaptureClient())
    transcript = _init(kb_path)
    result = _capture(kb_path, transcript, "--context", "Attendees: Jane Doe, Bob (Acme).")
    assert result.exit_code == 0
    assert "Context: Attendees" in result.output.replace("\n", " ")


def test_enrich_flow(kb_path: Path, monkeypatch):
    _client_queue(monkeypatch, FakeCaptureClient(), FakeClient(), FakeClient())
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
    bad_client = FakeClient((EXTRACTION_JSON, BAD_RESOLUTION_JSON))
    _client_queue(monkeypatch, FakeCaptureClient(), bad_client)
    transcript = _init(kb_path)
    _capture(kb_path, transcript)

    result = runner.invoke(app, ["-w", str(kb_path), "enrich", "1", "--yes", "--local"])
    assert result.exit_code == 1
    assert "failed validation" in result.output
    assert "guild" in result.output
    assert not (kb_path / "meetings" / "2026" / "2026-07-09-routing.md").exists()


def test_enrich_requires_provider(kb_path: Path, monkeypatch):
    _client_queue(monkeypatch, FakeCaptureClient(), None)
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


def test_capture_missing_file_fails(kb_path: Path, monkeypatch):
    _client_queue(monkeypatch, FakeCaptureClient())
    runner.invoke(app, ["init", str(kb_path)])
    result = runner.invoke(
        app, ["-w", str(kb_path), "ingest", "transcript", str(kb_path / "nope.txt"), "--yes"]
    )
    assert result.exit_code == 1
    assert "Ingest failed" in result.output


def test_capture_context_file_shows_in_preview(kb_path: Path, monkeypatch):
    _client_queue(monkeypatch, FakeCaptureClient())
    transcript = _init(kb_path)
    context_file = kb_path / "context.txt"
    context_file.write_text("Extra context from a file.\n")

    result = _capture(kb_path, transcript, "--context-file", str(context_file))
    assert result.exit_code == 0, result.output
    assert "Extra context from a file." in result.output


def test_capture_repeated_context_joined_in_order(kb_path: Path, monkeypatch):
    _client_queue(monkeypatch, FakeCaptureClient())
    transcript = _init(kb_path)
    result = _capture(kb_path, transcript, "--context", "FirstBit", "--context", "SecondBit")
    assert result.exit_code == 0, result.output
    flat = result.output.replace("\n", " ")
    assert "FirstBit" in flat
    assert "SecondBit" in flat
    assert flat.index("FirstBit") < flat.index("SecondBit")


def test_capture_context_file_reference_expands(kb_path: Path, monkeypatch):
    _client_queue(monkeypatch, FakeCaptureClient())
    transcript = _init(kb_path)
    referenced = kb_path / "attendees.txt"
    referenced.write_text("Jane Doe, Bob (Acme).\n")

    result = _capture(kb_path, transcript, "--context", "See @file:attendees.txt for detail.")
    assert result.exit_code == 0, result.output
    assert "Jane Doe, Bob (Acme)." in result.output
    # The inline @file: token is stripped from its original position and the
    # referenced content is appended in an "Attached Context" block instead --
    # the raw "@file:attendees.txt" text does resurface there as that block's
    # label, it just no longer sits inline in the sentence that referenced it.
    assert "See @file:attendees.txt for detail." not in result.output.replace("\n", " ")


def test_capture_context_reference_outside_workspace_fails(tmp_path: Path, kb_path: Path):
    # Context resolution happens before the model-provider check, so no
    # client needs to be scripted here -- this fails before any model call.
    transcript = _init(kb_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("outside content\n")

    result = _capture(kb_path, transcript, "--context", f"See @file:{outside} for detail.")
    assert result.exit_code == 1
    assert "outside the workspace" in result.output
    assert [p.name for p in (kb_path / "sources" / "transcripts").iterdir()] == ["notitle.md"]


def test_capture_context_reference_missing_file_fails(kb_path: Path):
    transcript = _init(kb_path)
    result = _capture(kb_path, transcript, "--context", "See @file:nope.txt for detail.")
    assert result.exit_code == 1
    assert "File not found" in result.output
    assert [p.name for p in (kb_path / "sources" / "transcripts").iterdir()] == ["notitle.md"]


def test_capture_context_file_path_missing_fails(kb_path: Path):
    transcript = _init(kb_path)
    result = _capture(kb_path, transcript, "--context-file", str(kb_path / "nope-ctx.txt"))
    assert result.exit_code == 1
    assert "Could not read context file" in result.output
    assert [p.name for p in (kb_path / "sources" / "transcripts").iterdir()] == ["notitle.md"]


def test_capture_context_hard_budget_aborts(kb_path: Path, monkeypatch):
    monkeypatch.setattr("wakil.app.context_references.MODEL_CONTEXT_WINDOW_TOKENS", 100)
    transcript = _init(kb_path)

    result = _capture(kb_path, transcript, "--context", "x" * 1000)
    assert result.exit_code == 1
    assert "hard budget" in result.output
    assert [p.name for p in (kb_path / "sources" / "transcripts").iterdir()] == ["notitle.md"]


def test_capture_context_soft_budget_warns_and_succeeds(kb_path: Path, monkeypatch):
    _client_queue(monkeypatch, FakeCaptureClient())
    monkeypatch.setattr("wakil.app.context_references.MODEL_CONTEXT_WINDOW_TOKENS", 100)
    transcript = _init(kb_path)

    result = _capture(kb_path, transcript, "--context", "x" * 120)
    assert result.exit_code == 0, result.output
    assert "soft budget" in result.output
    assert list((kb_path / "sources" / "transcripts").glob("2*.md"))


def test_enrich_context_shows_in_preview(kb_path: Path, monkeypatch):
    _client_queue(monkeypatch, FakeCaptureClient(), FakeClient())
    transcript = _init(kb_path)
    _capture(kb_path, transcript)

    result = runner.invoke(
        app,
        [
            "-w",
            str(kb_path),
            "enrich",
            "1",
            "--yes",
            "--local",
            "--context",
            "Attendees: Jane Doe, Bob (Acme).",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Context: Attendees" in result.output.replace("\n", " ")


def test_enrich_bad_context_aborts_before_branch_switch(kb_path: Path, monkeypatch):
    _client_queue(monkeypatch, FakeCaptureClient(), FakeClient())
    transcript = _init(kb_path)
    _capture(kb_path, transcript)

    result = runner.invoke(
        app,
        [
            "-w",
            str(kb_path),
            "enrich",
            "1",
            "--yes",
            "--context",
            "See @file:nope.txt for detail.",
        ],
    )
    assert result.exit_code == 1
    assert "Context resolution failed" in result.output
    assert "On branch" not in result.output


def test_sources_list_empty(kb_path: Path):
    runner.invoke(app, ["init", str(kb_path)])
    result = runner.invoke(app, ["-w", str(kb_path), "sources", "list"])
    assert result.exit_code == 0
    assert "No sources match" in result.output


def test_sources_list_shows_captured_source(kb_path: Path, monkeypatch):
    _client_queue(monkeypatch, FakeCaptureClient())
    transcript = _init(kb_path)
    _capture(kb_path, transcript)

    result = runner.invoke(app, ["-w", str(kb_path), "sources", "list"])
    assert result.exit_code == 0, result.output
    assert "2026-07-09 Fake Capture Title" in result.output
    assert "raw" in result.output
    assert "wakil sources show <id> for detail" in result.output


def test_sources_list_status_filter(kb_path: Path, monkeypatch):
    _client_queue(monkeypatch, FakeCaptureClient(), FakeClient(), FakeClient())
    transcript = _init(kb_path)
    _capture(kb_path, transcript)
    runner.invoke(app, ["-w", str(kb_path), "enrich", "1", "--yes", "--local"])

    raw_result = runner.invoke(app, ["-w", str(kb_path), "sources", "list", "--status", "raw"])
    assert raw_result.exit_code == 0
    assert "No sources match" in raw_result.output

    enriched_result = runner.invoke(
        app, ["-w", str(kb_path), "sources", "list", "--status", "enriched"]
    )
    assert enriched_result.exit_code == 0
    assert "enriched" in enriched_result.output


def test_sources_list_limit_zero_means_unbounded(kb_path: Path, monkeypatch):
    _client_queue(monkeypatch, FakeCaptureClient())
    transcript = _init(kb_path)
    _capture(kb_path, transcript)

    result = runner.invoke(app, ["-w", str(kb_path), "sources", "list", "--limit", "0"])
    assert result.exit_code == 0
    assert "2026-07-09 Fake Capture Title" in result.output


def test_sources_show_detail(kb_path: Path, monkeypatch):
    _client_queue(monkeypatch, FakeCaptureClient())
    transcript = _init(kb_path)
    _capture(kb_path, transcript)

    result = runner.invoke(app, ["-w", str(kb_path), "sources", "show", "1"])
    assert result.exit_code == 0, result.output
    assert "Source #1" in result.output
    assert "2026-07-09 Fake Capture Title" in result.output
    assert "sources/transcripts" in result.output


def test_sources_show_unknown_id(kb_path: Path):
    runner.invoke(app, ["init", str(kb_path)])
    result = runner.invoke(app, ["-w", str(kb_path), "sources", "show", "42"])
    assert result.exit_code == 1
    assert "No source with id 42" in result.output
    assert "Enrichment preview" not in result.output
