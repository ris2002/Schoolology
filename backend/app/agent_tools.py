"""Data tools for the chat agent. Each returns stored facts only, as JSON-ready dicts.

    tools = AgentTools(facts)
    tools.definitions            # pass to llm.chat(..., tools=tools.definitions)
    tools.run(name, arguments)   # -> dict; raises ToolError for bad calls

Every assignment in a result carries assignment_id, title and course, so the
agent can name its source.
"""

import inspect

from . import analytics
from .analytics import changes, grades


class ToolError(Exception):
    """A bad tool call (unknown tool, bad arguments). Send the message back as an error result."""


STATUSES = ["missing", "late", "upcoming", "graded", "awaiting_grade", "excused", "all"]
KINDS = ["homework", "test", "other", "all"]

DEFINITIONS = [
    {
        "name": "get_overview",
        "description": "Each course's current average, its change over 7 and 14 days, and counts of "
                       "missing, late and ungraded work. Also lists missing work and work due in the next 7 days.",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "list_assignments",
        "description": "Assignments filtered by course, status and kind, newest due date first (max 50).",
        "parameters": {
            "type": "object",
            "properties": {
                "course": {"type": "string", "description": "Part of the course name, e.g. 'math'. Omit for all."},
                "status": {"type": "string", "enum": STATUSES, "description": "Default 'all'."},
                "kind": {"type": "string", "enum": KINDS, "description": "Default 'all'."},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "get_assignment",
        "description": "One assignment in detail, including the marks lost on it and their topics.",
        "parameters": {
            "type": "object",
            "properties": {"assignment_id": {"type": "integer"}},
            "required": ["assignment_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_weak_topics",
        "description": "Topics where marks were lost, ranked by points lost. A topic is flagged as weak when "
                       "missed in several different assignments. Includes example mistakes.",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "get_homework_test_gap",
        "description": "Homework average versus test average per course, and whether the gap is large.",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "get_likely_cause",
        "description": "The rule-based judgement of whether struggles look like effort, understanding, both, "
                       "or something outside school, with the signals behind it for each course.",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "get_recent_changes",
        "description": "What changed in the last 7 days: grade moves, new missing work, work handed in late, "
                       "tests graded, newly flagged weak topics and tests coming up.",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
]


class AgentTools:
    definitions = DEFINITIONS

    def __init__(self, facts: analytics.ChildFacts):
        self.facts = facts

    def run(self, name: str, arguments: dict) -> dict:
        handler = getattr(self, f"_{name}", None)
        if name not in {d["name"] for d in DEFINITIONS} or handler is None:
            raise ToolError(f"Unknown tool {name!r}")
        if not isinstance(arguments, dict):
            raise ToolError("Arguments must be an object")
        try:
            inspect.signature(handler).bind(**arguments)
        except TypeError as e:
            raise ToolError(f"Bad arguments for {name}: {e}") from e
        return analytics.jsonable(handler(**arguments))

    def _get_overview(self):
        return self.facts.overview

    def _list_assignments(self, course: str | None = None, status: str = "all", kind: str = "all"):
        if status not in STATUSES or kind not in KINDS:
            raise ToolError(f"status must be one of {STATUSES}; kind one of {KINDS}")
        data, now = self.facts.data, self.facts.now
        refs = [grades.assignment_ref(data, a, now) for a in data.assignments]
        if course:
            refs = [r for r in refs if course.lower() in r["course"].lower()]
            if not refs:
                raise ToolError(f"No course matches {course!r}. Courses: {[c['title'] for c in data.courses]}")
        if status == "late":
            refs = [r for r in refs if r["late"]]
        elif status != "all":
            refs = [r for r in refs if r["status"] == status]
        if kind != "all":
            refs = [r for r in refs if r["kind"] == kind]
        refs.sort(key=lambda r: r["due_at"] or now, reverse=True)
        return {"count": len(refs), "assignments": refs[:50]}

    def _get_assignment(self, assignment_id: int):
        data = self.facts.data
        try:
            assignment_id = int(assignment_id)  # some open models send ids as strings
        except (TypeError, ValueError) as e:
            raise ToolError("assignment_id must be a number") from e
        a = data.assignment_by_id.get(assignment_id)
        if a is None:
            raise ToolError(f"No assignment with id {assignment_id}")
        marks = [m for m in data.lost_marks if m["assignment_id"] == assignment_id]
        return {**grades.assignment_ref(data, a, self.facts.now), "lost_marks": marks}

    def _get_weak_topics(self):
        return self.facts.weak

    def _get_homework_test_gap(self):
        return {"courses": self.facts.gap}

    def _get_likely_cause(self):
        return self.facts.cause

    def _get_recent_changes(self):
        f = self.facts
        return changes.weekly_changes(f.data, f.overview, f.weak, f.now)
