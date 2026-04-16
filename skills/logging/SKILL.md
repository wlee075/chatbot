---
name: logging
description: "Documents the session-scoped JSONL logger in utils/logger.py. Covers the log_event() contract, standard context fields, all event types by node, thread_id vs run_id scoping, DEBUG gating, and how to add new log events."
---

# Logging

A session-scoped JSONL event logger. Every log line is a self-contained JSON object with standard context fields and flat event-specific extras. Provides structured observability across all 6 nodes and the routing functions.

---

## Scope

| Item | Location |
|---|---|
| Logger implementation | `utils/logger.py` |
| Call sites | `graph/nodes.py`, `graph/routing.py` |

See `skills/prd-writing/SKILL.md` for `thread_id` and `run_id` field definitions.

---

## Output Files

| File | Written when | Content |
|---|---|---|
| `logs/session_<thread_id[:8]>.log` | Always | INFO + WARNING |
| `logs/session_<thread_id[:8]>.debug` | `LOG_LEVEL=DEBUG` only | DEBUG (prompt text, raw LLM responses) |

File handles are cached per `thread_id` in `_HANDLES` to avoid re-opening on every call.

---

## `log_event()` Signature

```python
def log_event(
    *,
    thread_id: str,
    run_id: str,
    level: str,          # "DEBUG" | "INFO" | "WARNING"
    event_type: str,     # e.g. "node_start", "reflect_parsed"
    message: str,        # short human-readable summary
    node_name: str = "",
    section_name: str = "",
    section_index: int = -1,
    iteration: int = -1,
    **extra,             # flat event-specific fields
) -> None
```

Standard fields appear on every log line. Extra kwargs are merged at the same level — no nesting.

---

## Standard Context Helper

Every node calls `_log_ctx(state, node_name)` to build the standard context dict:

```python
def _log_ctx(state: PRDState, node_name: str) -> dict:
    section_idx = state.get("section_index", 0)
    section_name = PRD_SECTIONS[section_idx].title if 0 <= section_idx < len(PRD_SECTIONS) else ""
    return {
        "thread_id": state.get("thread_id", ""),
        "run_id": state.get("run_id", ""),
        "node_name": node_name,
        "section_name": section_name,
        "section_index": section_idx,
        "iteration": state.get("iteration", 0),
    }
```

Pass as `**ctx` to every `log_event(**ctx, level=..., event_type=..., ...)` call.

---

## Event Type Inventory

### `load_context_node`

| Event type | Level | Extra fields |
|---|---|---|
| `node_start` | INFO | `context_doc_present`, `context_len` |
| `node_end` | INFO | `duration_ms`, `context_doc_present`, `context_len` |

### `generate_questions_node`

| Event type | Level | Extra fields |
|---|---|---|
| `node_start` | INFO | `is_follow_up`, `triage`, `gaps_count` |
| `elicitor_empty_output` | WARNING | — |
| `elicitor_output` | INFO | `is_follow_up`, `triage`, `gaps_count`, `question_count`, `output_len` |
| `elicitor_prompt` | DEBUG | `system_prompt` |
| `elicitor_raw_output` | DEBUG | `raw_output` |
| `node_end` | INFO | `duration_ms`, `question_count` |

### `draft_node`

| Event type | Level | Extra fields |
|---|---|---|
| `node_start` | INFO | `qa_rounds` |
| `drafter_empty_output` | WARNING | — |
| `drafter_high_assumptions` | WARNING | `assumption_count` |
| `drafter_output` | INFO | `qa_rounds`, `draft_len`, `assumption_count` |
| `drafter_prompt` | DEBUG | `system_prompt` |
| `drafter_raw_output` | DEBUG | `raw_output` |
| `node_end` | INFO | `duration_ms`, `draft_len`, `assumption_count` |

### `reflect_node`

| Event type | Level | Extra fields |
|---|---|---|
| `node_start` | INFO | `draft_len` |
| `reflector_prompt` | DEBUG | `system_prompt` |
| `reflector_raw_output` | DEBUG | `raw_output` |
| `reflect_parsed` | INFO | `overall_score`, `completeness_score`, `specificity_score`, `internal_consistency_score`, `implementability_score`, `llm_verdict`, `llm_triage`, `resolved_count`, `unresolved_count`, `gaps_count` |
| `reflect_parse_warning` | WARNING | `field` |
| `reflect_override` | WARNING | `field`, `llm_value`, `enforced_value`, `overall_score`, `threshold` |
| `reflect_missing_gaps` | WARNING | — |
| `state_update` | INFO | one key per changed field as `"before -> after"` string |
| `node_end` | INFO | `duration_ms`, `overall_score`, `llm_verdict`, `enforced_verdict`, `enforced_triage`, `new_iteration`, `new_recovery_count` |

### `advance_section_node`

| Event type | Level | Extra fields |
|---|---|---|
| `node_start` | INFO | `advance_reason` |
| `advance_section_pass` | INFO | `section_saved`, `final_score`, `final_verdict`, `iterations_used`, `recovery_count`, `next_section`, `is_complete` |
| `advance_section_forced_recovery_cap` | INFO | same as above |
| `advance_section_forced_iter_cap` | INFO | same as above |
| `forced_progression` | WARNING | `section_saved`, `final_score`, `advance_reason` |
| `node_end` | INFO | `duration_ms`, `advance_reason`, `next_section`, `is_complete` |

### `finalize_node`

| Event type | Level | Extra fields |
|---|---|---|
| `node_start` | INFO | `sections_completed`, `total_sections` |
| `node_end` | INFO | `duration_ms`, `sections_completed`, `total_sections`, `prd_len` |

### `route_after_reflect`

| Event type | Level | Extra fields |
|---|---|---|
| `routing_decision` | INFO | `overall_score`, `verdict`, `triage`, `recovery_mode_consecutive_count`, `route`, `reason` |

---

## `thread_id` vs `run_id`

| Identifier | Scope | Set by | Stable across |
|---|---|---|---|
| `thread_id` | Streamlit session | `app.py` on session init | All `.invoke()` calls in the session |
| `run_id` | Single `.invoke()` call | `app.py` per invocation (`uuid.uuid4()`) | All nodes executing before the next interrupt |

Use `thread_id` to group all events for a PM session. Use `run_id` to trace which nodes ran in response to a specific PM answer.

---

## DEBUG Gating

Gate all prompt text and raw LLM response logging behind `DEBUG`:

```
LOG_LEVEL=DEBUG   # in .env
```

Without this, `.debug` files are not created and verbose output is never written.

---

## Adding a New Log Event

1. Choose level: WARNING for unexpected/problematic state, INFO for normal events, DEBUG for verbose output.
2. Choose `event_type`: `snake_case`, domain-specific, unique within the node.
3. Call `log_event(**ctx, level="INFO", event_type="my_event", message="...", my_field=value)`.
4. Add the event to this skill's inventory table.

Extra fields must be **flat** (no nested dicts or lists).

---

## Do / Don't

| Do | Don't |
|---|---|
| Gate ALL prompt/response text behind DEBUG | Log full prompts or LLM responses at INFO — they are large |
| Use `_log_ctx(state, node_name)` for standard fields | Manually build the context dict — risks field inconsistency |
| Use flat extra kwargs only | Pass nested objects or lists as extra fields |
| Log state changes as `"before -> after"` strings in `state_update` events | Log state changes silently |
| Emit `reflect_override` WARNING whenever programmatic enforcement fires | Silently enforce thresholds — this event is the audit trail |
