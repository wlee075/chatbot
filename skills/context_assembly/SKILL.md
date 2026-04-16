---
name: context-assembly
description: "Documents how prompt context blocks are conditionally assembled and injected in each node. Covers conditional guards, format field contracts, the _format_prd_so_far helper, and the rules for adding new context blocks."
---

# Context Assembly

Each node assembles only the context blocks relevant to the current section, iteration, and available state. This skill documents the exact assembly logic per node so changes are deliberate and auditable.

---

## Scope

| Item | Location |
|---|---|
| Assembly logic | `generate_questions_node`, `draft_node`, `reflect_node` in `graph/nodes.py` |
| Block templates | `ELICITOR_CONTEXT_BLOCK`, `ELICITOR_PRD_BLOCK`, `ELICITOR_ITERATION_BLOCK`, `DRAFTER_PRD_CONTEXT_BLOCK`, `DRAFTER_CONTEXT_DOC_BLOCK`, `REFLECTOR_PRIOR_SECTIONS_BLOCK` in `prompts/templates.py` |

See `skills/prd-writing/SKILL.md` for PRDState fields and global prompt block definitions.

---

## Assembly Pattern

Every LLM-calling node follows this sequence:

1. Read relevant state fields.
2. Conditionally render each named block (`""` when not applicable).
3. Call `PromptTemplate.format(**all_fields)` — missing fields raise `KeyError`; extra fields are silently ignored.
4. Invoke LLM with `[SystemMessage(content=system_prompt), HumanMessage(content=task_instruction)]`.

Blocks that evaluate to `""` render as blank — no visible section, no whitespace artefacts.

---

## Elicitor — `generate_questions_node`

### `context_block`

```python
context_block = (
    ELICITOR_CONTEXT_BLOCK.format(context_doc=state["context_doc"])
    if state.get("context_doc") else ""
)
```

Injected when a background document was uploaded. Enables domain-aware questions.

### `prd_block`

```python
prd_so_far = _format_prd_so_far(state.get("prd_sections", {}))
prd_block = ELICITOR_PRD_BLOCK.format(prd_so_far=prd_so_far) if prd_so_far else ""
```

Injected once at least one section is complete. Prevents duplicate or contradictory questions.

### `iteration_block`

```python
if iteration > 0 and state.get("reflection"):
    iteration_block = ELICITOR_ITERATION_BLOCK.format(
        iteration=iteration + 1,
        max_iterations=...,
        reflection=state["reflection"],
        requirement_gaps=raw_gaps or "None identified. Refer to reflection feedback above.",
        triage_decision=state.get("triage_decision", "TRIAGE: NORMAL ITERATION"),
    )
else:
    iteration_block = ""
```

Injected only when both `iteration > 0` AND `reflection` is non-empty.

**Full injection map:**

| Format field | Present when |
|---|---|
| `section_title`, `section_description`, `expected_components_list` | Always |
| `context_block` | `state["context_doc"]` non-empty |
| `prd_block` | At least one prior section complete |
| `iteration_block` | `iteration > 0` AND `state["reflection"]` truthy |
| `global_rigor_block`, `decision_enforcement_block`, `iteration_discipline_block`, `human_trust_block` | Always |

---

## Drafter — `draft_node`

### `prd_context_block`

```python
prd_context_block = (
    DRAFTER_PRD_CONTEXT_BLOCK.format(prd_so_far=prd_so_far) if prd_so_far else ""
)
```

Prevents the draft from contradicting or duplicating completed sections.

### `context_doc_block`

```python
context_doc_block = (
    DRAFTER_CONTEXT_DOC_BLOCK.format(context_doc=state["context_doc"])
    if state.get("context_doc") else ""
)
```

Provides background domain context for writing style.

### Q&A block (inline, not a named template)

```python
for i, qa in enumerate(state.get("section_qa_pairs", []), 1):
    qa_parts.append(
        f"--- Round {i} ---\n"
        f"Questions:\n{qa['questions']}\n\n"
        f"PM's answer:\n{qa['answer']}"
    )
```

All Q&A rounds are concatenated in order and passed as the `HumanMessage` content.

**Full injection map:**

| Format field | Present when |
|---|---|
| `section_title`, `section_description`, `expected_components_list` | Always |
| `prd_context_block` | At least one prior section complete |
| `context_doc_block` | `state["context_doc"]` non-empty |
| `global_rigor_block` | Always |

---

## Reflector — `reflect_node`

### `prior_sections_block`

```python
prior_sections_block = (
    REFLECTOR_PRIOR_SECTIONS_BLOCK.format(prd_so_far=prd_so_far)
    if prd_so_far else "No prior sections yet."
)
```

Never `""` — falls back to the literal string `"No prior sections yet."`.

**Full injection map:**

| Format field | Present when |
|---|---|
| `section_title` | Always |
| `prior_sections_block` | Always (fallback string if no prior sections) |
| `expected_components_list` | Always |
| `specificity_guidance` | Always (from `section.specificity_guidance`) |
| `global_rigor_block`, `scoring_interpretation_block` | Always |

The current draft is passed as the `HumanMessage` content.

---

## `_format_prd_so_far` Helper

```python
def _format_prd_so_far(prd_sections: dict) -> str:
    if not prd_sections:
        return ""
    parts = []
    for section in PRD_SECTIONS:       # canonical order
        if section.id in prd_sections:
            parts.append(f"## {section.title}\n{prd_sections[section.id]}")
    return "\n\n".join(parts)
```

Iterates `PRD_SECTIONS` in canonical order regardless of dict insertion order. Returns `""` when dict is empty so callers can use `if prd_so_far` as a guard.

---

## Adding a New Context Block

1. Define the template string in `prompts/templates.py`.
2. Add the `{new_block}` field to the relevant `AGENT_SYSTEM` template.
3. Build the block conditionally in the node and pass it to `.format(new_block=...)`.
4. Import the new constant in `nodes.py`.

A `{field}` added to a prompt template without a matching `.format()` key raises `KeyError` at invocation time.

---

## Do / Don't

| Do | Don't |
|---|---|
| Guard optional blocks with an `if` before calling `.format()` | Pass `None` as a format field — use `""` as the falsy default |
| Iterate via `PRD_SECTIONS` in `_format_prd_so_far` to preserve section order | Build `prd_so_far` from `prd_sections.items()` — dict order is not canonical |
| Verify all `{format_fields}` are supplied when adding a new block | Add more than one `HumanMessage` — nodes use a two-message list |
