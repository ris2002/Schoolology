"""A scripted stand-in for app.llm.LLM, so AI features can be tested without keys or cost."""

import json

from app.llm import LLMResponse, ToolCall


def text_reply(text: str) -> LLMResponse:
    return LLMResponse(text=text, tool_calls=[], stop="end",
                       message={"role": "assistant", "content": text, "tool_calls": []},
                       provider="fake", model="fake")


def tool_reply(*calls: tuple[str, dict], text: str = "") -> LLMResponse:
    tool_calls = [ToolCall(f"call_{i}_{name}", name, args) for i, (name, args) in enumerate(calls)]
    return LLMResponse(text=text, tool_calls=tool_calls, stop="tool_use",
                       message={"role": "assistant", "content": text or None, "tool_calls": tool_calls},
                       provider="fake", model="fake")


class FakeLLM:
    """Replies come from `script`, in order. Each entry is an LLMResponse, or a
    function (system, messages, tools, json_schema) -> LLMResponse | dict | str.
    Every call is recorded in `calls` for assertions."""

    provider, model, key_source = "fake", "fake", "app"

    def __init__(self, *script):
        self.script = list(script)
        self.calls = []

    def chat(self, system, messages, *, tools=None, json_schema=None, max_tokens=4096):
        self.calls.append({"system": system, "messages": [dict(m) for m in messages], "tools": tools,
                           "json_schema": json_schema})
        if not self.script:
            raise AssertionError("FakeLLM ran out of scripted replies")
        reply = self.script.pop(0)
        if callable(reply):
            reply = reply(system, messages, tools, json_schema)
        if isinstance(reply, (dict, list)):
            reply = text_reply(json.dumps(reply))
        if isinstance(reply, str):
            reply = text_reply(reply)
        return reply

    def json(self, system, prompt, schema, max_tokens=4096):
        return json.loads(self.chat(system, [{"role": "user", "content": prompt}], json_schema=schema).text)
