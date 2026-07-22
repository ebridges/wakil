"""Load judgment content from SKILL.md and build system prompts.

Skill files carry prose only — heuristics, anti-patterns, worked examples.
The JSON shape the model must return is never written into a skill file; it
is generated here from the Pydantic contract's `model_json_schema()`, so the
schema the model sees and the schema `validate_model_response` checks are
always the same object (docs/ingestion-refactor-spec.md).

Skill content is resolved through `wakil.skills.resolver` — the same
precedence chain `wakil skills list/which/validate/lint` use
(`WAKIL_SKILL_PATH` -> `<kb-root>/skills` -> user-level -> built-in). A
knowledge base can override `article`/`text`/`transcript`/`entity-resolve`
just like any other catalog skill, and that override actually takes effect
here — this is the one place their content gets read for `wakil enrich`.
"""

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import frontmatter as frontmatter_lib
from pydantic import BaseModel

from wakil.skills.errors import SkillResolutionError
from wakil.skills.resolver import default_context, resolve_skill

BASE_SYSTEM = (
    "You are wakil, a careful assistant for a personal Markdown knowledge base. "
    "When referencing an existing note anywhere in your response, cite it as a "
    "[[wikilink]] using its workspace-relative path (directory plus the note's "
    "slug, without a .md extension) — not a backticked path, a bare title, or a "
    "relative Markdown link."
)


class SkillLoadError(RuntimeError):
    pass


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    body: str


def load_skill(name: str, kb_root: Path) -> Skill:
    return _load_cached(name, str(kb_root.resolve()))


@lru_cache(maxsize=32)
def _load_cached(name: str, kb_root: str) -> Skill:
    context = default_context(Path(kb_root))
    try:
        resolved = resolve_skill(name, context)
    except SkillResolutionError as exc:
        raise SkillLoadError(str(exc)) from exc

    post = frontmatter_lib.loads(resolved.manifest.read_text(encoding="utf-8"))
    body = post.content.strip()
    if not body:
        raise SkillLoadError(f"{resolved.manifest} has no prose body")
    description = str(post.get("description") or "")
    return Skill(name=resolved.metadata.name, description=description, body=body)


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
