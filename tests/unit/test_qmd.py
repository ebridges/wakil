import json
from pathlib import Path

from wakil.integrations import qmd


def test_parse_results_list_shape(tmp_path: Path):
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
    results = qmd.parse_qmd_results(output, tmp_path)
    assert len(results) == 1
    assert results[0].path == "concepts/graph-memory.md"
    assert results[0].score == 0.92
    assert results[0].docid == "#abc123"


def test_parse_results_dict_shape_and_qmd_uri(tmp_path: Path):
    output = json.dumps({"results": [{"path": "qmd://notes/a.md", "relevance": "0.5"}]})
    results = qmd.parse_qmd_results(output, tmp_path)
    assert len(results) == 1
    assert results[0].path == "notes/a.md"
    assert results[0].score == 0.5


def test_parse_results_tolerates_garbage(tmp_path: Path):
    assert qmd.parse_qmd_results("not json", tmp_path) == []
    assert qmd.parse_qmd_results("42", tmp_path) == []
    assert qmd.parse_qmd_results(json.dumps([{"no_path": True}, "junk"]), tmp_path) == []


def test_qmd_search_returns_empty_when_binary_missing(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(qmd.shutil, "which", lambda _: None)
    assert qmd.qmd_search(tmp_path, "anything") == []


def test_qmd_search_invokes_expected_command(tmp_path: Path, monkeypatch):
    captured = {}

    class FakeCompleted:
        returncode = 0
        stdout = json.dumps([{"file": str(tmp_path / "a.md"), "score": 1.0}])

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["cwd"] = kwargs.get("cwd")
        return FakeCompleted()

    monkeypatch.setattr(qmd.shutil, "which", lambda _: "/usr/bin/qmd")
    monkeypatch.setattr(qmd.subprocess, "run", fake_run)

    results = qmd.qmd_search(tmp_path, "claims routing", limit=5, mode="vsearch")
    assert captured["cmd"] == [
        "qmd",
        "vsearch",
        "claims routing",
        "--format",
        "json",
        "-n",
        "5",
        "--full-path",
    ]
    assert captured["cwd"] == tmp_path
    assert results[0].path == "a.md"


def test_detect_reports_project_index(tmp_path: Path):
    (tmp_path / ".qmd").mkdir()
    info = qmd.detect_qmd(tmp_path)
    assert info.project_index is True
