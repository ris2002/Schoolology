"""Feature 4: one-line alert explanations."""

from ..llm import LLM
from .prompts import ALERT_EXPLAINER 
import json


def explain_alerts(llm: LLM, child_name: str, alerts: list[dict]) -> dict[str, str]:
    """Return {dedupe_key: message} with one line per alert: what happened and one next step.

    alerts: new alerts, each {"rule": ..., "dedupe_key": ..., "facts": {...}}. Rules and their facts:
        missing_work     facts = <assignment> (see summary.py)
        low_test_score   facts = <assignment> + {"threshold": 60}
        average_drop     facts = {"course", "average_now", "average_7_days_ago", "change", "threshold"}
        new_weak_topic   facts = {"topic", "courses", "assignment_count", "points_lost", "examples"}

    Must: one sentence of what happened plus one short next step, about 30 words;
    only numbers from facts; neutral, never alarming or blaming.
    Missing keys are allowed: that alert is shown with its facts only.

    """
    n_dict={}
    s_dict={}
    for x_dict in alerts:
        dedupe_key=x_dict['dedupe_key']
        n_dict[dedupe_key]=x_dict

    data={"child_name":child_name,**n_dict}
    content=json.dumps(data)
    messages=[{'role':'user','content':content}]
    response=llm.chat(ALERT_EXPLAINER ,messages)

    # The reply has one line per alert: "<dedupe_key>: <message>"
    for line in response.text.splitlines():
        parts=line.split(": ",1)
        if len(parts)!=2:
            continue  # a blank or badly written line
        key=parts[0].strip()
        message=parts[1].strip()
        if key in n_dict and message:
            s_dict[key]=message
    return s_dict
