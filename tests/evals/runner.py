"""Runs and grades wakil skill eval scenarios.

Plain functions, not a class-based framework — twelve skills don't need one.
`materialize_workspace` builds the scenario's workspace on disk,
`run_scenario` puts the skill's judgment prose and that workspace in front of
a model for a free-form response, and `grade_transcript` has a second model
call judge that response against the scenario's rubric.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from wakil.llm.client import ModelClient
from wakil.llm.schemas import complete_with_contract
from wakil.llm.skill_loader import BASE_SYSTEM, Skill
from wakil.skills.evals import EvalScenario, GradeResult

# tests/evals/runner.py -> tests/evals -> tests -> repo root.
REPO_ROOT = Path(__file__).resolve().parents[2]

GRADING_SYSTEM = (
    "You are grading an AI agent's response against a rubric. For each rubric "
    "item, decide pass or fail and give a one-sentence reason."
)


def materialize_workspace(tmp_path: Path, scenario: EvalScenario) -> Path:
    """Copy the scenario's base fixture into `tmp_path`, then apply its overlay.

    `base_fixture` is resolved relative to the repo root (e.g.
    "tests/fixtures/kb"), mirroring the `kb_path` fixture in
    `tests/conftest.py`. Overlay files are written on top, creating parent
    directories as needed, and may add new files or replace fixture ones.
    """
    base = (REPO_ROOT / scenario.workspace.base_fixture).resolve()
    workspace = tmp_path / "workspace"
    shutil.copytree(base, workspace)
    for relative_path, content in scenario.workspace.overlay.items():
        target = workspace / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return workspace


def _describe_workspace(workspace: Path) -> str:
    """A listing of every file under `workspace` with its content.

    Gives the model the same information a real agent reading the workspace
    would have. Files that can't be decoded as UTF-8 text are skipped.
    """
    sections: list[str] = []
    for path in sorted(workspace.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(workspace).as_posix()
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        sections.append(f"### {relative}\n\n{content}")
    return "\n\n".join(sections)


def run_scenario(
    client: ModelClient, skill: Skill, scenario: EvalScenario, workspace: Path
) -> str:
    """Run one scenario against `client` and return the raw text response.

    No JSON contract here — this is a free-form "what would you do and why"
    response, graded separately by `grade_transcript`.
    """
    system = f"{BASE_SYSTEM}\n\n{skill.body}"
    user = (
        f"{scenario.query}\n\n"
        "The knowledge base workspace contains the following files:\n\n"
        f"{_describe_workspace(workspace)}"
    )
    return client.complete(system, user)


def grade_transcript(client: ModelClient, scenario: EvalScenario, transcript: str) -> GradeResult:
    """Grade `transcript` against the scenario's rubric with an LLM judge."""
    schema_json = json.dumps(GradeResult.model_json_schema(), indent=2)
    system = (
        f"{GRADING_SYSTEM}\n\n"
        "Respond with a single JSON object and nothing else — no code fences, no prose.\n"
        "It must conform to this JSON Schema:\n\n"
        f"{schema_json}\n"
    )
    rubric = "\n".join(f"- {item}" for item in scenario.expected_behavior)
    user = f"Rubric items:\n{rubric}\n\nAgent response:\n{transcript}"
    return complete_with_contract(client, system, user, GradeResult)
