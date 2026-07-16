"""Local registry of known workspaces, so the CLI can address them by name.

Stored at ~/.config/wakil/workspaces.yaml (XDG_CONFIG_HOME aware) as a flat
name -> root_path mapping. `wakil init` registers the workspace; `-w <name>`
resolves through it. The registry is a convenience index only — each
workspace stays fully self-contained in its own `.wakil/` directory.
"""

import os
from pathlib import Path

import yaml

REGISTRY_FILENAME = "workspaces.yaml"


def config_home() -> Path:
    config_home_env = os.environ.get("XDG_CONFIG_HOME")
    base = Path(config_home_env) if config_home_env else Path.home() / ".config"
    return base / "wakil"


def registry_path() -> Path:
    return config_home() / REGISTRY_FILENAME


def _load() -> dict[str, str]:
    path = registry_path()
    if not path.is_file():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def register_workspace(name: str, root: Path) -> None:
    data = _load()
    data[name] = str(root)
    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=True), encoding="utf-8")


def lookup_workspace(name: str) -> Path | None:
    value = _load().get(name)
    return Path(value) if value else None


def list_workspaces() -> dict[str, str]:
    return _load()
