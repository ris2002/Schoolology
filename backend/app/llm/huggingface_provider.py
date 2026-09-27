"""Open models through Hugging Face Inference Providers (chat completion API)."""

import json
import re

from huggingface_hub import InferenceClient
from huggingface_hub.errors import HfHubHTTPError

from .types import LLMBadOutput, LLMError, LLMNotConfigured, LLMResponse, ToolCall


def to_hf_messages(system: str, messages: list[dict]) -> list[dict]:
    out = [{"role": "system", "content": system}] if system else []
    for m in messages:
        if m["role"] == "user":
            out.append({"role": "user", "content": m["content"]})
        elif m["role"] == "assistant":
            msg = {"role": "assistant", "content": m.get("content") or ""}
            if m.get("tool_calls"):
                msg["tool_calls"] = [
                    {"id": c.id, "type": "function",
                     "function": {"name": c.name, "arguments": json.dumps(c.arguments)}}
                    for c in m["tool_calls"]
                ]
            out.append(msg)
        elif m["role"] == "tool":
            content = m["content"] if not m.get("is_error") else f"ERROR: {m['content']}"
            out.append({"role": "tool", "tool_call_id": m["tool_call_id"], "name": m.get("name"), "content": content})
        else:
            raise ValueError(f"unknown role {m['role']!r}")
    return out


def to_hf_tools(tools: list[dict]) -> list[dict]:
    return [
        {"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": t["parameters"]}}
        for t in tools
    ]


def extract_json(text: str) -> str:
    """Open models sometimes wrap JSON in ``` fences or add a sentence around it."""
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    start = min((i for i in (text.find("{"), text.find("[")) if i >= 0), default=-1)
    if start > 0:
        text = text[start:]
    return text.strip()


def _parse_arguments(raw) -> tuple[dict, str | None]:
    if isinstance(raw, dict):
        return raw, None
    try:
        value = json.loads(raw or "{}")
        return (value, None) if isinstance(value, dict) else ({}, "arguments were not a JSON object")
    except json.JSONDecodeError as e:
        return {}, f"arguments were not valid JSON: {e}"


class HuggingFaceProvider:
    name = "huggingface"

    def __init__(self, api_key: str, model: str):
        self.model = model
        self._client = InferenceClient(api_key=api_key)

    def chat(self, system: str, messages: list[dict], tools=None, json_schema=None, max_tokens=4096) -> LLMResponse:
        if json_schema:
            # Not every provider behind Hugging Face enforces response_format, so
            # the schema is also stated in the prompt and the reply is parsed leniently.
            system = (
                f"{system}\n\nRespond with only a JSON value matching this JSON Schema, no other text:\n"
                f"{json.dumps(json_schema)}"
            )
        params = {"model": self.model, "messages": to_hf_messages(system, messages), "max_tokens": max_tokens}
        if tools:
            params["tools"] = to_hf_tools(tools)
            params["tool_choice"] = "auto"
        if json_schema:
            params["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "output", "schema": json_schema, "strict": True},
            }

        try:
            resp = self._client.chat_completion(**params)
        except HfHubHTTPError as e:
            status = e.response.status_code if e.response is not None else None
            if status in (401, 403):
                raise LLMNotConfigured("The Hugging Face token was rejected or lacks inference access") from e
            if status == 404:
                raise LLMError(f"Hugging Face model {self.model!r} was not found or has no inference provider") from e
            if status == 429:
                raise LLMError("Hugging Face rate limit reached", retryable=True) from e
            raise LLMError(f"Hugging Face API error ({status})", retryable=status is None or status >= 500) from e
        except (TimeoutError, ConnectionError) as e:
            raise LLMError("Could not reach Hugging Face", retryable=True) from e

        choice = resp.choices[0]
        text = choice.message.content or ""
        calls = []
        for c in choice.message.tool_calls or []:
            args, error = _parse_arguments(c.function.arguments)
            calls.append(ToolCall(c.id, c.function.name, args, error))
        if json_schema and not calls:
            text = extract_json(text)
            try:
                json.loads(text)
            except json.JSONDecodeError as e:
                raise LLMBadOutput("The model did not return valid JSON") from e

        if calls:
            stop = "tool_use"
        elif choice.finish_reason == "length":
            stop = "max_tokens"
        else:
            stop = "end"
        usage = {}
        if resp.usage:
            usage = {"input_tokens": resp.usage.prompt_tokens, "output_tokens": resp.usage.completion_tokens}
        return LLMResponse(
            text=text,
            tool_calls=calls,
            stop=stop,
            message={"role": "assistant", "content": text or None, "tool_calls": calls},
            provider=self.name,
            model=self.model,
            usage=usage,
        )
