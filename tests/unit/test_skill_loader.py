"""Skill loading and the prompt/contract pairing (Phase C)."""

import pytest
from pydantic import ValidationError

from wakil.llm.schemas import ExtractionOutput, validate_model_response
from wakil.llm.skill_loader import SkillLoadError, build_system_prompt, load_skill

SHIPPED_SKILLS = ["transcript", "article", "text", "entity-resolve"]


@pytest.mark.parametrize("name", SHIPPED_SKILLS)
def test_shipped_skills_load(name):
    skill = load_skill(name)
    assert skill.name == name
    assert skill.description
    assert len(skill.body) > 100  # prose judgment, not a stub


def test_skill_bodies_carry_no_json_schema():
    # The JSON shape lives in code (model_json_schema), never in the prose —
    # so prompt and validator cannot drift apart.
    for name in SHIPPED_SKILLS:
        assert "{" not in load_skill(name).body


def test_unknown_skill_raises():
    with pytest.raises(SkillLoadError, match="No skill file"):
        load_skill("carrier-pigeon")


def test_system_prompt_pairs_prose_with_contract_schema():
    skill = load_skill("transcript")
    system = build_system_prompt(skill, ExtractionOutput)
    assert skill.body in system
    # The exact contract schema is injected, key properties included.
    assert '"proposed_note"' in system
    assert '"memories"' in system
    assert "no code fences" in system


def test_validate_model_response_strips_code_fences():
    fenced = '```json\n{"summary": "ok", "memories": []}\n```'
    result = validate_model_response(fenced, ExtractionOutput)
    assert result.summary == "ok"


def test_validate_model_response_rejects_bad_shapes():
    with pytest.raises(ValidationError):
        validate_model_response("not json", ExtractionOutput)
    with pytest.raises(ValidationError):
        validate_model_response('{"memories": [{"confidence": 1}]}', ExtractionOutput)
