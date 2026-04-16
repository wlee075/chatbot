---
name: reflection
description: "Documents the reflect_node and REFLECTOR_SYSTEM. Covers 4-rubric scoring, OVERALL SCORE parsing, TRIAGE extraction, programmatic verdict and triage overrides, recovery counter logic, and score parse failure handling."
---

# Reflection

The Reflector evaluates the current section draft against 4 rubrics and produces a structured review including scores, requirement status, gaps, a triage decision, and a final verdict. It is the only node that gates section advancement.

---

## Scope

| Item | Location |
|---|---|
| Node | `reflect_node` in `graph/nodes.py` |
| Prompt | `REFLECTOR_SYSTEM` in `prompts/templates.py` |

See `skills/prompt_tuning/SKILL.md` for parsing contracts and output format rules.  
See `skills/routing/SKILL.md` for how verdict and triage drive routing.  
See `skills/prd-writing/SKILL.md` for threshold constants.

---

## Inputs and Outputs

**State fields consumed:**

| Field | Use |
|---|---|
| `section_index` | Identifies which `PRDSection` is under review |
| `current_draft` | Draft text passed as `HumanMessage` |
| `prd_sections` | Built into `prior_sections_block` for consistency checking |

**State fields written:**

| Field | Source |
|---|---|
| `reflection` | Full raw LLM response |
| `verdict` | Parsed + possibly overridden |
| `triage_decision` | Parsed + possibly overridden |
| `requirement_gaps` | Extracted section 7 (REQUIREMENT GAPS) of reflector output |
| `overall_score` | Parsed OVERALL SCORE; `-1.0` on parse failure |
| `iteration` | Incremented by 1 on REWORK; unchanged on PASS |
| `recovery_mode_consecutive_count` | Updated per counter logic below |
| `chat_history` | Appends `{"type": "reflect", "verdict": ..., "overall_score": ...}` |

---

## The 4 Rubrics

| # | Name | State field | Checks |
|---|---|---|---|
| 1 | COMPLETENESS | `completeness_score` | All `section.expected_components` addressed |
| 2 | SPECIFICITY | `specificity_score` | No vague qualifiers; guided by `section.specificity_guidance` |
| 3 | INTERNAL CONSISTENCY | `consistency_score` | No contradictions or duplications vs prior sections |
| 4 | IMPLEMENTABILITY | `implementability_score` | Downstream team can implement without guessing |

All scores: `0.0–10.0`, one decimal place.

**Specificity vague-word list:** `improve`, `better`, `smart`, `enhanced`, `appropriate`, `flexible`, `as needed`, `low confidence`, `borderline`. Each requires a threshold, rule, or condition.

---

## Required Output Format

See `skills/prompt_tuning/SKILL.md` for full parsing contracts. Section order must not change:

```
1. COMPLETENESS — X.X/10
2. SPECIFICITY — X.X/10
3. INTERNAL CONSISTENCY — X.X/10
4. IMPLEMENTABILITY — X.X/10
5. OVERALL SCORE — X.X/10
6. REQUIREMENT STATUS
   - RESOLVED: <decision>
   - UNRESOLVED: <gap> — <why>
7. REQUIREMENT GAPS
   <numbered decision questions>
8. TRIAGE DECISION
TRIAGE: ENTER RECOVERY MODE | TRIAGE: NORMAL ITERATION
VERDICT: PASS | VERDICT: REWORK - <reason>
```

---

## Triage Logic

| Condition | Output |
|---|---|
| `resolved_components < 50%` OR `OVERALL SCORE < 5.0` | `TRIAGE: ENTER RECOVERY MODE` |
| Otherwise | `TRIAGE: NORMAL ITERATION` |

`resolved_components` = fraction of `section.expected_components` classified as RESOLVED in section 6. The `5.0` threshold matches `RECOVERY_MODE_SCORE_THRESHOLD` in `prompts/templates.py`.

---

## Programmatic Threshold Enforcement

Applied after parsing, independent of LLM output. Fires only when `overall_score >= 0.0`.

**Verdict override:**
```python
if verdict == "PASS" and overall_score < PASS_SCORE_THRESHOLD:
    verdict = "REWORK"   # logged as reflect_override WARNING
```

**Triage override:**
```python
if overall_score < RECOVERY_MODE_SCORE_THRESHOLD:
    triage_decision = "TRIAGE: ENTER RECOVERY MODE"   # logged as reflect_override WARNING if changed
```

When `overall_score == -1.0` (parse failure), the LLM's raw VERDICT and TRIAGE are used unchanged.

---

## Counter Logic

**`iteration`**
- REWORK: incremented by 1
- PASS: unchanged (reset to 0 by `advance_section_node`)

**`recovery_mode_consecutive_count`**

| Outcome | New count |
|---|---|
| PASS | 0 |
| REWORK + NORMAL ITERATION | 0 |
| REWORK + ENTER RECOVERY MODE | `current_count + 1` |

When `recovery_mode_consecutive_count >= DEFAULT_MAX_RECOVERY_MODE_CONSECUTIVE_ITERATIONS` (2), routing advances the section regardless of verdict.

---

## Score Parse Failures

| Field | Failure value | Impact |
|---|---|---|
| `overall_score` | `-1.0` | Disables programmatic enforcement; LLM verdict/triage used as-is |
| Per-rubric scores | `-1.0` | Logged as `reflect_parse_warning`; no routing impact; corrupts eval CSV |

One `reflect_parse_warning` is logged per failed field.

---

## Adversarial Review Posture

`REFLECTOR_SYSTEM` instructs the LLM to assume the draft is flawed unless proven otherwise, prefer false negatives over false positives, and not be generous. This is intentional — the Reflector is the only quality gate.

---

## Do / Don't

| Do | Don't |
|---|---|
| Keep `VERDICT:` as the last line of the output | Move VERDICT before TRIAGE — the parser scans from the bottom |
| Keep `TRIAGE DECISION` as the heading before VERDICT | Rename the REQUIREMENT GAPS heading — the gaps regex terminates on `TRIAGE DECISION` |
| Update `SCORING_INTERPRETATION_BLOCK` when changing threshold constants | Change only the prompt threshold text without updating the Python constant |
| Log both `llm_verdict` and `enforced_verdict` in `node_end` | Skip logging overrides — `reflect_override` is the audit trail |
| Default triage parse failure to `NORMAL ITERATION` | Default to RECOVERY MODE on parse failure — triggers spurious recovery escalation |
