---
name: elicitation
description: "Generates one focused question per round to elicit PM decisions for the current PRD section. Covers generate_questions_node, ELICITOR_SYSTEM, ELICITOR_ITERATION_BLOCK, and RECOVERY vs NORMAL iteration behaviour."
---

# Elicitation

The Elicitor asks **one question per round** targeting the most critical unresolved component of the current PRD section. It does not draft content, normalise decisions, or score the draft.

---

## Scope

| Item | Location |
|---|---|
| Node | `generate_questions_node` in `graph/nodes.py` |
| Main prompt | `ELICITOR_SYSTEM` in `prompts/templates.py` |
| Iteration prompt | `ELICITOR_ITERATION_BLOCK` in `prompts/templates.py` |

See `skills/prd-writing/SKILL.md` for PRDState fields, constants, and graph topology.

---

## Inputs and Outputs

**State fields consumed:**

| Field | Use |
|---|---|
| `section_index` | Determines which `PRDSection` to elicit for |
| `iteration` | `0` = first question; `>0` = follow-up round |
| `context_doc` | Injected as `ELICITOR_CONTEXT_BLOCK` when non-empty |
| `prd_sections` | Injected as `ELICITOR_PRD_BLOCK` when any sections are complete |
| `reflection` | Injected into `ELICITOR_ITERATION_BLOCK` when `iteration > 0` |
| `requirement_gaps` | Injected into `ELICITOR_ITERATION_BLOCK` when `iteration > 0` |
| `triage_decision` | Controls which rules branch fires in `ELICITOR_ITERATION_BLOCK` |
| `max_iterations` | Used in iteration header display only |

**State fields written:**

| Field | Value |
|---|---|
| `current_questions` | Raw LLM output — the single question |
| `chat_history` | Appends one `{"role": "assistant", "type": "elicit", ...}` item |

The `chat_history` content is `"{header}\n\n{question}"` where:
- First round: `**Section N/13: {section_title}**`
- Follow-up: `**Section N/13: {section_title}** _(follow-up · iteration K/max)_`

---

## Prompt Architecture

### `ELICITOR_SYSTEM` — format fields

| Field | Present when |
|---|---|
| `section_title`, `section_description`, `expected_components_list` | Always |
| `context_block` | `state["context_doc"]` is non-empty |
| `prd_block` | At least one prior section is complete |
| `iteration_block` | `iteration > 0` AND `state["reflection"]` is truthy |
| `global_rigor_block`, `decision_enforcement_block`, `iteration_discipline_block`, `human_trust_block` | Always |

Optional blocks evaluate to `""` when their condition is false. The `.format()` call always passes all fields.

### Core output rules

- Ask exactly **1** focused question.
- Target the **single most important unresolved component**.
- Question must be binary (Yes/No) or a selection among clearly defined options.
- Output only the question — no numbering, no preamble, no closing remarks.

---

## Iteration Block (`ELICITOR_ITERATION_BLOCK`)

Injected only when `iteration > 0` AND `state["reflection"]` is non-empty.

**Format fields:**

| Field | Value |
|---|---|
| `iteration` | `iteration + 1` (1-based for display) |
| `max_iterations` | `state["max_iterations"]` |
| `reflection` | Full reflector output text |
| `requirement_gaps` | Extracted gaps, or fallback string if empty |
| `triage_decision` | Full triage string from state |

**Rules by triage mode:**

| Mode | Instruction |
|---|---|
| `TRIAGE: ENTER RECOVERY MODE` | Ask 1 high-impact question that collapses multiple gaps into one threshold or enforcement decision |
| `TRIAGE: NORMAL ITERATION` | Ask 1 focused question targeting the most important unresolved gap |

Both modes: convert vague areas to Yes/No or explicit-option decisions; never repeat resolved questions.

---

## Iteration 0 vs Iteration > 0

| Property | Iteration 0 | Iteration > 0 |
|---|---|---|
| `iteration_block` | `""` | Full `ELICITOR_ITERATION_BLOCK` |
| Question guidance source | `section.expected_components` only | `expected_components` + `requirement_gaps` from Reflector |
| Question priority | First unresolved component | Most critical gap from reflection |

---

## Warning Log Events

| `event_type` | Level | Condition |
|---|---|---|
| `elicitor_empty_output` | WARNING | LLM returns empty string |

See `skills/logging/SKILL.md` for the full event inventory.

---

## Do / Don't

| Do | Don't |
|---|---|
| Change question wording, rigor level, or domain framing | Change `"Ask exactly 1"` to a higher count |
| Add new optional context blocks (with guards in the node) | Remove or rename existing `{format_fields}` — breaks `.format()` call |
| Adjust fallback string for empty `requirement_gaps` | Inject `iteration_block` when `iteration == 0` |
| Change chat header display text | Change `"type": "elicit"` — `app.py` renders on this value |
