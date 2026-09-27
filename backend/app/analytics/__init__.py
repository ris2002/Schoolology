"""The rule-based layer. Every number the parent sees is calculated here, never by the AI."""

from dataclasses import dataclass
from datetime import datetime

from . import cause, changes, grades, topics
from .data import ChildData, jsonable, load


@dataclass
class ChildFacts:
    data: ChildData
    overview: dict
    gap: list[dict]
    weak: dict
    cause: dict
    now: datetime


def compute(data: ChildData, now: datetime) -> ChildFacts:
    overview = grades.overview(data, now)
    weak = topics.weak_topics(data)
    return ChildFacts(
        data=data,
        overview=overview,
        gap=grades.homework_test_gap(data),
        weak=weak,
        cause=cause.likely_cause(data, overview, weak, now),
        now=now,
    )


def for_child(conn, child_id: int, now: datetime) -> ChildFacts:
    return compute(load(conn, child_id), now)


__all__ = ["ChildData", "ChildFacts", "changes", "compute", "for_child", "jsonable", "load"]
