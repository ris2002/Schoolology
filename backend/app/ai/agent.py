"""Feature 7: the chat agent."""

import json
from dataclasses import dataclass, field

from ..agent_tools import AgentTools, ToolError
from ..llm import LLM, tool_result
from .prompts import CHAT_AGENT

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
    conversation_history = list(history)
    conversation_history.append({"role": "user", "content": question})
    system = CHAT_AGENT + f"\nThe child's name is {child_name}."
    max_rounds = 8
    sources = []

    for turn in range(max_rounds):
        response = llm.chat(system, conversation_history, tools=tools.definitions, max_tokens=1200)
        conversation_history.append(response.message)

        if not response.tool_calls:
            text = response.text.strip()
            if response.stop == "max_tokens":
                text += " (The answer was cut short. Try a narrower question.)"
            return AgentAnswer(text=text, sources=sources)

        for call in response.tool_calls:
            if call.arguments_error:
                conversation_history.append(
                    tool_result(call, f"Arguments were not valid JSON: {call.arguments_error}", is_error=True))
                continue
            try:
                result = tools.run(call.name, call.arguments)
            except ToolError as e:
                conversation_history.append(tool_result(call, str(e), is_error=True))
                continue
            sources.append({"tool": call.name, "arguments": call.arguments})
            conversation_history.append(tool_result(call, json.dumps(result)))

    return AgentAnswer(text="I couldn't finish looking that up. Please try asking in a simpler way.", sources=sources)
