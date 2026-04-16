---
name: drafting
description: "Documents the draft_node and DRAFTER_SYSTEM. Covers Q&A accumulation across all rounds, multi-round synthesis behaviour, inline flag semantics ([NEEDS CLARIFICATION], [ASSUMPTION], [CONFLICT]), and warning thresholds."
---

# Drafting

The Drafter synthesises all accumulated Q&A pairs for the current section into a structured prose draft. It writes only from confirmed PM answers — it does not ask questions, normalise decisions, or score.

---

## Scope

| Item | Location |
|---|---|
| Node | `draft_node` in `graph/nodes.py` |
| Prompt | `DRAFTER_SYSTEM` in `prompts/templates.py` |

See `skills/context_assembly/SKILL.md` for how `prd_context_block` and `context_doc_block` are injected.  
See `skills/prd-writing/SKILL.md` for PRDState fields and graph topology.

---

## Inputs and Outputs

**State fields consumed:**

| Field | Use |
|---|---|
| `section_index` | Identifies which `PRDSection` to write |
| `section_qa_pairs` | All `[{"questions": str, "answer": str}]` rounds for this section |
| `prd_sections` | Prior completed sections for consistency context |
| `context_doc` | Background document for domain grounding |

**State fields written:**

| Field | Value |
|---|---|
| `current_draft` | Raw LLM draft output |
| `chat_history` | Appends `{"role": "assistant", "type": "draft", "section": ..., "content": draft}` |

`"type": "draft"` renders as a collapsed expander in the Streamlit UI.

---

## Prompt Format Fields

| Field | Present when |
|---|---|
| `section_title`, `section_description`, `expected_components_list` | Always |
| `prd_context_block` | At least one prior section complete |
| `context_doc_block` | `state["context_doc"]` non-empty |
| `global_rigor_block` | Always |

The full formatted Q&A is passed as the `HumanMessage` content, not a system prompt field.

---

## Q&A Accumulation

All rounds are concatenated in order and passed to every draft call:

```
--- Round 1 ---
Questions:
<question from iteration 0>

PM's answer:
<answer from iteration 0>

--- Round 2 ---
Questions:
<question from iteration 1>

PM's answer:
<answer from iteration 1>
```

On REWORK, the Drafter rewrites the full section from scratch using all accumulated rounds — it does not append to the previous draft. `current_draft` from the prior attempt is not injected.

---

## Inline Flag Semantics

Three flags surface problems visibly in the output rather than smoothing them over:

| Flag | When to use |
|---|---|
| `[NEEDS CLARIFICATION: <decision>]` | Critical information is missing; the section cannot be written without it |
| `[ASSUMPTION: <statement>]` | Draft proceeds with incomplete information; assumption must be visible |
| `[CONFLICT: <contradiction>]` | Draft output contradicts or duplicates a prior section |

**Reflector behaviour:** the IMPLEMENTABILITY rubric explicitly fails on unresolved `[ASSUMPTION]` flags, causing a REWORK loop so the PM can resolve them.

**Logging:** `assumption_count = draft.upper().count("[ASSUMPTION]")`. When `assumption_count > 3`, logs `drafter_high_assumptions` WARNING.

---

## Drafter Writing Rules

- Write only from confirmed Q&A — no inventing decisions, thresholds, or policy rules.
- Do not include the section heading in output.
- Do not contradict or duplicate prior sections.
- Do not infer beyond explicitly confirmed information.
- Ignore ambiguous or non-committal PM responses.
- Use structured formatting (numbered lists, bullet points) where appropriate.

---

## Warning Log Events

| `event_type` | Level | Condition |
|---|---|---|
| `drafter_empty_output` | WARNING | LLM returns empty string |
| `drafter_high_assumptions` | WARNING | `assumption_count > 3` |

See `skills/logging/SKILL.md` for the full event inventory.

---

## Do / Don't

| Do | Don't |
|---|---|
| Pass all Q&A rounds in the `HumanMessage` | Pass only the latest round — prior rounds contain decisions the new draft depends on |
| Use inline flags to surface missing or conflicting information | Smooth over gaps with invented policy decisions |
| Keep `"type": "draft"` in the `chat_history` item | Change the type key — `app.py` renders draft items based on this value |
| Write section body without a heading | Include the section title as a heading — `finalize_node` adds it |
| Let the Reflector flag unresolved `[ASSUMPTION]` items | Pre-resolve assumptions on behalf of the PM |
