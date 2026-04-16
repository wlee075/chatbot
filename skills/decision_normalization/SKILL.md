---
name: decision-normalization
description: "Converts a PM response to a requirement question into a canonical, implementable decision statement or marks it unresolved. Covers DECISION_NORMALIZER_SYSTEM, the RESOLVED vs UNRESOLVED two-output contract, and normalisation faithfulness rules."
---

# Decision Normalization

The Decision Normalizer converts an accepted PM response into a canonical, implementable requirement statement — or marks it unresolved. It is the final gate before a decision is stored in the PRD.

---

## Scope

| Item | Location |
|---|---|
| Prompt | `DECISION_NORMALIZER_SYSTEM` in `prompts/templates.py` |
| Shared blocks | `DECISION_ENFORCEMENT_BLOCK`, `CONFIRMATION_RULE_BLOCK` in `prompts/templates.py` |

See `skills/clarification_control/SKILL.md` for clarification retry logic and max-attempts fallback.  
See `skills/prd-writing/SKILL.md` for constants and graph topology.

---

## Prompt Format Fields

| Field | Value |
|---|---|
| `requirement_question` | The specific decision question being answered |
| `pm_response` | PM's raw answer text |
| `confirmation_rule_block` | `CONFIRMATION_RULE_BLOCK` (shared with clarification controller) |
| `human_trust_block` | `HUMAN_TRUST_BLOCK` |

---

## Two-Output Contract

The normalizer outputs **exactly one** of two forms.

### Resolved

```
RESOLVED: <normalized decision>
```

The normalised decision must be:
- Concise (one sentence preferred)
- Implementable without further interpretation
- In declarative form ("The system will…" / "Threshold is X" / "Feature Y is disabled by default")
- Faithful to the PM's actual response — no inference beyond what was said

### Unresolved

```
UNRESOLVED
```

Plain, no additional text. The requirement gap returns to the clarification controller for another attempt.

---

## What Makes a Response RESOLVED

| Type | Example |
|---|---|
| Binary | "Yes, we support manual override" |
| Option selection | "Use option B — silent discard" |
| Specific rule | "Accounts with >5 violations in 30 days are suspended" |
| Threshold | "Confidence score below 0.7 triggers manual review" |

If the PM says "probably option B", the response remains `UNRESOLVED` per `CONFIRMATION_RULE_BLOCK`.

---

## Normalisation Principles

1. **Faithfulness** — output the decision the PM actually made. If it is ambiguous, output `UNRESOLVED`, not a clarified version.
2. **Conciseness** — strip filler; restate as a direct requirement statement.
3. **No invention** — never add thresholds, conditions, or options the PM did not state.
4. **No synthesis** — normalise only the current response, not a combination of prior rounds.

---

## Related Blocks

**`DECISION_ENFORCEMENT_BLOCK`** — injected into the Elicitor (not the Normalizer). Requires all questions to be binary or option-choice. The Normalizer is its downstream mirror: if the PM's answer yields a binary or explicit selection, convert it to a clean declarative statement.

**`CONFIRMATION_RULE_BLOCK`** — shared with `CLARIFICATION_CONTROLLER_SYSTEM`. Treats anything hedged or non-committal (`"maybe"`, `"depends"`, `"for now"`, `"probably"`, `"something like that"`, `"we can decide later"`) as `UNRESOLVED` regardless of response length.

---

## Flow

```
PM answer
    │
    ▼
Clarification Controller ── RESOLVED ──► Decision Normalizer ──► stored decision
    │
    ├─ Not resolved, attempts < max ──► structured re-prompt ──► PM answers again
    └─ Not resolved, attempts exhausted ──► "To be clarified in meeting"
```

The Normalizer is invoked only after the Clarification Controller accepts the response as sufficiently clear.

---

## Do / Don't

| Do | Don't |
|---|---|
| Output exactly `RESOLVED: ...` or exactly `UNRESOLVED` | Add commentary after `UNRESOLVED` |
| Normalise to a declarative, implementable statement | Infer or fill in details the PM omitted |
| Faithfully represent the PM's actual decision | Output a "better" decision that improves on what was said |
| Return `UNRESOLVED` when the PM's response is hedged | Accept `"probably"` or `"for now"` to keep the workflow moving |
