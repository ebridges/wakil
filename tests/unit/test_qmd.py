import json
from pathlib import Path

from wakil.integrations import qmd


def test_parse_results_list_shape(tmp_path: Path):
    qmd_dir = tmp_path / ".wakil" / "qmd"
    output = json.dumps(
        [
            {
                "file": str(tmp_path / "concepts" / "graph-memory.md"),
                "score": 0.92,
                "snippet": "graph memory ...",
                "title": "Graph Memory",
                "docid": "#abc123",
            }
        ]
    )
    results = qmd.parse_qmd_results(output, tmp_path, qmd_dir)
    assert len(results) == 1
    assert results[0].path == "concepts/graph-memory.md"
    assert results[0].score == 0.92
    assert results[0].docid == "#abc123"


def test_parse_results_dict_shape_and_qmd_uri_without_registry(tmp_path: Path):
    """With no index.yml (collection unknown), a qmd:// URI falls back to the
    stripped name/relpath string rather than crashing."""
    qmd_dir = tmp_path / ".wakil" / "qmd"
    output = json.dumps({"results": [{"path": "qmd://notes/a.md", "relevance": "0.5"}]})
    results = qmd.parse_qmd_results(output, tmp_path, qmd_dir)
    assert len(results) == 1
    assert results[0].path == "notes/a.md"
    assert results[0].score == 0.5


def test_parse_results_resolves_qmd_uri_via_collection_registry(tmp_path: Path):
    qmd_dir = tmp_path / ".wakil" / "qmd"
    qmd_dir.mkdir(parents=True)
    (tmp_path / "concepts").mkdir()
    (qmd_dir / "index.yml").write_text(
        "collections:\n"
        "  concepts:\n"
        f"    path: {tmp_path / 'concepts'}\n"
        "    pattern: '**/*.md'\n"
    )
    output = json.dumps([{"file": "qmd://concepts/graph-memory.md", "score": 0.5}])
    results = qmd.parse_qmd_results(output, tmp_path, qmd_dir)
    assert results[0].path == "concepts/graph-memory.md"


def test_parse_results_zero_score_survives(tmp_path: Path):
    qmd_dir = tmp_path / ".wakil" / "qmd"
    output = json.dumps([{"file": str(tmp_path / "a.md"), "score": 0}])
    results = qmd.parse_qmd_results(output, tmp_path, qmd_dir)
    assert results[0].score == 0.0


def test_parse_results_tolerates_garbage(tmp_path: Path):
    qmd_dir = tmp_path / ".wakil" / "qmd"
    assert qmd.parse_qmd_results("not json", tmp_path, qmd_dir) == []
    assert qmd.parse_qmd_results("42", tmp_path, qmd_dir) == []
    assert qmd.parse_qmd_results(json.dumps([{"no_path": True}, "junk"]), tmp_path, qmd_dir) == []


