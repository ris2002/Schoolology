"""Provider layer: message conversion and output parsing, no network calls."""

from types import SimpleNamespace

import pytest

from app.llm import ToolCall, tool_result
from app.llm.anthropic_provider import to_anthropic_messages, to_anthropic_tools
from app.llm.huggingface_provider import HuggingFaceProvider, extract_json, to_hf_messages, to_hf_tools
from app.llm.types import LLMBadOutput

CALL_A = ToolCall("call_a", "get_overview", {})
CALL_B = ToolCall("call_b", "list_assignments", {"status": "missing"})
CONVERSATION = [
    {"role": "user", "content": "What is missing?"},
    {"role": "assistant", "content": "Let me check.", "tool_calls": [CALL_A, CALL_B]},
    tool_result(CALL_A, '{"courses": []}'),
    tool_result(CALL_B, "no access", is_error=True),
]
TOOLS = [{"name": "get_overview", "description": "Overview", "parameters": {"type": "object", "properties": {}}}]


def test_anthropic_groups_tool_results_into_one_user_message():
    out = to_anthropic_messages(CONVERSATION)
    assert [m["role"] for m in out] == ["user", "assistant", "user"]
    assert [b["type"] for b in out[1]["content"]] == ["text", "tool_use", "tool_use"]
    results = out[2]["content"]
    assert [r["tool_use_id"] for r in results] == ["call_a", "call_b"]
    assert results[1]["is_error"] is True


def test_anthropic_replays_raw_blocks_so_thinking_is_kept():
    raw = [{"type": "thinking", "thinking": "", "signature": "sig"}, {"type": "tool_use", "id": "call_a",
           "name": "get_overview", "input": {}}]
    msg = {"role": "assistant", "content": None, "tool_calls": [CALL_A], "raw": raw, "raw_provider": "anthropic"}
    assert to_anthropic_messages([{"role": "user", "content": "hi"}, msg])[1]["content"] == raw


def test_raw_blocks_from_another_provider_are_not_replayed():
    msg = {"role": "assistant", "content": "ok", "tool_calls": [], "raw": ["x"], "raw_provider": "other"}
    assert to_anthropic_messages([{"role": "user", "content": "hi"}, msg])[1]["content"] == [
        {"type": "text", "text": "ok"}
    ]


def test_hugging_face_uses_openai_style_messages():
    out = to_hf_messages("Be brief.", CONVERSATION)
    assert out[0] == {"role": "system", "content": "Be brief."}
    assert out[2]["tool_calls"][1]["function"] == {"name": "list_assignments", "arguments": '{"status": "missing"}'}
    assert out[3]["role"] == "tool" and out[3]["tool_call_id"] == "call_a"
    assert out[4]["content"].startswith("ERROR:")


def test_tool_definitions_convert_for_both_providers():
    assert to_anthropic_tools(TOOLS)[0]["input_schema"] == TOOLS[0]["parameters"]
    assert to_hf_tools(TOOLS)[0]["function"]["parameters"] == TOOLS[0]["parameters"]


@pytest.mark.parametrize("text", ['{"a": 1}', '```json\n{"a": 1}\n```', 'Here you go: {"a": 1}'])
def test_extract_json_handles_common_open_model_wrapping(text):
    assert extract_json(text) == '{"a": 1}'


def _hf_reply(content=None, tool_calls=None, finish="stop"):
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason=finish)], usage=None)


def test_hugging_face_parses_tool_calls_and_flags_bad_arguments(monkeypatch):
    provider = HuggingFaceProvider("hf_test", "some/model")
    calls = [
        SimpleNamespace(id="1", function=SimpleNamespace(name="get_overview", arguments="{}")),
        SimpleNamespace(id="2", function=SimpleNamespace(name="list_assignments", arguments="{not json")),
    ]
    monkeypatch.setattr(provider._client, "chat_completion", lambda **kw: _hf_reply(tool_calls=calls))
    r = provider.chat("sys", [{"role": "user", "content": "hi"}], tools=TOOLS)
    assert r.stop == "tool_use"
    assert r.tool_calls[0].arguments == {} and r.tool_calls[0].arguments_error is None
    assert r.tool_calls[1].arguments_error


def test_hugging_face_json_mode_rejects_non_json(monkeypatch):
    provider = HuggingFaceProvider("hf_test", "some/model")
    monkeypatch.setattr(provider._client, "chat_completion", lambda **kw: _hf_reply(content="Sorry, no."))
    with pytest.raises(LLMBadOutput):
        provider.chat("sys", [{"role": "user", "content": "hi"}], json_schema={"type": "object"})
