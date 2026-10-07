"""Marked papers: read the pages (vision), then write advice for that assignment."""

import json

from ..llm import LLM, LLMBadOutput, image_part, text_part
from .prompts import ASSIGNMENT_ADVICE, PAPER_READER

# Every field may be missing on the paper, so each one also allows null. All are required, so the
# model always sends every key (null when it can't read it) instead of leaving keys out.
_TEXT = {"type": ["string", "null"]}
_MARK = {"type": ["number", "null"]}
QUESTIONS_SCHEMA = {
    "type": "object",
    "properties": {
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"label": _TEXT, "question": _TEXT, "student_answer": _TEXT,
                               "teacher_comment": _TEXT, "points_earned": _MARK, "points_possible": _MARK},
                "required": ["label", "question", "student_answer", "teacher_comment",
                             "points_earned", "points_possible"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["questions"],
    "additionalProperties": False,
}


def read_marked_paper(llm: LLM, pages: list[bytes], context: dict) -> list[dict]:
    """Read a marked paper and return one dict per question, in paper order.

    pages:   JPEG bytes, one per page, in order. Send them with llm.image_part(page)
             inside a single user message, e.g.
             [llm.text_part(instructions), llm.image_part(p1), llm.image_part(p2)].
    context: {"subject": "Math 7", "assignment": "Quiz 3: Fractions",
              "max_points": 100 or None}   # from Schoology when known

    Return, per question:
        {"label": "3b" or None,              # the paper's own numbering
         "question": "3/4 ÷ 1/2 = ?" or None,
         "student_answer": "3/8" or None,
         "teacher_comment": "Flip the second fraction" or None,
         "points_earned": 0 or None,         # None when the mark can't be read
         "points_possible": 5 or None}

    Must:
    - Read only what is on the page. Never guess a mark that isn't written; use None.
    - Include questions that got full marks too, so totals can be checked.
    - Use structured output (llm.json or llm.chat with json_schema) so every field is present.
    The parent checks and corrects everything before it is saved, and the caller
    cleans the values, so an imperfect read is fine; an invented one is not.
    """
    
    # One user message: what the paper is, then every page as an image, in order.
    intro = "Read this marked paper.\n" + json.dumps(context)
    content = [text_part(intro), *[image_part(page) for page in pages]]
    response = llm.chat(PAPER_READER, [{"role": "user", "content": content}], json_schema=QUESTIONS_SCHEMA)
    if response.stop == "max_tokens":
        raise LLMBadOutput("The paper was too long to read in one go. Try fewer pages.")
    try:
        data = json.loads(response.text)
    except json.JSONDecodeError as e:
        raise LLMBadOutput("The paper could not be read. Please try again.") from e
    questions = data.get("questions") if isinstance(data, dict) else None
    return questions if isinstance(questions, list) else []



def write_assignment_advice(llm: LLM, child_name: str, facts: dict) -> str:
    """Advice for one assignment, aware of earlier ones in the same subject.

    facts:
        {"subject": "Math 7", "assignment": "Quiz 3: Fractions",
         "score": {"earned": 60, "possible": 100, "percent": 60.0},
         "questions_lost": [{"label": "3", "question": "...", "student_answer": "...",
                             "teacher_comment": "...", "points_lost": 15, "points_possible": 15,
                             "topic": "Dividing fractions" or None}, ...],
         "repeats": [{"topic": "Dividing fractions", "earlier_assignments": ["Quiz 2", "Unit 2 Test"],
                      "times_before": 2}, ...],       # same topic lost in earlier work, this subject
         "subject_verdict": {"cause": "understanding", "suggestion": "..."} or None,
         "homework_vs_tests": {"homework_average": 93.3, "test_average": 63.0, "gap": 30.3,
                               "flagged": True} or None}

    Must:
    - Be about this assignment: what went wrong and what to practise next.
    - Mention repeats by naming the earlier assignments ("as in Quiz 2").
    - Stay consistent with subject_verdict; never contradict it.
    - Only use numbers present in facts. Never blame the child. 80-150 words.
    """
    data={"child_name":child_name,**facts}
    content=json.dumps(data)
    messages=[{'role':'user','content':content}]
    response=llm.chat(ASSIGNMENT_ADVICE,messages)
    return response.text
    raise NotImplementedError
