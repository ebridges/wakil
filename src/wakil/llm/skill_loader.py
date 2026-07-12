"""Load judgment content from skills/<name>/SKILL.md and build system prompts.

Skill files carry prose only — heuristics, anti-patterns, worked examples.
The JSON shape the model must return is never written into a skill file; it
is generated here from the Pydantic contract's `model_json_schema()`, so the
schema the model sees and the schema `validate_model_response` checks are
always the same object (docs/ingestion-refactor-spec.md).

Skills ship inside the wakil package; extension happens by forking and
editing them, not by runtime discovery from a workspace.
"""

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import frontmatter as frontmatter_lib
from pydantic import BaseModel

SKILLS_DIR = Path(__file__).parent.parent / "skills"

BASE_SYSTEM = "You are wakil, a careful assistant for a personal Markdown knowledge base."


class SkillLoadError(RuntimeError):
    pass


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    body: str


def load_skill(name: str, skills_dir: Path | None = None) -> Skill:
    return _load_cached(name, str((skills_dir or SKILLS_DIR).resolve()))


@lru_cache(maxsize=32)
def _load_cached(name: str, skills_dir: str) -> Skill:
    path = Path(skills_dir) / name / "SKILL.md"
    if not path.is_file():
        raise SkillLoadError(f"No skill file at {path}")
    post = frontmatter_lib.loads(path.read_text(encoding="utf-8"))
    body = post.content.strip()
    if not body:
        raise SkillLoadError(f"{path} has no prose body")
    return Skill(
        name=str(post.get("name") or name),
        description=str(post.get("description") or ""),
        body=body,
    )


def build_system_prompt(skill: Skill, output_model: type[BaseModel]) -> str:
    """System prompt = wakil identity + the skill's judgment prose + the contract."""
    schema_json = json.dumps(output_model.model_json_schema(), indent=2)
    return (
        f"{BASE_SYSTEM}\n\n"
        f"{skill.body}\n\n"
        "Respond with a single JSON object and nothing else — no code fences, no prose.\n"
        "It must conform to this JSON Schema:\n\n"
        f"{schema_json}\n"
    )
