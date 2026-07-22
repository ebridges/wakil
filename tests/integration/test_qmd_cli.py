import json
from pathlib import Path

import yaml
from typer.testing import CliRunner

from wakil.cli.main import app

runner = CliRunner()

CAPTURE_METADATA_JSON = json.dumps(
    {
        "title": "2026-07-09 Fake Capture Title",
        "abstract": "A fake abstract for CLI capture tests, roughly the length a real one "
        "would be, useful for retrieval without being a full summary.",
    }
)


class _FakeCaptureClient:
    """The capture-time title/abstract call (docs/adr/0010): one scripted payload."""

    model = "fake-model"

    def complete(self, system, prompt, max_tokens=8192):
        return CAPTURE_METADATA_JSON


def _fake_qmd_run(qmd_dir: Path):
    """A stand-in for the real qmd binary: only implements `collection add`
    and `collection remove` against the YAML config file, mirroring the real
    tool's own behavior closely enough to exercise the round trip."""

    class FakeCompleted:
        def __init__(self, returncode=0, stdout="", stderr=""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def fake_run(cmd, **kwargs):
        config_path = qmd_dir / "index.yml"
        data = {}
        if config_path.is_file():
            data = yaml.safe_load(config_path.read_text()) or {}
        data.setdefault("collections", {})

        if cmd[:3] == ["qmd", "collection", "add"]:
            path = cmd[3]
            name = None
            pattern = "**/*.md"
            i = 4
            while i < len(cmd):
                if cmd[i] == "--name":
                    name = cmd[i + 1]
                    i += 2
                elif cmd[i] == "--mask":
                    pattern = cmd[i + 1]
                    i += 2
                else:
                    i += 1
            name = name or Path(path).name
            if name in data["collections"]:
                return FakeCompleted(returncode=1, stderr=f"Collection '{name}' already exists.\n")
            data["collections"][name] = {"path": path, "pattern": pattern}
            qmd_dir.mkdir(parents=True, exist_ok=True)
            config_path.write_text(yaml.safe_dump(data))
            return FakeCompleted(stdout=f"Collection '{name}' created successfully\n")

        if cmd[:3] == ["qmd", "collection", "remove"]:
            name = cmd[3]
            if name not in data["collections"]:
                return FakeCompleted(returncode=1, stderr=f"Collection not found: {name}\n")
            del data["collections"][name]
            config_path.write_text(yaml.safe_dump(data))
            return FakeCompleted(stdout=f"Removed collection '{name}'\n")

        if cmd == ["qmd", "update"]:
            return FakeCompleted(stdout="All collections updated.\n")

        if cmd == ["qmd", "embed"]:
            return FakeCompleted(stdout="Embedded 0 chunks from 0 documents\n")

        return FakeCompleted(returncode=1, stderr="unsupported in test double")

    return fake_run


def _patch_qmd(monkeypatch, qmd_dir: Path):
    monkeypatch.setattr("wakil.integrations.qmd.shutil.which", lambda name: "/usr/bin/qmd")
    monkeypatch.setattr("wakil.integrations.qmd.subprocess.run", _fake_qmd_run(qmd_dir))


def _init(kb_path: Path, monkeypatch, *, skip_qmd_collection: bool):
    """Patch qmd *before* init, since init itself now auto-creates the
    default collection unless --no-qmd-collection is passed."""
    qmd_dir = kb_path / ".wakil" / "qmd"
    _patch_qmd(monkeypatch, qmd_dir)
    args = ["init", str(kb_path)]
    if skip_qmd_collection:
        args.append("--no-qmd-collection")
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    return qmd_dir


def test_init_creates_default_qmd_collection(kb_path: Path, monkeypatch):
    _init(kb_path, monkeypatch, skip_qmd_collection=False)

    result = runner.invoke(app, ["-w", str(kb_path), "qmd", "collection", "list"])
    assert "kb" in result.output.replace("\n", "")


def test_init_no_qmd_collection_flag_skips_creation(kb_path: Path, monkeypatch):
    _init(kb_path, monkeypatch, skip_qmd_collection=True)

    result = runner.invoke(app, ["-w", str(kb_path), "qmd", "collection", "list"])
    assert "No QMD collections registered" in result.output


def test_qmd_collection_add_then_list(kb_path: Path, monkeypatch):
    _init(kb_path, monkeypatch, skip_qmd_collection=True)

    result = runner.invoke(app, ["-w", str(kb_path), "qmd", "collection", "add", "concepts"])
    assert result.exit_code == 0, result.output

    result = runner.invoke(app, ["-w", str(kb_path), "qmd", "collection", "list"])
    assert result.exit_code == 0
    assert "concepts" in result.output.replace("\n", "")


def test_qmd_collection_add_rejects_escaping_path(kb_path: Path, monkeypatch):
    _init(kb_path, monkeypatch, skip_qmd_collection=True)

    result = runner.invoke(app, ["-w", str(kb_path), "qmd", "collection", "add", "../outside"])
    assert result.exit_code == 1


def test_qmd_collection_remove(kb_path: Path, monkeypatch):
    _init(kb_path, monkeypatch, skip_qmd_collection=True)

    runner.invoke(app, ["-w", str(kb_path), "qmd", "collection", "add", "concepts"])
    result = runner.invoke(app, ["-w", str(kb_path), "qmd", "collection", "remove", "concepts"])
    assert result.exit_code == 0, result.output

    result = runner.invoke(app, ["-w", str(kb_path), "qmd", "collection", "list"])
    assert "No QMD collections registered" in result.output


def test_qmd_sync_proposes_single_collection_when_none_exist(kb_path: Path, monkeypatch):
    _init(kb_path, monkeypatch, skip_qmd_collection=True)

    result = runner.invoke(app, ["-w", str(kb_path), "qmd", "sync"], input="y\n")
    assert result.exit_code == 0, result.output
    assert "kb" in result.output.replace("\n", "")

    result = runner.invoke(app, ["-w", str(kb_path), "qmd", "collection", "list"])
    assert "kb" in result.output.replace("\n", "")


def test_qmd_sync_declines_without_confirmation(kb_path: Path, monkeypatch):
    _init(kb_path, monkeypatch, skip_qmd_collection=True)

    result = runner.invoke(app, ["-w", str(kb_path), "qmd", "sync"], input="n\n")
    assert result.exit_code == 0, result.output

    result = runner.invoke(app, ["-w", str(kb_path), "qmd", "collection", "list"])
    assert "No QMD collections registered" in result.output


def test_qmd_sync_yes_flag_skips_prompt(kb_path: Path, monkeypatch):
    _init(kb_path, monkeypatch, skip_qmd_collection=True)

    result = runner.invoke(app, ["-w", str(kb_path), "qmd", "sync", "--yes"])
    assert result.exit_code == 0, result.output

    result = runner.invoke(app, ["-w", str(kb_path), "qmd", "collection", "list"])
    assert "kb" in result.output.replace("\n", "")


def test_qmd_sync_noop_once_default_collection_exists(kb_path: Path, monkeypatch):
    _init(kb_path, monkeypatch, skip_qmd_collection=False)

    result = runner.invoke(app, ["-w", str(kb_path), "qmd", "sync"])
    assert result.exit_code == 0, result.output
    assert "No new collections to propose" in result.output


def test_qmd_embed_command(kb_path: Path, monkeypatch):
    _init(kb_path, monkeypatch, skip_qmd_collection=True)

    result = runner.invoke(app, ["-w", str(kb_path), "qmd", "embed"])
    assert result.exit_code == 0, result.output
    assert "Embedded" in result.output


def test_qmd_embed_command_reports_failure(kb_path: Path, monkeypatch):
    _init(kb_path, monkeypatch, skip_qmd_collection=True)
    monkeypatch.setattr(
        "wakil.integrations.qmd.subprocess.run",
        lambda cmd, **kwargs: type("R", (), {"returncode": 1, "stdout": "", "stderr": "boom\n"})(),
    )

    result = runner.invoke(app, ["-w", str(kb_path), "qmd", "embed"])
    assert result.exit_code == 1
    assert "boom" in result.output


def test_init_creates_collection_and_ingest_refreshes_index(kb_path: Path, monkeypatch):
    """init auto-registers the default collection, and a subsequent ingest
    should re-scan + embed automatically (the behavior asked for here)."""
    qmd_dir = _init(kb_path, monkeypatch, skip_qmd_collection=False)
    calls = []
    real_fake_run = _fake_qmd_run(qmd_dir)

    def tracking_run(cmd, **kwargs):
        calls.append(list(cmd))
        return real_fake_run(cmd, **kwargs)

    monkeypatch.setattr("wakil.integrations.qmd.subprocess.run", tracking_run)
    monkeypatch.setattr("wakil.llm.client.resolve_client", lambda: _FakeCaptureClient())

    transcript = kb_path / "meeting.txt"
    transcript.write_text("Q: hi\nA: hi\n")
    result = runner.invoke(
        app, ["-w", str(kb_path), "ingest", "transcript", str(transcript), "--yes", "--local"]
    )
    assert result.exit_code == 0, result.output
    assert "QMD index refreshed" in result.output
    assert ["qmd", "update"] in calls
    assert ["qmd", "embed"] in calls


def test_ingest_skips_refresh_when_no_collection_registered(kb_path: Path, monkeypatch):
    _init(kb_path, monkeypatch, skip_qmd_collection=True)
    calls = []
    qmd_dir = kb_path / ".wakil" / "qmd"
    real_fake_run = _fake_qmd_run(qmd_dir)

    def tracking_run(cmd, **kwargs):
        calls.append(list(cmd))
        return real_fake_run(cmd, **kwargs)

    monkeypatch.setattr("wakil.integrations.qmd.subprocess.run", tracking_run)
    monkeypatch.setattr("wakil.llm.client.resolve_client", lambda: _FakeCaptureClient())

    transcript = kb_path / "meeting.txt"
    transcript.write_text("Q: hi\nA: hi\n")
    result = runner.invoke(
        app, ["-w", str(kb_path), "ingest", "transcript", str(transcript), "--yes", "--local"]
    )
    assert result.exit_code == 0, result.output
    assert ["qmd", "update"] not in calls
    assert ["qmd", "embed"] not in calls
