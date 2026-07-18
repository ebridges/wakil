from pathlib import Path

import pytest

from wakil.app import qmd_service
from wakil.config.settings import WorkspaceConfig


def _config(root: Path, qmd_enabled: bool = False) -> WorkspaceConfig:
    return WorkspaceConfig(name="kb", root_path=root, qmd_enabled=qmd_enabled)


def test_plan_default_collections_proposes_single_whole_kb_collection(tmp_path: Path):
    plans = qmd_service.plan_default_collections(_config(tmp_path))
    assert len(plans) == 1
    assert plans[0].name == "kb"
    assert plans[0].path == "."
    assert plans[0].pattern == "**/*.md"


def test_plan_default_collections_empty_once_any_collection_exists(tmp_path: Path):
    qmd_dir = tmp_path / ".wakil" / "qmd"
    qmd_dir.mkdir(parents=True)
    (qmd_dir / "index.yml").write_text(
        f"collections:\n  something:\n    path: {tmp_path / 'concepts'}\n    pattern: '**/*.md'\n"
    )

    assert qmd_service.plan_default_collections(_config(tmp_path)) == []


def test_ensure_default_collection_registers_when_none_exist(tmp_path: Path, monkeypatch):
    captured = {}

    def fake_qmd_add_collection(root_arg, qmd_dir, path, name=None, pattern=None):
        captured["path"] = path
        captured["name"] = name
        captured["pattern"] = pattern
        from wakil.integrations.qmd import QmdCommandResult

        return QmdCommandResult(success=True, message="Collection 'kb' created successfully")

    monkeypatch.setattr(qmd_service.qmd, "qmd_add_collection", fake_qmd_add_collection)

    result = qmd_service.ensure_default_collection(_config(tmp_path))
    assert result is not None
    assert result.success is True
    assert captured["name"] == "kb"
    assert captured["path"] == tmp_path.resolve()


def test_ensure_default_collection_noop_when_already_registered(tmp_path: Path, monkeypatch):
    qmd_dir = tmp_path / ".wakil" / "qmd"
    qmd_dir.mkdir(parents=True)
    (qmd_dir / "index.yml").write_text(
        f"collections:\n  kb:\n    path: {tmp_path}\n    pattern: '**/*.md'\n"
    )

    def fail_if_called(*args, **kwargs):
        raise AssertionError("qmd_add_collection should not be called")

    monkeypatch.setattr(qmd_service.qmd, "qmd_add_collection", fail_if_called)

    assert qmd_service.ensure_default_collection(_config(tmp_path)) is None


def test_add_collection_rejects_path_escaping_root(tmp_path: Path):
    root = tmp_path / "kb"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    with pytest.raises(qmd_service.QmdPathError):
        qmd_service.add_collection(_config(root), outside)


def test_add_collection_rejects_relative_traversal(tmp_path: Path):
    root = tmp_path / "kb"
    (root / "concepts").mkdir(parents=True)

    with pytest.raises(qmd_service.QmdPathError):
        qmd_service.add_collection(_config(root), Path("../outside"))


def test_add_collection_invokes_qmd_for_valid_path(tmp_path: Path, monkeypatch):
    root = tmp_path / "kb"
    (root / "concepts").mkdir(parents=True)
    captured = {}

    def fake_qmd_add_collection(root_arg, qmd_dir, path, name=None, pattern=None):
        captured["root"] = root_arg
        captured["qmd_dir"] = qmd_dir
        captured["path"] = path
        captured["name"] = name
        captured["pattern"] = pattern
        from wakil.integrations.qmd import QmdCommandResult

        return QmdCommandResult(success=True, message="ok")

    monkeypatch.setattr(qmd_service.qmd, "qmd_add_collection", fake_qmd_add_collection)

    result = qmd_service.add_collection(_config(root), Path("concepts"), name="concepts")
    assert result.success is True
    assert captured["path"] == (root / "concepts").resolve()
    assert captured["qmd_dir"] == _config(root).qmd_dir


def _register_collection(tmp_path: Path) -> None:
    qmd_dir = tmp_path / ".wakil" / "qmd"
    qmd_dir.mkdir(parents=True)
    (qmd_dir / "index.yml").write_text(
        f"collections:\n  kb:\n    path: {tmp_path}\n    pattern: '**/*.md'\n"
    )


def test_refresh_index_noop_when_qmd_disabled(tmp_path: Path):
    _register_collection(tmp_path)
    assert qmd_service.refresh_index(_config(tmp_path, qmd_enabled=False)) == []


def test_refresh_index_noop_when_no_collection_registered(tmp_path: Path):
    assert qmd_service.refresh_index(_config(tmp_path, qmd_enabled=True)) == []


def test_refresh_index_runs_update_then_embed(tmp_path: Path, monkeypatch):
    _register_collection(tmp_path)
    calls = []

    from wakil.integrations.qmd import QmdCommandResult

    monkeypatch.setattr(
        qmd_service.qmd,
        "qmd_update",
        lambda qmd_dir, root: calls.append("update") or QmdCommandResult(True, "updated"),
    )
    monkeypatch.setattr(
        qmd_service.qmd,
        "qmd_embed",
        lambda qmd_dir, root: calls.append("embed") or QmdCommandResult(True, "embedded"),
    )

    results = qmd_service.refresh_index(_config(tmp_path, qmd_enabled=True))
    assert calls == ["update", "embed"]
    assert [r.success for r in results] == [True, True]


def test_refresh_index_skips_embed_when_update_fails(tmp_path: Path, monkeypatch):
    _register_collection(tmp_path)

    from wakil.integrations.qmd import QmdCommandResult

    monkeypatch.setattr(
        qmd_service.qmd, "qmd_update", lambda qmd_dir, root: QmdCommandResult(False, "boom")
    )

    def fail_if_called(*args, **kwargs):
        raise AssertionError("qmd_embed should not be called if update failed")

    monkeypatch.setattr(qmd_service.qmd, "qmd_embed", fail_if_called)

    results = qmd_service.refresh_index(_config(tmp_path, qmd_enabled=True))
    assert len(results) == 1
    assert results[0].success is False
