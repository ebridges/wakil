"""Eval scenario schema for wakil's built-in skill catalog.

Each built-in skill may ship an `eval.json` alongside its `SKILL.md`: a small
set of scenarios — a query, a workspace to materialize, and a rubric of
expected behaviors — used to grade the skill's judgment prose against a live
model. This module is the schema and file-loading only; running scenarios
and grading transcripts lives in `tests/evals/runner.py`, and the scenario
content itself is authored per skill in a later phase.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError, field_validator

MIN_SCENARIOS = 3


class EvalLoadError(RuntimeError):
    """`eval.json` is missing, not valid JSON, or fails schema validation."""


class EvalWorkspace(BaseModel):
    """Where a scenario's workspace comes from: a base fixture plus an overlay."""

    base_fixture: str = Field(
        description="Repo-relative path to a fixture KB, e.g. 'tests/fixtures/kb'."
    )
    overlay: dict[str, str] = Field(
        default_factory=dict,
        description="Workspace-relative path -> file content, written on top of base_fixture.",
    )


class EvalScenario(BaseModel):
    """One graded interaction: a query against a materialized workspace, judged by a rubric."""

    id: str
    query: str
    workspace: EvalWorkspace
    expected_behavior: list[str] = Field(description="Rubric items, graded pass/fail.")
    related_skills: list[str] = Field(default_factory=list)


class EvalFile(BaseModel):
    """The parsed, validated `eval.json` for one skill."""

    skill: str
    scenarios: list[EvalScenario]

    @field_validator("scenarios")
    @classmethod
    def _require_min_scenarios(cls, value: list[EvalScenario]) -> list[EvalScenario]:
        if len(value) < MIN_SCENARIOS:
            raise ValueError(
                f"eval.json must define at least {MIN_SCENARIOS} scenarios, got {len(value)}."
            )
        return value


def load_eval_file(skill_dir: Path) -> EvalFile:
    """Read and validate `<skill_dir>/eval.json`."""
    path = skill_dir / "eval.json"
    if not path.is_file():
        raise EvalLoadError(f"No eval.json at {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise EvalLoadError(f"{path} is not valid JSON: {exc}") from exc
    try:
        return EvalFile.model_validate(raw)
    except ValidationError as exc:
        raise EvalLoadError(f"{path} failed validation: {exc}") from exc


class GradeItem(BaseModel):
    """One rubric item's pass/fail verdict."""

    item: str
    passed: bool
    reason: str


class GradeResult(BaseModel):
    """A grader's verdicts across every rubric item for one scenario."""

    items: list[GradeItem]
