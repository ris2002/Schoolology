"""Feature 2: label each lost mark with a short topic."""

from ..llm import LLM


def label_lost_marks(llm: LLM, marks: list[dict], existing_topics: list[str]) -> dict[int, str]:
    """Return {lost_mark_id: topic} for the marks given.

    marks: up to 40 unlabelled lost marks, each like
        {"id": 12, "course": "Math 7", "assignment": "Quiz 3: Multiplying and Dividing Fractions",
         "granularity": "question",            # or "assignment" when only the score is known
         "question": "3/4 ÷ 1/2 = ?", "student_answer": "3/8",
         "teacher_comment": "Keep, change, flip", "points_lost": 15, "points_possible": 15}
    existing_topics: topics already used for this child, e.g. ["Dividing fractions", "Ratios"].
        Reuse one when it fits, so the same skill is never named two ways.

    Must:
    - Return a label for every id given, and only for those ids.
    - Keep labels short (2-5 words), a skill rather than a question ("Dividing fractions").
    - Use structured output (llm.json with a JSON Schema) rather than parsing free text.
    Ids you return that were not asked for are ignored by the caller.
    """
    raise NotImplementedError
