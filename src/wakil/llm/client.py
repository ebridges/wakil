"""Minimal provider-abstracted model client.

Two providers: Anthropic (via the official SDK) and any OpenAI-compatible
chat-completions endpoint (via httpx). Configuration comes from environment
variables so no credentials are ever stored in the workspace:

    ANTHROPIC_API_KEY       use Anthropic (default provider when set)
    OPENAI_API_KEY          use an OpenAI-compatible endpoint
    WAKIL_PROVIDER          force "anthropic" or "openai"
    WAKIL_MODEL             override the model id
    WAKIL_OPENAI_BASE_URL   OpenAI-compatible base URL (default api.openai.com/v1)
"""

import os
from typing import Protocol

DEFAULT_ANTHROPIC_MODEL = "claude-opus-4-8"
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MAX_TOKENS = 8192


class ModelError(RuntimeError):
    pass


class ModelTruncatedError(ModelError):
    """The provider stopped generating because it hit max_tokens.

    Distinct from a generic malformed response: the text is genuinely
    incomplete (cut off mid-token), not just badly shaped, so callers can
    retry with a larger budget instead of re-prompting for the same length.
    """

    def __init__(self, max_tokens: int, partial: str):
        self.max_tokens = max_tokens
        self.partial = partial
        super().__init__(
            f"model response was truncated: it hit the max_tokens={max_tokens} limit "
            f"before finishing ({len(partial)} chars generated). This usually means "
            "the request asked for more output than the budget allows — e.g. too "
            "many items in one batch."
        )


class ModelClient(Protocol):
    model: str

    def complete(self, system: str, prompt: str, max_tokens: int = DEFAULT_MAX_TOKENS) -> str:
        """Return the model's text response for a single-turn prompt."""
        ...


class AnthropicClient:
    def __init__(self, model: str | None = None):
        import anthropic

        self._client = anthropic.Anthropic()
        self.model = model or DEFAULT_ANTHROPIC_MODEL

    def complete(self, system: str, prompt: str, max_tokens: int = DEFAULT_MAX_TOKENS) -> str:
        response = self._client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            thinking={"type": "adaptive"},
            messages=[{"role": "user", "content": prompt}],
        )
        if response.stop_reason == "refusal":
            raise ModelError("The model declined to answer this request.")
        text = "".join(block.text for block in response.content if block.type == "text")
        if response.stop_reason == "max_tokens":
            raise ModelTruncatedError(max_tokens, text)
        return text


class OpenAICompatibleClient:
    def __init__(self, model: str, api_key: str, base_url: str = DEFAULT_OPENAI_BASE_URL):
        self.model = model
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")

    def complete(self, system: str, prompt: str, max_tokens: int = DEFAULT_MAX_TOKENS) -> str:
        import httpx

        response = httpx.post(
            f"{self._base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self._api_key}"},
            json={
                "model": self.model,
                "max_tokens": max_tokens,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
            },
            timeout=300,
        )
        if response.status_code != 200:
            raise ModelError(
                f"Model endpoint returned {response.status_code}: {response.text[:200]}"
            )
        data = response.json()
        try:
            choice = data["choices"][0]
            content = choice["message"]["content"] or ""
        except (KeyError, IndexError) as exc:
            raise ModelError(f"Unexpected response shape from model endpoint: {exc}") from exc
        if choice.get("finish_reason") == "length":
            raise ModelTruncatedError(max_tokens, content)
        return content


def resolve_client() -> ModelClient | None:
    """Build a client from the environment; None when no provider is configured."""
    provider = os.environ.get("WAKIL_PROVIDER")
    model = os.environ.get("WAKIL_MODEL")

    if provider is None:
        if os.environ.get("ANTHROPIC_API_KEY"):
            provider = "anthropic"
        elif os.environ.get("OPENAI_API_KEY"):
            provider = "openai"
        else:
            return None

    if provider == "anthropic":
        return AnthropicClient(model=model)
    if provider == "openai":
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key or not model:
            raise ModelError("OpenAI-compatible provider needs OPENAI_API_KEY and WAKIL_MODEL set.")
        base_url = os.environ.get("WAKIL_OPENAI_BASE_URL", DEFAULT_OPENAI_BASE_URL)
        return OpenAICompatibleClient(model=model, api_key=api_key, base_url=base_url)
    raise ModelError(f"Unknown WAKIL_PROVIDER: {provider}")
