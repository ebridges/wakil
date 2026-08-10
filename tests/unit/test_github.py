"""`gh` CLI wrapper. No test here ever runs a real `gh` -- every case
monkeypatches `subprocess.run` in the module under test.
"""

import subprocess
from pathlib import Path

import pytest

from wakil.integrations.github import GhError, create_pull_request, find_pull_request


class _Result:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _patch_run(monkeypatch, result, capture: list | None = None):
    def _run(args, **kwargs):
        if capture is not None:
            capture.append(args)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr("wakil.integrations.github.subprocess.run", _run)


def test_create_pull_request_passes_head_and_base_explicitly(monkeypatch):
    """Without --head, `gh` infers the head branch from whatever is checked
    out in cwd -- which is how a PR ended up opened against an unrelated
    source's branch (#180)."""
    seen: list = []
    _patch_run(monkeypatch, _Result(stdout="https://github.com/o/r/pull/7\n"), seen)

    url = create_pull_request(
        Path("/tmp/repo"), "title", "body", head="wakil/ingest/mine", base="main", draft=True
    )

    assert url == "https://github.com/o/r/pull/7"
    args = seen[0]
    assert args[:3] == ["gh", "pr", "create"]
    assert args[args.index("--head") + 1] == "wakil/ingest/mine"
    assert args[args.index("--base") + 1] == "main"
    assert "--draft" in args


def test_create_pull_request_rejects_output_with_no_url(monkeypatch):
    """An empty stdout used to yield "", which was persisted as the source's
    PR URL and then read back as "no PR yet", opening a second one."""
    _patch_run(monkeypatch, _Result(stdout="   \n"))
    with pytest.raises(GhError, match="printed no PR URL"):
        create_pull_request(Path("/tmp/repo"), "t", "b", head="h", base="main")


def test_create_pull_request_ignores_trailing_non_url_chatter(monkeypatch):
    _patch_run(
        monkeypatch,
        _Result(stdout="https://github.com/o/r/pull/7\nWarning: something\n"),
    )
    url = create_pull_request(Path("/tmp/repo"), "t", "b", head="h", base="main")
    assert url == "https://github.com/o/r/pull/7"


def test_find_pull_request_returns_the_url_for_an_exact_head_match(monkeypatch):
    seen: list = []
    _patch_run(monkeypatch, _Result(stdout='[{"url": "https://github.com/o/r/pull/9"}]'), seen)

    url = find_pull_request(Path("/tmp/repo"), "wakil/ingest/mine")

    assert url == "https://github.com/o/r/pull/9"
    args = seen[0]
    assert args[:3] == ["gh", "pr", "list"]
    assert args[args.index("--head") + 1] == "wakil/ingest/mine"
    assert args[args.index("--state") + 1] == "open"


def test_find_pull_request_returns_none_when_there_is_no_pr(monkeypatch):
    _patch_run(monkeypatch, _Result(stdout="[]"))
    assert find_pull_request(Path("/tmp/repo"), "wakil/ingest/mine") is None


def test_find_pull_request_raises_rather_than_reporting_no_pr(monkeypatch):
    """An auth failure must not be indistinguishable from "there is no PR" --
    that would send the caller straight into a duplicate `gh pr create`."""
    _patch_run(monkeypatch, _Result(returncode=4, stderr="gh auth login required"))
    with pytest.raises(GhError, match="gh pr list failed"):
        find_pull_request(Path("/tmp/repo"), "wakil/ingest/mine")


def test_find_pull_request_raises_on_unparseable_json(monkeypatch):
    _patch_run(monkeypatch, _Result(stdout="not json"))
    with pytest.raises(GhError, match="unparseable JSON"):
        find_pull_request(Path("/tmp/repo"), "wakil/ingest/mine")


def test_gh_timeout_is_reported_as_a_gh_error(monkeypatch):
    _patch_run(monkeypatch, subprocess.TimeoutExpired(cmd=["gh"], timeout=60))
    with pytest.raises(GhError, match="gh pr list failed"):
        find_pull_request(Path("/tmp/repo"), "b")
