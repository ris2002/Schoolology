"""One interface over Hugging Face and Claude. AI features call only this.

    llm = llm_for_user(conn, user)            # provider, model and key chosen for you
    r = llm.chat(system, messages, tools=..., json_schema=..., max_tokens=...)
    data = llm.json(system, prompt, schema)   # one-shot structured output, parsed

Which provider and model: the admin panel. Which key: the parent's own key if
the admin has BYOK on for that provider and the parent saved one, otherwise
the app's key from .env.
"""

import json
from dataclasses import dataclass

from .. import config, crypto, settings
from ..db import one
from .types import (
    LLMBadOutput,
    LLMError,
    LLMNotConfigured,
    LLMRefusal,
    LLMResponse,
    ToolCall,
    tool_result,
)

APP_KEYS = {"huggingface": lambda: config.HF_TOKEN, "anthropic": lambda: config.ANTHROPIC_API_KEY}


@dataclass
class LLM:
    provider: str
    model: str
    key_source: str  # "app" or "byok"
    _impl: object

    def chat(
        self,
        system: str,
        messages: list[dict],
        *,
        tools: list[dict] | None = None,
        json_schema: dict | None = None,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """One model turn. Loops (e.g. an agent calling tools) are up to the caller."""
        return self._impl.chat(system, messages, tools=tools, json_schema=json_schema, max_tokens=max_tokens)

    def json(self, system: str, prompt: str, schema: dict, max_tokens: int = 4096):
        """Ask once for output matching `schema` and return it parsed."""
        r = self.chat(system, [{"role": "user", "content": prompt}], json_schema=schema, max_tokens=max_tokens)
        if r.stop == "max_tokens":
            raise LLMBadOutput("The reply was cut off before the JSON was complete")
        try:
            return json.loads(r.text)
        except json.JSONDecodeError as e:
            raise LLMBadOutput("The model did not return valid JSON") from e


def build(provider: str, model: str, api_key: str, key_source: str = "app") -> LLM:
    if provider == "anthropic":
        from .anthropic_provider import AnthropicProvider
        impl = AnthropicProvider(api_key, model)
    elif provider == "huggingface":
        from .huggingface_provider import HuggingFaceProvider
        impl = HuggingFaceProvider(api_key, model)
    else:
        raise LLMNotConfigured(f"Unknown AI provider {provider!r}")
    return LLM(provider, model, key_source, impl)


def resolve_key(conn, user: dict | None, provider: str, ai_settings: dict) -> tuple[str, str]:
    """(api_key, source). The parent's key wins only while BYOK is on for the provider."""
    if user and ai_settings["byok"].get(provider):
        row = one(conn, "select key_enc from user_ai_keys where user_id = %s and provider = %s",
                  (user["id"], provider))
        if row:
            return crypto.decrypt(row["key_enc"]), "byok"
    key = APP_KEYS[provider]()
    if not key:
        raise LLMNotConfigured(f"No API key is configured for {provider}")
    return key, "app"


def llm_for_user(conn, user: dict | None) -> LLM:
    """The model to use for this parent's requests (user=None for app-level jobs)."""
    ai = settings.get(conn)
    provider = ai["provider"]
    key, source = resolve_key(conn, user, provider, ai)
    return build(provider, ai["models"][provider], key, source)


__all__ = [
    "LLM", "LLMBadOutput", "LLMError", "LLMNotConfigured", "LLMRefusal", "LLMResponse", "ToolCall",
    "build", "llm_for_user", "resolve_key", "tool_result",
]
