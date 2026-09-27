"""Feature 7: the chat agent."""

from dataclasses import dataclass, field

from ..agent_tools import AgentTools
from ..llm import LLM


@dataclass
class AgentAnswer:
    text: str
    # One entry per tool call made, e.g. {"tool": "list_assignments", "arguments": {"status": "missing"}}.
    sources: list[dict] = field(default_factory=list)


def answer(llm: LLM, child_name: str, history: list[dict], question: str, tools: AgentTools) -> AgentAnswer:
    """Answer the parent's question from the child's data.

    history: earlier turns, oldest first, as {"role": "user" | "assistant", "content": "text"}.
    tools:   tools.definitions to pass to llm.chat, tools.run(name, arguments) to run one.
             tools.run raises agent_tools.ToolError for a bad call: send it back as an error result.

    Must:
    - Loop: call llm.chat with tools; run every tool call in the reply; append response.message
      and one llm.tool_result(...) per call; repeat until the model stops calling tools.
    - Cap the loop (e.g. 8 rounds) and handle stop == "max_tokens".
    - Answer only from tool results, naming the course or assignment used.
    - Say "I don't have that information" when the data does not cover the question.
    - Never calculate new numbers; quote the ones the tools return.
    """
    raise NotImplementedError