def test_qmd_search_returns_empty_when_binary_missing(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(qmd.shutil, "which", lambda _: None)
    assert qmd.qmd_search(tmp_path, tmp_path / ".wakil" / "qmd", "anything") == []


def test_qmd_search_invokes_expected_command(tmp_path: Path, monkeypatch):
    captured = {}
    qmd_dir = tmp_path / ".wakil" / "qmd"

    class FakeCompleted:
        returncode = 0
        stdout = json.dumps([{"file": str(tmp_path / "a.md"), "score": 1.0}])

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["cwd"] = kwargs.get("cwd")
        captured["env"] = kwargs.get("env")
        return FakeCompleted()

    monkeypatch.setattr(qmd.shutil, "which", lambda _: "/usr/bin/qmd")
    monkeypatch.setattr(qmd.subprocess, "run", fake_run)

    results = qmd.qmd_search(tmp_path, qmd_dir, "claims routing", limit=5, mode="vsearch")
    assert captured["cmd"] == ["qmd", "vsearch", "claims routing", "--json", "-n", "5"]
    assert captured["cwd"] == tmp_path
    assert captured["env"]["QMD_CONFIG_DIR"] == str(qmd_dir)
    assert captured["env"]["INDEX_PATH"] == str(qmd_dir / "index.sqlite")
    assert results[0].path == "a.md"


def test_detect_reports_project_index(tmp_path: Path):
    qmd_dir = tmp_path / ".wakil" / "qmd"
    qmd_dir.mkdir(parents=True)
    (qmd_dir / "index.sqlite").touch()
    info = qmd.detect_qmd(tmp_path, qmd_dir=qmd_dir)
    assert info.project_index is True


def test_detect_reports_no_project_index_when_absent(tmp_path: Path):
    info = qmd.detect_qmd(tmp_path, qmd_dir=tmp_path / ".wakil" / "qmd")
    assert info.project_index is False


def test_qmd_list_collections_reads_yaml(tmp_path: Path):
    qmd_dir = tmp_path / ".wakil" / "qmd"
    qmd_dir.mkdir(parents=True)
    (qmd_dir / "index.yml").write_text(
        "collections:\n  concepts:\n    path: /kb/concepts\n    pattern: '**/*.md'\n"
    )
    collections = qmd.qmd_list_collections(qmd_dir)
    assert len(collections) == 1
    assert collections[0].name == "concepts"
    assert collections[0].path == Path("/kb/concepts")
    assert collections[0].pattern == "**/*.md"


def test_qmd_list_collections_empty_when_no_config(tmp_path: Path):
    assert qmd.qmd_list_collections(tmp_path / ".wakil" / "qmd") == []


def test_qmd_add_collection_invokes_expected_command(tmp_path: Path, monkeypatch):
    captured = {}
    qmd_dir = tmp_path / ".wakil" / "qmd"

    class FakeCompleted:
        returncode = 0
        stdout = "Collection 'concepts' created successfully\n"
        stderr = ""

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs.get("env")
        return FakeCompleted()

    monkeypatch.setattr(qmd.shutil, "which", lambda _: "/usr/bin/qmd")
    monkeypatch.setattr(qmd.subprocess, "run", fake_run)

    result = qmd.qmd_add_collection(tmp_path, qmd_dir, tmp_path / "concepts", name="concepts")
    assert result.success is True
    assert captured["cmd"] == [
        "qmd",
        "collection",
        "add",
        str(tmp_path / "concepts"),
        "--mask",
        qmd.DEFAULT_PATTERN,
        "--name",
        "concepts",
    ]
    assert captured["env"]["INDEX_PATH"] == str(qmd_dir / "index.sqlite")


def test_qmd_add_collection_reports_failure(tmp_path: Path, monkeypatch):
    class FakeCompleted:
        returncode = 1
        stdout = ""
        stderr = "Collection 'concepts' already exists.\n"

    monkeypatch.setattr(qmd.shutil, "which", lambda _: "/usr/bin/qmd")
    monkeypatch.setattr(qmd.subprocess, "run", lambda cmd, **kwargs: FakeCompleted())

    result = qmd.qmd_add_collection(tmp_path, tmp_path / ".wakil" / "qmd", tmp_path / "concepts")
    assert result.success is False
    assert "already exists" in result.message


def test_qmd_remove_collection_invokes_expected_command(tmp_path: Path, monkeypatch):
    captured = {}

    class FakeCompleted:
        returncode = 0
        stdout = "Removed collection 'concepts'\n"
        stderr = ""

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return FakeCompleted()

    monkeypatch.setattr(qmd.shutil, "which", lambda _: "/usr/bin/qmd")
    monkeypatch.setattr(qmd.subprocess, "run", fake_run)

    result = qmd.qmd_remove_collection(tmp_path / ".wakil" / "qmd", "concepts")
    assert result.success is True
    assert captured["cmd"] == ["qmd", "collection", "remove", "concepts"]


def test_qmd_update_invokes_expected_command(tmp_path: Path, monkeypatch):
    captured = {}
    qmd_dir = tmp_path / ".wakil" / "qmd"

    class FakeCompleted:
        returncode = 0
        stdout = "All collections updated.\n"
        stderr = ""

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs.get("env")
        captured["cwd"] = kwargs.get("cwd")
        return FakeCompleted()

    monkeypatch.setattr(qmd.shutil, "which", lambda _: "/usr/bin/qmd")
    monkeypatch.setattr(qmd.subprocess, "run", fake_run)

    result = qmd.qmd_update(qmd_dir, tmp_path)
    assert result.success is True
    assert captured["cmd"] == ["qmd", "update"]
    assert captured["cwd"] == tmp_path
    assert captured["env"]["INDEX_PATH"] == str(qmd_dir / "index.sqlite")


def test_qmd_update_returns_failure_when_binary_missing(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(qmd.shutil, "which", lambda _: None)
    result = qmd.qmd_update(tmp_path / ".wakil" / "qmd")
    assert result.success is False


def test_qmd_embed_invokes_expected_command(tmp_path: Path, monkeypatch):
    captured = {}
    qmd_dir = tmp_path / ".wakil" / "qmd"

    class FakeCompleted:
        returncode = 0
        stdout = "Embedded 4 chunks from 2 documents\n"
        stderr = ""

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return FakeCompleted()

    monkeypatch.setattr(qmd.shutil, "which", lambda _: "/usr/bin/qmd")
    monkeypatch.setattr(qmd.subprocess, "run", fake_run)

    result = qmd.qmd_embed(qmd_dir, tmp_path)
    assert result.success is True
    assert captured["cmd"] == ["qmd", "embed"]
    assert captured["kwargs"]["timeout"] > 300  # generous: first run may download the model
    # Deliberately not captured, so qmd's own progress bar streams live to the
    # terminal instead of being buffered until the process exits.
    assert "capture_output" not in captured["kwargs"]
    assert "stdout" not in captured["kwargs"]
    assert "stderr" not in captured["kwargs"]


def test_qmd_embed_tolerates_uncaptured_output(tmp_path: Path, monkeypatch):
    """When output truly isn't captured, subprocess.run returns None for
    stdout/stderr — qmd_embed must not crash extracting a message from that."""

    class FakeCompleted:
        returncode = 0
        stdout = None
        stderr = None

    monkeypatch.setattr(qmd.shutil, "which", lambda _: "/usr/bin/qmd")
    monkeypatch.setattr(qmd.subprocess, "run", lambda cmd, **kwargs: FakeCompleted())

    result = qmd.qmd_embed(tmp_path / ".wakil" / "qmd", tmp_path)
    assert result.success is True
    assert result.message == ""


def test_qmd_embed_returns_failure_when_binary_missing(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(qmd.shutil, "which", lambda _: None)
    result = qmd.qmd_embed(tmp_path / ".wakil" / "qmd")
    assert result.success is False
