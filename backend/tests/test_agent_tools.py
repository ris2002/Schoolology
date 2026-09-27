"""The chat agent's data tools: valid calls return JSON-ready facts, bad calls raise ToolError."""

import json

import pytest

from app.agent_tools import AgentTools, ToolError
from tests.ai.test_ai_contracts import sample_facts


@pytest.fixture
def tools():
    return AgentTools(sample_facts())


def test_every_tool_without_arguments_returns_json(tools):
    for d in tools.definitions:
        if not d["parameters"].get("required"):
            json.dumps(tools.run(d["name"], {}))


def test_filters_and_lookup(tools):
    tests = tools.run("list_assignments", {"course": "math", "kind": "test"})
    assert tests["count"] == 3
    first = tests["assignments"][0]
    assert tools.run("get_assignment", {"assignment_id": str(first["assignment_id"])})["lost_marks"]


@pytest.mark.parametrize("name, args", [
    ("no_such_tool", {}),
    ("list_assignments", {"course": "art"}),
    ("list_assignments", {"status": "lost"}),
    ("get_assignment", {"assignment_id": 999999}),
    ("get_assignment", {}),
    ("get_overview", {"unexpected": 1}),
])
def test_bad_calls_raise_tool_error(tools, name, args):
    with pytest.raises(ToolError):
        tools.run(name, args)
