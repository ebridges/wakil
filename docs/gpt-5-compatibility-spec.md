---
title: gpt-5 compatibility for the OpenAI-compatible client
status: proposal
audience: wakil coding agent
---

# gpt-5 compatibility for the OpenAI-compatible client

## About

`WAKIL_MODEL=gpt-5` fails every call. This brief carries the diagnosis and the
measurements behind it so the fix can be implemented without a live OpenAI key
— every number and error string below was gathered against a real key on
2026-08-17 and is quoted verbatim. Tracking issue: **#245**.

Two changes, deliberately kept separate:

1. **The fix** — rename one wire parameter. This is the whole bug.
2. **`WAKIL_REASONING_EFFORT`** — a new optional env var. Not required to make
   gpt-5 work; it makes it cheaper and removes a wasted round trip.

## The bug

`OpenAICompatibleClient.complete` hardcodes `"max_tokens"` in the
`/chat/completions` body at `src/wakil/llm/client.py:134`. The gpt-5 family
removed that parameter:

```
Model endpoint returned 400: {"error":{"message":"Unsupported parameter:
'max_tokens' is not supported with this model. Use 'max_completion_tokens'
instead.", ...}}
```

Reproduce through wakil's own client, so it isn't a curl artifact:

```python
import os
from wakil.llm.client import OpenAICompatibleClient

c = OpenAICompatibleClient(model="gpt-5", api_key=os.environ["OPENAI_API_KEY"])
c.complete("Reply with exactly: ok", "ready?", max_tokens=16)
# ModelError: Model endpoint returned 400: ... Use 'max_completion_tokens' instead.
```

### Measured compatibility

Same trivial prompt for every cell, against `https://api.openai.com/v1`:

| Model | `max_tokens` | `max_completion_tokens` |
|---|---|---|
| `gpt-4.1` | 200 | 200 |
| `gpt-4o` | 200 | 200 |
| `gpt-5` | **400** | 200 |

`gpt-4.1-mini` also returns 200 with `max_tokens` (not tested with the new
name). Recorded so it doesn't get proposed as a workaround:
**`gpt-5-chat-latest` returns 404, "has been deprecated"** — it is not a
substitute for `gpt-5`.

## Change 1 — rename the wire parameter

In `OpenAICompatibleClient.complete`, `src/wakil/llm/client.py`:

```python
json={
    "model": self.model,
    "max_completion_tokens": max_tokens,   # was: "max_tokens"
    "messages": [...],
}
```

**Unconditionally — do not branch on `self.model` or `self._base_url`.** Every
model measured above accepts the new name, so a version check would be dead
weight that needs revisiting as the model list turns over.

Only the *wire key* changes. Leave alone:

- the `max_tokens` parameter name on `complete()` — it is wakil's own vocabulary
  and is shared with `AnthropicClient.complete`;
- `ModelTruncatedError(max_tokens, content)`;
- `complete_with_contract`'s `max_tokens *= 2` retry.

### The one untested claim

`WAKIL_OPENAI_BASE_URL` means this client also points at self-hosted
OpenAI-compatible servers (llama.cpp, vLLM, LM Studio, OpenRouter) — and
`tests/unit/test_llm_client.py::test_resolve_client_openai` exercises a
`localhost:8080` base URL. **Whether older builds of those servers accept
`max_completion_tokens` was not verified** — no such server was available.

The decision was made anyway, on the repo's "keep it simple / no speculative
abstraction" directive: if a self-hosted endpoint rejects the new name, that
arrives as a bug report with an already-known fix (branch on `base_url`), which
is better than guessing a compatibility rule now and maintaining it forever.
If you do end up needing the branch:

```python
budget_key = (
    "max_completion_tokens"
    if self._base_url == DEFAULT_OPENAI_BASE_URL.rstrip("/")
    else "max_tokens"
)
```

## Change 2 — `WAKIL_REASONING_EFFORT`

### Why

gpt-5 is a reasoning model, and reasoning tokens are billed against
`max_completion_tokens`. With a small budget they consume all of it and leave no
content:

| Call | `finish_reason` | `reasoning_tokens` | content |
|---|---|---|---|
| `gpt-5`, `max_completion_tokens=16` | `length` | 16 | *empty* |
| `gpt-5`, `max_completion_tokens=2000` | `stop` | 128 | `ok` |

**This is not a correctness bug, and the rename alone is enough to make gpt-5
work.** `complete_with_contract` (`src/wakil/llm/schemas.py:274-289`) already
catches `ModelTruncatedError` and retries once at double the budget, so starting
from `DEFAULT_MAX_TOKENS = 8192` the reasoning overhead is comfortably absorbed.

What follows from it is worth documenting, though: **on gpt-5,
`DEFAULT_MAX_TOKENS` is a combined reasoning+output budget, not an output
budget.** A structured response that fits in 8192 output tokens on gpt-4.1 may
truncate on gpt-5 and cost a second call to recover. And there is only one
retry — a large enrichment payload plus heavy reasoning could exhaust both
attempts and surface as `ModelContractError(truncated=True)`, which mentions
nothing about reasoning and would be a confusing report to receive.

