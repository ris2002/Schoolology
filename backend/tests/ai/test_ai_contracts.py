"""Contract tests for the AI features in app/ai/. They fail until each feature is written.

Run just these with:  pytest -m ai
The fake model cannot know your JSON schema, so the topic and alert tests
answer from the schema-free parts of the contract; add your own tests next to
these for your prompts and schemas.
"""

import re

import pytest

from app import analytics, llm
from app.agent_tools import AgentTools
from app.ai import advice, agent, alerts, prompts, summary, topics

from .fake_llm import FakeLLM, text_reply, tool_reply
from ..test_analytics import NOW, ChildData, assignment, category, course, lost

pytestmark = pytest.mark.ai


def numbers(text: str) -> set[str]:
    return {n.rstrip(".").rstrip("0").rstrip(".") if "." in n else n for n in re.findall(r"\d+(?:\.\d+)?", text)}


def unsupported_numbers(text: str, facts) -> set[str]:
    """Numbers in the AI's text that do not appear anywhere in the facts it was given."""
    return numbers(text) - numbers(str(analytics.jsonable(facts)))


def sample_facts():
    c = course("Math 7")
    hw, test = category(c["id"], "homework", 40), category(c["id"], "test", 60)
    a1, a2, a3 = (assignment(c["id"], test["id"], 60, days_ago=d, title=f"Quiz {i}") for i, d in enumerate((20, 10, 2)))
    data = ChildData([c], [hw, test], [assignment(c["id"], hw["id"], 95), a1, a2, a3],
                     [lost(a["id"], "Dividing fractions") for a in (a1, a2, a3)])
    return analytics.compute(data, NOW)


def test_prompts_are_written():
    for name in ("TOPIC_LABELLER", "WEEKLY_SUMMARY", "ALERT_EXPLAINER", "CAUSE_EXPLAINER", "GAP_EXPLAINER", "CHAT_AGENT"):
        assert getattr(prompts, name).strip(), f"prompts.{name} is empty"


def test_topic_labels_cover_exactly_the_marks_given():
    marks = [{"id": 7, "course": "Math 7", "assignment": "Quiz 3", "granularity": "question",
              "question": "3/4 ÷ 1/2", "student_answer": "3/8", "teacher_comment": None,
              "points_lost": 15, "points_possible": 15},
             {"id": 9, "course": "Math 7", "assignment": "Quiz 3", "granularity": "question",
              "question": "12:18 simplest form", "student_answer": "6:9", "teacher_comment": None,
              "points_lost": 5, "points_possible": 15}]

    def reply(system, messages, tools, schema):
        assert schema is not None, "use structured output (a JSON schema)"
        # Put both ids and a topic in whatever shape the schema asks for is up to you;
        # this fake answers with a common shape. Adjust if your schema differs.
        return {"labels": [{"id": 7, "topic": "Dividing fractions"}, {"id": 9, "topic": "Ratios"}]}

    result = topics.label_lost_marks(FakeLLM(reply), marks, ["Dividing fractions"])
    assert {int(k): v for k, v in result.items()} == {7: "Dividing fractions", 9: "Ratios"}


def test_weekly_summary_uses_only_given_numbers():
    facts = sample_facts()
    week = analytics.jsonable(analytics.changes.weekly_changes(facts.data, facts.overview, facts.weak, NOW))
    model = FakeLLM("Maya's Math 7 average is 60. Dividing fractions came up again.")
    text = summary.write_weekly_summary(model, "Maya", week)
    assert text.strip()
    assert "Math 7" in str(model.calls[0]["messages"]), "the facts must be sent to the model"
    assert not unsupported_numbers(text, week)


def test_alert_messages_are_keyed_by_dedupe_key():
    facts = sample_facts()
    candidates = analytics.changes.alert_candidates(facts.data, facts.overview, facts.weak, NOW)
    batch = [{**c, "facts": analytics.jsonable(c["facts"])} for c in candidates]
    model = FakeLLM(lambda s, m, t, schema: (
        {"messages": [{"dedupe_key": c["dedupe_key"], "message": "Something to look at."} for c in batch]}
        if schema else "\n".join(f"{c['dedupe_key']}: Something to look at." for c in batch)))
    result = alerts.explain_alerts(model, "Maya", batch)
    assert set(result) <= {c["dedupe_key"] for c in batch}
    assert all(isinstance(v, str) and v for v in result.values())


def test_cause_explanation_keeps_the_rules_decision():
    facts = sample_facts()
    cause = analytics.jsonable(facts.cause)
    model = FakeLLM("This may point to understanding in Math 7 rather than effort.")
    text = advice.explain_cause(model, "Maya", cause)
    assert text.strip()
    assert cause["cause"] in str(model.calls[0]["messages"]) or "understanding" in model.calls[0]["system"].lower()


def test_gap_explanation_is_returned():
    gap = {"course": "Math 7", "homework_average": 95.0, "test_average": 60.0, "gap": 35.0,
           "flagged": True, "threshold": 20}
    text = advice.explain_gap(FakeLLM("Tests in Math 7 are 35 points below homework."), "Maya", gap)
    assert text.strip() and not unsupported_numbers(text, gap)


def test_agent_calls_tools_then_answers_with_sources():
    model = FakeLLM(
        tool_reply(("get_overview", {})),
        text_reply("Math 7 is at 60 per the overview."),
    )
    result = agent.answer(model, "Maya", [], "How is math going?", AgentTools(sample_facts()))
    assert result.text == "Math 7 is at 60 per the overview."
    assert [s["tool"] for s in result.sources] == ["get_overview"]
    sent_back = model.calls[1]["messages"]
    assert any(m["role"] == "tool" and m["name"] == "get_overview" for m in sent_back)
    assert model.calls[0]["tools"], "tools must be passed to the model"


def test_agent_reports_bad_tool_calls_as_errors():
    model = FakeLLM(tool_reply(("no_such_tool", {})), text_reply("I don't have that information."))
    agent.answer(model, "Maya", [], "?", AgentTools(sample_facts()))
    [result] = [m for m in model.calls[1]["messages"] if m["role"] == "tool"]
    assert result["is_error"] is True


def test_agent_loop_is_capped():
    model = FakeLLM(*[tool_reply(("get_overview", {})) for _ in range(30)])
    try:
        agent.answer(model, "Maya", [], "?", AgentTools(sample_facts()))
    except llm.LLMError:
        pass
    assert len(model.calls) <= 12
