import pytest
from pydantic import BaseModel

from wakil.llm.client import ModelTruncatedError
from wakil.llm.schemas import ModelContractError, complete_with_contract


class _Output(BaseModel):
    value: str


class _ScriptedClient:
    """Fake ModelClient that returns a scripted sequence of responses.

    Each entry is either a JSON string to return, or an exception instance
    to raise, in place of a real provider call.
    """

    model = "fake-model"

    def __init__(self, script):
        self._script = list(script)
        self.calls: list[int] = []

    def complete(self, system: str, prompt: str, max_tokens: int = 8192) -> str:
        self.calls.append(max_tokens)
        step = self._script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


def test_complete_with_contract_succeeds_first_try():
    client = _ScriptedClient(['{"value": "ok"}'])
    result = complete_with_contract(client, "sys", "prompt", _Output)
    assert result.value == "ok"
    assert client.calls == [8192]


def test_complete_with_contract_retries_invalid_json_without_growing_budget():
    client = _ScriptedClient(["not json", '{"value": "ok"}'])
    result = complete_with_contract(client, "sys", "prompt", _Output)
    assert result.value == "ok"
    assert client.calls == [8192, 8192]


def test_complete_with_contract_retries_truncation_with_doubled_budget():
    client = _ScriptedClient(
        [ModelTruncatedError(max_tokens=8192, partial='{"value": "cut off'), '{"value": "ok"}']
    )
    result = complete_with_contract(client, "sys", "prompt", _Output)
    assert result.value == "ok"
    assert client.calls == [8192, 16384]


def test_complete_with_contract_raises_after_second_truncation():
    client = _ScriptedClient(
        [
            ModelTruncatedError(max_tokens=8192, partial='{"value": "cut off'),
            ModelTruncatedError(max_tokens=16384, partial='{"value": "still cut off'),
        ]
    )
    with pytest.raises(ModelContractError) as exc_info:
        complete_with_contract(client, "sys", "prompt", _Output)
    assert "truncated" in str(exc_info.value)
    assert "max_tokens=16384" in str(exc_info.value)


def test_complete_with_contract_raises_after_second_invalid_response():
    client = _ScriptedClient(["not json", "still not json"])
    with pytest.raises(ModelContractError):
        complete_with_contract(client, "sys", "prompt", _Output)
