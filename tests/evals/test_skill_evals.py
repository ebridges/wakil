"""Grades every built-in skill's SKILL.md judgment prose against its eval.json.

Skill names are discovered live via `discover_skill_names` — never
hardcoded — so a 13th skill or a renamed one is picked up automatically.
Skills without an `eval.json` yet are skipped with a clear reason rather
than failed: this phase builds the eval data model and runner only, the
scenario content for each of the 12 skills ships in a later phase.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from wakil.llm.client import resolve_client
from wakil.llm.skill_loader import load_skill
from wakil.skills.evals import load_eval_file
from wakil.skills.models import ResolutionContext
from wakil.skills.resolver import discover_skill_names

from .runner import grade_transcript, materialize_workspace, run_scenario

BUILTIN_ROOT = Path(__file__).resolve().parents[2] / "src" / "wakil" / "skills" / "builtin"

TRANSCRIPT_EXCERPT_CHARS = 1000


def _builtin_context() -> ResolutionContext:
    """A context that resolves only against the real, shipped builtin catalog."""
    return ResolutionContext(
        kb_root=BUILTIN_ROOT / ".no-such-kb",
        user_skill_root=BUILTIN_ROOT / ".no-such-user-root",
        builtin_skill_root=BUILTIN_ROOT,
    )


BUILTIN_NAMES = discover_skill_names(_builtin_context())


def _collect_cases() -> list:
    """One pytest.param per (skill, scenario), skipped up front if no eval.json."""
    cases = []
    for name in BUILTIN_NAMES:
        eval_path = BUILTIN_ROOT / name / "eval.json"
        if not eval_path.is_file():
            cases.append(
                pytest.param(
                    name,
                    None,
                    id=f"{name}-no-eval-json",
                    marks=pytest.mark.skip(
                        reason=f"{name} has no eval.json yet (scenario content ships later)."
                    ),
                )
            )
            continue
        eval_file = load_eval_file(BUILTIN_ROOT / name)
        for scenario in eval_file.scenarios:
            cases.append(pytest.param(name, scenario.id, id=f"{name}-{scenario.id}"))
    return cases


@pytest.mark.eval
@pytest.mark.parametrize("skill_name,scenario_id", _collect_cases())
def test_skill_eval_scenario(skill_name: str, scenario_id: str | None, tmp_path: Path):
    client = resolve_client()
    if client is None:
        pytest.skip("no model provider configured")

    eval_file = load_eval_file(BUILTIN_ROOT / skill_name)
    scenario = next(s for s in eval_file.scenarios if s.id == scenario_id)

    skill = load_skill(skill_name, skills_dir=BUILTIN_ROOT)
    workspace = materialize_workspace(tmp_path, scenario)
    transcript = run_scenario(client, skill, scenario, workspace)
    result = grade_transcript(client, scenario, transcript)

    failed = [item for item in result.items if not item.passed]
    if failed:
        reasons = "\n".join(f"- {item.item}: {item.reason}" for item in failed)
        excerpt = transcript[:TRANSCRIPT_EXCERPT_CHARS]
        pytest.fail(
            f"{skill_name}/{scenario_id} failed {len(failed)}/{len(result.items)} rubric "
            f"item(s):\n{reasons}\n\nTranscript excerpt:\n{excerpt}"
        )
