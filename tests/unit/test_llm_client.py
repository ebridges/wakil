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
    assert llm_client.resolve_client().model == "claude-haiku-4-5"


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
