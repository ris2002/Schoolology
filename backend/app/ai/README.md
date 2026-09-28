# AI features

Each file here has one or two empty functions. Their docstrings say exactly what they receive and must return. Everything around them is done: routes call them, results are stored and cached, and failures are reported to the API as `ai_error`.

| File | Function | Feature |
|---|---|---|
| `prompts.py` | system prompts | Tone and rules for all features |
| `topics.py` | `label_lost_marks` | 2 Weak-topic labels |
| `summary.py` | `write_weekly_summary` | 3 Weekly summary |
| `alerts.py` | `explain_alerts` | 4 Alert explanations |
| `advice.py` | `explain_cause`, `explain_gap` | 5 Advice, 6 Gap explanation |
| `agent.py` | `answer` | 7 Chat agent |
| `papers.py` | `read_marked_paper` | Read a marked paper from page images (vision) |
| `papers.py` | `write_assignment_advice` | Advice for one uploaded assignment, aware of earlier ones |

A suggested order: `prompts.py` → `advice.py` (one call, text out) → `summary.py` → `alerts.py` → `topics.py` (structured output) → `agent.py` (tool loop).

## Rules for every feature

1. **Only numbers from the input.** Never calculate, estimate or invent a number. If a feature needs a new number, it belongs in `app/analytics/`.
2. **Causes are possibilities**, never certainties: "this may mean".
3. **Never blame or accuse the child.**
4. **Short and plain**, for a busy parent.

## The model interface

You get an `llm` object. It works the same for Hugging Face and Claude, whichever the admin selected.

```python
from ..llm import LLM, LLMError, tool_result

# One turn, text out
r = llm.chat(system=prompts.CAUSE_EXPLAINER,
             messages=[{"role": "user", "content": json.dumps(facts)}],
             max_tokens=600)
r.text          # the reply
r.stop          # "end", "tool_use" or "max_tokens"

# Structured output: pass a JSON Schema, get parsed JSON back
data = llm.json(system, prompt, schema={"type": "object", "properties": {...}, "required": [...]})

# Images (for reading papers): one user message with the instructions and every page
from ..llm import image_part, text_part
r = llm.chat(system, [{"role": "user", "content": [text_part("Read this marked paper."), *[image_part(p) for p in pages]]}],
             json_schema=schema)

# Tools (for the agent)
r = llm.chat(system, messages, tools=tools.definitions)
for call in r.tool_calls:        # ToolCall(id, name, arguments, arguments_error)
    ...
messages.append(r.message)        # always append r.message, not a dict you build
messages.append(tool_result(call, json.dumps(result)))            # one per call
messages.append(tool_result(call, "error text", is_error=True))   # for a failed call
```

**Errors:**
- `LLMError` is the base class. It has `.retryable` for rate limits and timeouts.
- `LLMRefusal`: the model declined to answer.
- `LLMBadOutput`: invalid JSON or output cut off.
- `LLMNotConfigured`: no key, or the key was rejected.

Let these propagate unless you can handle them. The service layer reports them.

**Provider differences to design for:**
- **Structured output:** Claude enforces the schema. Hugging Face models are told the schema and parsed leniently, but can still return something off-schema. Validate what you get, e.g. check every id you asked for came back.
- **Tool calling:** open models sometimes send bad arguments. `ToolCall.arguments_error` is set when the arguments were not valid JSON. `tools.run` raises `agent_tools.ToolError` for unknown tools or bad arguments. Send both back as `is_error=True` results so the model can retry.
- **Models:** the default Hugging Face model is smaller than Claude. Test prompts on both.

## Testing

```bash
cd backend
../.venv/bin/python -m pytest -m ai
```

- `tests/ai/test_ai_contracts.py` checks each feature against the contract using `tests/ai/fake_llm.py`, a scripted model. It needs no keys and costs nothing.
- `unsupported_numbers(text, facts)` in that file finds numbers in AI text that aren't in the facts. It's useful when you test with real models too.
- The topic and alert tests assume a common JSON shape for the fake reply. If your schema differs, adjust the fake reply in the test, not the rule.

To try a feature against the demo child with a real model:
1. Set `HF_TOKEN` or `ANTHROPIC_API_KEY`.
2. Start the server and `POST /auth/demo`.
3. Call the feature's route, e.g. `GET /api/children/{id}/advice`.
