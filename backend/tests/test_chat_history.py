"""Earlier chat messages are labelled with their age before the agent reads them. No database needed."""

from datetime import timedelta

from app import services

NOW = services.now()


def msg(role, text, minutes_ago):
    return {"role": role, "text": text, "created_at": NOW - timedelta(minutes=minutes_ago)}


def test_recent_messages_are_sent_as_they_are():
    out = services.label_history([msg("user", "How is Physics?", 5), msg("assistant", "86.4", 4)], NOW)
    assert out == [{"role": "user", "content": "How is Physics?"}, {"role": "assistant", "content": "86.4"}]


def test_old_messages_carry_their_date_and_old_answers_a_warning():
    out = services.label_history([msg("user", "Which work was late?", 3 * 24 * 60),
                                  msg("assistant", "Two items were late.", 3 * 24 * 60)], NOW)
    assert out[0]["content"].startswith("[asked ") and out[0]["content"].endswith("Which work was late?")
    assert "may be out of date" in out[1]["content"] and out[1]["content"].endswith("Two items were late.")
    assert out[1]["role"] == "assistant"
