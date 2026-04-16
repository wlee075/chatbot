---
name: routing
description: "Documents the deterministic routing logic after reflect and advance_section nodes. Covers route_after_reflect decision table, cap priority order, route_after_advance, forced-advance semantics, and how to extend routing with new conditions."
---

# Routing

Routing functions are pure state readers. They make a deterministic decision about where the graph goes next, do not modify state, and each emit one log event.

---

## Scope

| Item | Location |
|---|---|
| `route_after_reflect` | `graph/routing.py` |
| `route_after_advance` | `graph/routing.py` |

See `skills/prd-writing/SKILL.md` for constants and graph topology.  
See `skills/reflection/SKILL.md` for how `verdict` and `triage_decision` are set.

---

## `route_after_reflect`

**Called after:** `reflect` node  
**Returns:** `"advance_section"` or `"generate_questions"`

### Decision Table

Evaluated in priority order — first match wins:

| Priority | Condition | Route | Reason code |
|---|---|---|---|
| 1 | `verdict == "PASS"` | `advance_section` | `PASS` |
| 2 | `recovery_count >= DEFAULT_MAX_RECOVERY_MODE_CONSECUTIVE_ITERATIONS` | `advance_section` | `RECOVERY_CAP` |
| 3 | `iteration >= state["max_iterations"]` | `advance_section` | `ITER_CAP` |
| 4 | (none of the above) | `generate_questions` | `LOOP` |

**Constants** (defined in `prompts/templates.py`, imported in `routing.py`):
- `DEFAULT_MAX_RECOVERY_MODE_CONSECUTIVE_ITERATIONS` = 2
- `DEFAULT_MAX_SECTION_ITERATIONS` = 5 (fallback when `state["max_iterations"]` is absent)

### Cap Precedence

Recovery cap (priority 2) is checked before iteration cap (priority 3). When both are simultaneously true, the logged reason is `RECOVERY_CAP`. Consecutive recovery failures are treated as a more urgent forcing condition than hitting the raw iteration limit.

### State Fields Read

| Field | Used for |
|---|---|
| `verdict` | Priority 1 check |
| `recovery_mode_consecutive_count` | Priority 2 check |
| `iteration` | Priority 3 check |
| `max_iterations` | Priority 3 threshold |
| `overall_score`, `triage_decision` | Logged only — not used in routing logic |

---

## `route_after_advance`

**Called after:** `advance_section` node  
**Returns:** `"finalize"` or `"generate_questions"`

| Condition | Route |
|---|---|
| `state["is_complete"] == True` | `finalize` |
| otherwise | `generate_questions` |

`is_complete` is set by `advance_section_node` when `next_index >= len(PRD_SECTIONS)`.

---

## Log Event: `routing_decision`

Both routing functions emit one `INFO` event per call:

```json
{
  "event_type": "routing_decision",
  "node_name": "route_after_reflect",
  "overall_score": 7.2,
  "verdict": "REWORK",
  "triage": "NORMAL",
  "recovery_mode_consecutive_count": 0,
  "route": "generate_questions",
  "reason": "LOOP"
}
```

`triage` is normalised to `"RECOVERY"` or `"NORMAL"` for log brevity.

---

## Forced Advance

When `route_after_reflect` returns `advance_section` with reason `RECOVERY_CAP` or `ITER_CAP`, the section advances **without a PASS verdict**. The draft in `current_draft` is saved as-is to `prd_sections`. `advance_section_node` logs a `forced_progression` WARNING.

Sections advanced this way may still contain `[NEEDS CLARIFICATION: ...]` or `[ASSUMPTION: ...]` flags — visible in the final PRD, not silently removed.

---

## Extending Routing

To add a new forced-advance condition (e.g. PM skip button, global time cap):

1. Add the condition check in `route_after_reflect` before the `LOOP` fallback.
2. Assign a new `reason` code string.
3. Update `advance_section_node` — add the new reason to the `if/elif/else` chain for `advance_event` and `advance_reason`.
4. Update the `log_event(event_type="routing_decision", ...)` call if the new reason needs additional fields.

---

## Do / Don't

| Do | Don't |
|---|---|
| Keep routing functions as pure state readers | Modify `state` inside a routing function |
| Maintain priority order (PASS → RECOVERY_CAP → ITER_CAP → LOOP) | Swap cap priorities |
| Log `overall_score` and `triage` even though they don't affect routing | Use routing functions for anything beyond returning a string |
| Return a key present in the `path_map` of `add_conditional_edges` | Return a route string not in the `path_map` — LangGraph raises at compile time |
