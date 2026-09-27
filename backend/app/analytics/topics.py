"""Weak topics: count lost marks per topic and flag topics missed across assignments.

Topic labels come from the AI labelling feature. This module only counts them.
"""

from .. import config
from .data import ChildData


def normalise(topic: str) -> str:
    return " ".join(topic.lower().split())


def weak_topics(data: ChildData) -> dict:
    """Topics ranked by points lost; flagged when missed in 3+ different assignments.

    flagged_at is when the topic crossed the threshold (when the assignment that
    made it the 3rd was graded), so alerts and weekly summaries can tell a
    newly flagged topic from an old one.
    """
    minimum = config.THRESHOLDS["weak_topic_min_assignments"]
    groups: dict[str, dict] = {}
    unlabelled = 0
    for m in data.lost_marks:
        if not m["topic"]:
            unlabelled += 1
            continue
        a = data.assignment_by_id.get(m["assignment_id"])
        if a is None:
            continue
        g = groups.setdefault(normalise(m["topic"]), {"topic": m["topic"], "marks": [], "assignments": {}})
        g["marks"].append((m, a))
        g["assignments"][a["id"]] = a

    topics = []
    for key, g in groups.items():
        assignments = sorted(g["assignments"].values(), key=lambda a: a["graded_at"] or a["due_at"])
        flagged = len(assignments) >= minimum
        examples = sorted(g["marks"], key=lambda ma: ma[0]["points_lost"], reverse=True)[:3]
        topics.append({
            "topic": g["topic"],
            "key": key,
            "courses": sorted({data.course_title(a) for a in assignments}),
            "course_ids": sorted({a["course_id"] for a in assignments}),
            "assignment_count": len(assignments),
            "points_lost": round(sum(m["points_lost"] for m, _ in g["marks"]), 1),
            "flagged": flagged,
            "flagged_at": (assignments[minimum - 1]["graded_at"] or assignments[minimum - 1]["due_at"])
            if flagged else None,
            "examples": [
                {
                    "assignment_id": a["id"],
                    "assignment": a["title"],
                    "course": data.course_title(a),
                    "question": m["question_text"],
                    "student_answer": m["student_answer"],
                    "teacher_comment": m["teacher_comment"],
                    "points_lost": m["points_lost"],
                    "points_possible": m["points_possible"],
                }
                for m, a in examples
            ],
        })
    topics.sort(key=lambda t: (not t["flagged"], -t["points_lost"]))
    return {"threshold": minimum, "unlabelled_marks": unlabelled, "topics": topics}
