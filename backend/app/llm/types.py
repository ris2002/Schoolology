"""Provider-neutral message and tool shapes used by all AI features.

Messages (a list, oldest first; the system prompt is passed separately):
    {"role": "user", "content": "text"}
    {"role": "user", "content": [text_part("..."), image_part(jpeg_bytes), ...]}   # with images
    {"role": "assistant", "content": "text or None", "tool_calls": [ToolCall, ...]}
    {"role": "tool", "tool_call_id": "...", "name": "...", "content": "text", "is_error": False}

Always append `response.message` (not a hand-built dict) after a model turn:
it carries provider data that some providers need back on the next call.

Tools:
    {"name": "get_overview", "description": "...", "parameters": {JSON Schema object}}
"""

from dataclasses import dataclass, field
from typing import Any, Literal


class LLMError(Exception):
    """The AI call failed. `retryable` is true for rate limits, timeouts and server errors."""

    def __init__(self, message: str, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


class LLMNotConfigured(LLMError):
    """No usable key for the chosen provider, or the key was rejected."""


class LLMRefusal(LLMError):
    """The model declined to answer."""


class LLMBadOutput(LLMError):
    """The model's output did not match what was asked for (e.g. invalid JSON)."""


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]
    # Set when the model produced arguments that are not valid JSON.
    arguments_error: str | None = None


@dataclass
class LLMResponse:
    text: str
    tool_calls: list[ToolCall]
    stop: Literal["end", "tool_use", "max_tokens"]
    message: dict  # the assistant message to append to the conversation
    provider: str
    model: str
    usage: dict = field(default_factory=dict)


def text_part(text: str) -> dict:
    return {"type": "text", "text": text}


def image_part(data: bytes, media_type: str = "image/jpeg") -> dict:
    """An image inside a user message. JPEG and PNG work with every provider."""
    import base64
    return {"type": "image", "media_type": media_type, "data": base64.b64encode(data).decode()}


def tool_result(call: ToolCall, content: str, is_error: bool = False) -> dict:
    """The message that answers one tool call."""
    return {"role": "tool", "tool_call_id": call.id, "name": call.name, "content": content, "is_error": is_error}
