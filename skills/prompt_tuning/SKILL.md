---
name: prompt-tuning
description: "Reference for safely modifying prompts in templates.py. Identifies which output format strings are load-bearing for downstream regex parsing, which changes are safe, how to update thresholds correctly, and how to add new rubrics or context blocks."
---

# Prompt Tuning

Several parts of `prompts/templates.py` produce text that is parsed by regex in `graph/nodes.py`. Changing the LLM's output format — even slightly — silently breaks parsing and corrupts state without raising an exception. This skill documents what is safe to change and what is not.

---

## Scope

| Item | Location |
|---|---|
| All prompt templates | `prompts/templates.py` |
| All parsing logic | `graph/nodes.py` — `reflect_node`, `_parse_rubric_score()` |

See `skills/prd-writing/SKILL.md` for threshold constants.  
See `skills/context_assembly/SKILL.md` for adding new context blocks.

---

## Parsing-Dependent Output Contracts

The following output strings are parsed by regex. Treat them as contracts.

### 1. VERDICT line — `reflect_node`

**Parser:** scans from the bottom of the reflector response line by line.

**Required format — exactly one of:**
```
VERDICT: PASS
VERDICT: REWORK - <reason>
```

**Parse failure default:** `"REWORK"` — spurious REWORK wastes an iteration but does not corrupt the PRD.

---

### 2. TRIAGE line — `reflect_node`

**Parser:** forward scan through the full reflector response.

**Required format — exactly one of:**
```
TRIAGE: ENTER RECOVERY MODE
TRIAGE: NORMAL ITERATION
```

**Parse failure default:** `"TRIAGE: NORMAL ITERATION"` — a section that should enter recovery mode will not, potentially advancing without intensifying its recovery strategy.

---

### 3. OVERALL SCORE — `reflect_node`

**Regex:**
```python
re.search(r"OVERALL\s+SCORE[^\d]*(\d+\.?\d*)\s*/\s*10", reflection_text, re.IGNORECASE)
```

**Tolerates:** em-dash, colon, space, markdown bold, varied spacing.

**Parse failure:** returns `-1.0`, disabling programmatic threshold enforcement. A section scoring below `PASS_SCORE_THRESHOLD` (8.5) could receive a spurious PASS.

---

### 4. Per-rubric scores — `_parse_rubric_score()`

**Regex:**
```python
re.search(rf"{re.escape(rubric)}[^\d\n]*(\d+\.?\d*)\s*/\s*10", text, re.IGNORECASE)
```

**Required format (any non-digit separator accepted):**
```
COMPLETENESS <separator> X.X/10
SPECIFICITY <separator> X.X/10
INTERNAL CONSISTENCY <separator> X.X/10
IMPLEMENTABILITY <separator> X.X/10
```

**Parse failure:** score is `-1.0`; logged as `reflect_parse_warning`; does not affect routing but corrupts eval CSV data.

---

### 5. REQUIREMENT GAPS block — `reflect_node`

**Regex:**
```python
re.search(
    r"REQUIREMENT GAPS\b.*?\n(.*?)(?=TRIAGE DECISION|\Z)",
    reflection_text,
    re.DOTALL | re.IGNORECASE,
)
```

Content between `REQUIREMENT GAPS` and `TRIAGE DECISION` (or end of response) is captured. If the heading is renamed or reordered, `requirement_gaps` is empty and the next Elicitor iteration generates a generic question. Logs `reflect_missing_gaps` WARNING.

---

### 6. RESOLVED / UNRESOLVED markers — `reflect_node`

**Regex:**
```python
re.findall(r"[-•*]\s*RESOLVED:", reflection_text, re.IGNORECASE)
re.findall(r"[-•*]\s*UNRESOLVED:", reflection_text, re.IGNORECASE)
```

REQUIREMENT STATUS items must be prefixed with `-`, `•`, or `*` followed by `RESOLVED:` or `UNRESOLVED:`. Parse failure only corrupts log counts — routing is unaffected.

---

## Required Output Section Order in `REFLECTOR_SYSTEM`

The REQUIREMENT GAPS regex terminates on `TRIAGE DECISION`. **This order must not change:**

```
1. COMPLETENESS — score
2. SPECIFICITY — score
3. INTERNAL CONSISTENCY — score
4. IMPLEMENTABILITY — score
5. OVERALL SCORE — score
6. REQUIREMENT STATUS
7. REQUIREMENT GAPS      ◄── captured until...
8. TRIAGE DECISION       ◄── ...this heading
   VERDICT: PASS|REWORK  ◄── must be the last line
```

---

## Safe to Change

| What | Why safe |
|---|---|
| Wording of any rubric description | Parsed output format is unchanged |
| Wording of `GLOBAL_RIGOR_BLOCK`, `HUMAN_TRUST_BLOCK`, etc. | No regex depends on their content |
| `section.specificity_guidance` | Injected as prose; not parsed |
| Score interpretation bands in `SCORING_INTERPRETATION_BLOCK` | LLM guidance only; routing uses Python constants |
| Question wording in `ELICITOR_SYSTEM` | Not parsed |
| Assumption/trade-off framing in `CLARIFICATION_CONTROLLER_SYSTEM` | Not parsed |
| `DRAFTER_SYSTEM` instruction wording | Not parsed |
| Inline flag labels `[NEEDS CLARIFICATION]`, `[ASSUMPTION]`, `[CONFLICT]` | Counted by `.count()` for logging only; no routing impact |
| Optional context blocks | Provided all `{format_fields}` are supplied to `.format()` |

---

## Updating Numeric Thresholds

Python constants always control routing. The prompt text is LLM guidance only.

When changing a threshold, update **all three** in sync:
1. The Python constant in `prompts/templates.py`
2. The description in `SCORING_INTERPRETATION_BLOCK`
3. The `VERDICT: PASS` rule in `REFLECTOR_SYSTEM` ("Only output VERDICT: PASS if the OVERALL SCORE is X or above")

---

## Adding a New Rubric

The system currently has 4 rubrics. To add a 5th:

1. Add a rubric prose block to `REFLECTOR_SYSTEM` under `━━ RUBRIC ━━`.
2. Add a numbered entry to the output format section in `REFLECTOR_SYSTEM`.
3. Add `_parse_rubric_score(reflection_text, "NEW_RUBRIC_NAME")` in `reflect_node`.
4. Add the new score to the `log_event(event_type="reflect_parsed", ...)` call.
5. Update `tests/eval_cases.py` expected score ranges if eval cases exist.

---

## Do / Don't

| Do | Don't |
|---|---|
| Change rubric prose and scoring guidance freely | Rename `VERDICT`, `TRIAGE`, `REQUIREMENT GAPS`, or `OVERALL SCORE` output headings |
| Update Python threshold constants AND matching prompt guidance together | Change only the prompt threshold text and expect routing to follow |
| Guard new context block fields with conditionals in the node | Add `{new_field}` to a template without updating the `.format()` call |
| Test rubric score parsing after changing reflector output format | Assume the LLM will use the separator the regex expects |
