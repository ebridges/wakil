from types import SimpleNamespace

import pytest

from wakil.llm import client as llm_client


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in (
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "WAKIL_PROVIDER",
        "WAKIL_MODEL",
        "WAKIL_OPENAI_BASE_URL",
    ):
        monkeypatch.delenv(var, raising=False)


def test_resolve_client_returns_none_without_config():
    assert llm_client.resolve_client() is None


def test_resolve_client_prefers_anthropic(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    client = llm_client.resolve_client()
    assert isinstance(client, llm_client.AnthropicClient)
    assert client.model == llm_client.DEFAULT_ANTHROPIC_MODEL


def test_resolve_client_model_override(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("WAKIL_MODEL", "claude-haiku-4-5")
    client = llm_client.resolve_client()
    assert client is not None
    assert client.model == "claude-haiku-4-5"


def test_resolve_client_openai_requires_model(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    with pytest.raises(llm_client.ModelError):
        llm_client.resolve_client()


def test_resolve_client_openai(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("WAKIL_MODEL", "local-model")
    monkeypatch.setenv("WAKIL_OPENAI_BASE_URL", "http://localhost:8080/v1/")
    client = llm_client.resolve_client()
    assert isinstance(client, llm_client.OpenAICompatibleClient)
    assert client.model == "local-model"
    assert client._base_url == "http://localhost:8080/v1"


def test_resolve_client_unknown_provider(monkeypatch):
    monkeypatch.setenv("WAKIL_PROVIDER", "carrier-pigeon")
    with pytest.raises(llm_client.ModelError):
        llm_client.resolve_client()


def test_anthropic_complete_raises_truncated_on_max_tokens(monkeypatch):
    client = llm_client.AnthropicClient.__new__(llm_client.AnthropicClient)
    client.model = "test-model"
    fake_response = SimpleNamespace(
        stop_reason="max_tokens",
        content=[SimpleNamespace(type="text", text='{"partial": "cut off')],
    )
    client._client = SimpleNamespace(  # ty: ignore[invalid-assignment]  # fake client double, not the real SDK type
        messages=SimpleNamespace(create=lambda **kwargs: fake_response)
    )
    with pytest.raises(llm_client.ModelTruncatedError) as exc_info:
        client.complete("sys", "prompt", max_tokens=123)
    assert exc_info.value.max_tokens == 123
    assert exc_info.value.partial == '{"partial": "cut off'


def test_anthropic_complete_returns_text_when_not_truncated(monkeypatch):
    client = llm_client.AnthropicClient.__new__(llm_client.AnthropicClient)
    client.model = "test-model"
    fake_response = SimpleNamespace(
        stop_reason="end_turn",
        content=[SimpleNamespace(type="text", text='{"ok": true}')],
    )
    client._client = SimpleNamespace(  # ty: ignore[invalid-assignment]  # fake client double, not the real SDK type
        messages=SimpleNamespace(create=lambda **kwargs: fake_response)
    )
    assert client.complete("sys", "prompt") == '{"ok": true}'


def test_openai_compatible_complete_raises_truncated_on_length_finish(monkeypatch):
    client = llm_client.OpenAICompatibleClient(model="m", api_key="k")

    class FakeResponse:
        status_code = 200

        def json(self):
            return {
                "choices": [
                    {
                        "message": {"content": '{"partial": "cut off'},
                        "finish_reason": "length",
                    }
                ]
            }

    monkeypatch.setattr("httpx.post", lambda *a, **kw: FakeResponse())
    with pytest.raises(llm_client.ModelTruncatedError) as exc_info:
        client.complete("sys", "prompt", max_tokens=456)
    assert exc_info.value.max_tokens == 456
