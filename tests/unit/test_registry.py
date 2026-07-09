from pathlib import Path

from wakil.config import registry


def test_register_and_lookup(tmp_path: Path):
    registry.register_workspace("my-kb", tmp_path / "kb")
    assert registry.lookup_workspace("my-kb") == tmp_path / "kb"
    assert registry.lookup_workspace("other") is None


def test_reregister_updates_path(tmp_path: Path):
    registry.register_workspace("kb", tmp_path / "old")
    registry.register_workspace("kb", tmp_path / "new")
    assert registry.lookup_workspace("kb") == tmp_path / "new"
    assert registry.list_workspaces() == {"kb": str(tmp_path / "new")}


def test_registry_survives_corrupt_file(tmp_path: Path):
    path = registry.registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(": not [valid yaml")
    assert registry.lookup_workspace("anything") is None
    registry.register_workspace("kb", tmp_path)  # rewrites cleanly
    assert registry.lookup_workspace("kb") == tmp_path


def test_registry_path_respects_xdg(tmp_path: Path):
    assert str(registry.registry_path()).startswith(str(tmp_path / "xdg-config"))
