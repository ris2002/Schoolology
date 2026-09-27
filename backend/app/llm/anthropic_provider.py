"""Claude through the official Anthropic SDK."""

import json

import anthropic

from .types import LLMBadOutput, LLMError, LLMNotConfigured, LLMRefusal, LLMResponse, ToolCall

# Models that support the server-side refusal fallback ("default" routing).
FALLBACK_MODELS = {"claude-opus-5", "claude-fable-5-1"}
FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Block types worth sending back on the next turn (thinking must accompany tool use).
REPLAY_BLOCKS = {"text", "thinking", "redacted_thinking", "tool_use"}


def _anthropic_content(content):
    if isinstance(content, str):
        return content
    blocks = []
    for part in content:
        if part["type"] == "text":
            blocks.append({"type": "text", "text": part["text"]})
        elif part["type"] == "image":
            blocks.append({"type": "image", "source": {"type": "base64", "media_type": part["media_type"],
                                                       "data": part["data"]}})
        else:
            raise ValueError(f"unknown content part {part['type']!r}")
    return blocks


def to_anthropic_messages(messages: list[dict]) -> list[dict]:
    out: list[dict] = []
    for m in messages:
        if m["role"] == "user":
            out.append({"role": "user", "content": _anthropic_content(m["content"])})
        elif m["role"] == "assistant":
            if m.get("raw") and m.get("raw_provider") == "anthropic":
                out.append({"role": "assistant", "content": m["raw"]})
                continue
            blocks = [{"type": "text", "text": m["content"]}] if m.get("content") else []
            for c in m.get("tool_calls") or []:
                blocks.append({"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments})
            out.append({"role": "assistant", "content": blocks})
        elif m["role"] == "tool":
            block = {
                "type": "tool_result",
                "tool_use_id": m["tool_call_id"],
                "content": m["content"],
                "is_error": bool(m.get("is_error")),
            }
            # All results for one assistant turn go back in a single user message.
            if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list) \
                    and all(b.get("type") == "tool_result" for b in out[-1]["content"]):
                out[-1]["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})
        else:
            raise ValueError(f"unknown role {m['role']!r}")
    return out


def to_anthropic_tools(tools: list[dict]) -> list[dict]:
    return [{"name": t["name"], "description": t["description"], "input_schema": t["parameters"]} for t in tools]


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str, model: str):
        self.model = model
        self._client = anthropic.Anthropic(api_key=api_key)

    def chat(self, system: str, messages: list[dict], tools=None, json_schema=None, max_tokens=4096) -> LLMResponse:
        params = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": to_anthropic_messages(messages),
        }
        if tools:
            params["tools"] = to_anthropic_tools(tools)
        if json_schema:
            params["output_config"] = {"format": {"type": "json_schema", "schema": json_schema}}
        if self.model in FALLBACK_MODELS:
            params["betas"] = [FALLBACK_BETA]
            params["fallbacks"] = "default"

        try:
            resp = self._client.beta.messages.create(**params)
        except anthropic.AuthenticationError as e:
            raise LLMNotConfigured("The Claude API key was rejected") from e
        except anthropic.PermissionDeniedError as e:
            raise LLMNotConfigured("The Claude API key cannot use this model") from e
        except anthropic.NotFoundError as e:
            raise LLMError(f"Claude model {self.model!r} was not found") from e
        except anthropic.BadRequestError as e:
            raise LLMError(f"Claude rejected the request: {e.message}") from e
        except anthropic.RateLimitError as e:
            raise LLMError("Claude rate limit reached", retryable=True) from e
        except anthropic.APIStatusError as e:
            raise LLMError(f"Claude API error ({e.status_code})", retryable=e.status_code >= 500) from e
        except anthropic.APIConnectionError as e:
            raise LLMError("Could not reach the Claude API", retryable=True) from e

        if resp.stop_reason == "refusal":
            category = resp.stop_details.category if resp.stop_details else None
            raise LLMRefusal(f"Claude declined to answer (category: {category})")

        text = "".join(b.text for b in resp.content if b.type == "text")
        calls = [ToolCall(b.id, b.name, dict(b.input)) for b in resp.content if b.type == "tool_use"]
        if json_schema and not calls:
            try:
                json.loads(text)
            except json.JSONDecodeError as e:
                raise LLMBadOutput("Claude did not return valid JSON") from e
        stop = {"tool_use": "tool_use", "max_tokens": "max_tokens"}.get(resp.stop_reason, "end")
        return LLMResponse(
            text=text,
            tool_calls=calls,
            stop=stop,
            message={
                "role": "assistant",
                "content": text or None,
                "tool_calls": calls,
                "raw_provider": "anthropic",
                "raw": [b.to_dict() for b in resp.content if b.type in REPLAY_BLOCKS],
            },
            provider=self.name,
            model=resp.model,
            usage={"input_tokens": resp.usage.input_tokens, "output_tokens": resp.usage.output_tokens},
        )