`reasoning_effort` drives reasoning tokens to zero, which removes both the cost
and the retry risk.

### Implementation

- `resolve_client()` reads `WAKIL_REASONING_EFFORT` and passes it to
  `OpenAICompatibleClient.__init__` as an optional keyword; store it on the
  instance.
- `complete` adds `"reasoning_effort"` to the JSON body **only when it is set**.
- Scope it to the OpenAI client. The Anthropic path has a different
  thinking-budget API and is out of scope here.

### The gating is mandatory, not stylistic

Sending it unconditionally breaks the models that work today. Measured:

```
gpt-4.1 + reasoning_effort=low -> 400 Unrecognized request argument supplied: reasoning_effort
gpt-4o  + reasoning_effort=low -> 400 Unrecognized request argument supplied: reasoning_effort
```

### Pass the value through unvalidated

All four documented values are accepted by gpt-5, and reasoning scales with the
task rather than sitting at a floor per level:

| `reasoning_effort` | result | `reasoning_tokens` |
|---|---|---|
| `minimal` | 200 | 0 |
| `low` | 200 | 0 |
| `medium` | 200 | 0 |
| `high` | 200 | 64 |
| `bogus` | **400** | — |

An invalid value already returns a clear, actionable message —
`Unsupported value: 'reasoning_effort' does not support 'bogus' with this model.
Supported values are…` — so local validation would only duplicate the
provider's own error with a staler list of valid values.

## Tests

`tests/unit/test_llm_client.py` already has the pattern to extend:
`monkeypatch.setattr("httpx.post", …)` returning a `FakeResponse`, as in
`test_openai_compatible_complete_raises_truncated_on_length_finish`. Capture the
posted body (the `json=` kwarg) and assert:

- the body carries `max_completion_tokens`, and **no** `max_tokens` key — assert
  the absence, not just the presence, or the regression this fixes can come back
  as a duplicated key;
- `reasoning_effort` is absent from the body by default;
- `reasoning_effort` is present when the client was constructed with it;
- `resolve_client()` threads `WAKIL_REASONING_EFFORT` through onto the client.

Add `WAKIL_REASONING_EFFORT` to the `clean_env` autouse fixture's tuple
(`tests/unit/test_llm_client.py:10-17`) so a real value in the developer's
environment can't leak into the suite.

## Docs to update

Per working-agreement item 8:

- **`.env.example`** — add a commented `#WAKIL_REASONING_EFFORT=` under the
  OpenAI-compatible block, noting it applies to reasoning models only and is
  rejected by `gpt-4.1`/`gpt-4o`.
- **`README.md:559-564`** — the provider bullet list under `wakil query`. Add
  the new variable, and note that gpt-5 needs nothing beyond
  `WAKIL_MODEL=gpt-5` once this lands.
- **`docs/TROUBLESHOOTING.md`** — one dated, append-only entry in the file's
  existing format, citing #245. This clears the bar: it cost real debugging
  time, was invisible from reading the code, and will recur as models turn over.
  The entry's value is mostly the *second* half — that reasoning tokens share
  the output budget — since the 400 itself is self-explanatory once seen.
- **`docs/DEVELOPMENT.md`** — **no entry.** A provider-parameter rename is not a
  pattern that generalizes to future, different work. Adding one would violate
  the `development-docs` skill's explicit "default: no entry" bar.

## Verification

```bash
uv run pytest tests/unit/test_llm_client.py
uv run pytest          # full suite
uv run ruff check
uv run ty check
```

Then a live check, which needs `OPENAI_API_KEY`:

```bash
WAKIL_PROVIDER=openai WAKIL_MODEL=gpt-5 uv run python -c "
from wakil.llm.client import resolve_client
print(resolve_client().complete('Reply with exactly: ok', 'ready?', max_tokens=2000))
"
```

Expect `ok`. Then:

- repeat with `WAKIL_MODEL=gpt-4.1` — confirms the rename didn't regress the
  models that already worked;
- repeat with `WAKIL_REASONING_EFFORT=minimal` against `gpt-5` — confirms the
  new key is accepted;
- repeat with `WAKIL_REASONING_EFFORT=minimal` against `gpt-4.1` — should 400,
  confirming the gating is the user's own choice to make and not silently
  swallowed.

Note `max_tokens=2000` in the snippet above rather than a small value: at 16,
gpt-5 spends the entire budget on reasoning and raises `ModelTruncatedError`
with empty content, which looks like a failure of this change and isn't.

Also note that **`uv run` does not auto-load `.env`** without `--env-file` —
already a recorded entry in `docs/TROUBLESHOOTING.md`, and it will otherwise
cost time on exactly the commands above.

## Commits

Gitmoji Conventional Commits per `CLAUDE.md`. Two changesets, not one:

- `🐛 fix(llm): send max_completion_tokens so the gpt-5 family works`
- `✨ feat(llm): add WAKIL_REASONING_EFFORT for reasoning models`

`CHANGELOG.md` is generated by `git-cliff` — don't hand-edit it.
