---
name: clarification-control
description: "Evaluates a PM response against a single requirement gap and decides whether it is resolved. Covers CLARIFICATION_CONTROLLER_SYSTEM, CONFIRMATION_RULE_BLOCK, three-output decision logic, and max-attempts fallback."
---

# Clarification Control

The Clarification Controller evaluates a single PM response against a single requirement gap and classifies it as resolved, needing clarification, or deferred. It is the primary guard against vague or hedged answers entering the PRD.

---

## Scope

| Item | Location |
|---|---|
| Prompt | `CLARIFICATION_CONTROLLER_SYSTEM` in `prompts/templates.py` |
| Shared block | `CONFIRMATION_RULE_BLOCK` in `prompts/templates.py` |

No dedicated graph node in the current build — prompt-only agent.  
See `skills/prd-writing/SKILL.md` for constants and graph topology.

---

## Prompt Format Fields

| Field | Value |
|---|---|
| `requirement_gap` | The specific unresolved requirement question |
| `pm_response` | The PM's raw answer text |
| `attempt_number` | Current attempt (1-based) |
| `max_attempts` | `DEFAULT_MAX_CLARIFICATION_ATTEMPTS_PER_REQUIREMENT` (3) |
| `global_rigor_block` | `GLOBAL_RIGOR_BLOCK` |
| `confirmation_rule_block` | `CONFIRMATION_RULE_BLOCK` |
| `human_trust_block` | `HUMAN_TRUST_BLOCK` |

---

## Three-Output Decision Logic

The controller outputs **exactly one** of three forms based on `attempt_number` vs `max_attempts`.

### Output 1 — Resolved

```
RESOLVED: <normalized decision>
```

The response yields a clear, actionable decision. The normalised decision must be implementable without follow-up.

### Output 2 — Not resolved, attempts remain (`attempt_number < max_attempts`)

```
Requirement needs more clarity. To clarify with PM.

Assumption: <current best working assumption>
Trade-off: <trade-off between proceeding with this assumption vs not>
Decision needed: Please confirm with a clear Yes if we should proceed with <assumption> as the working requirement.
```

Converts ambiguity into a binary confirmation request. Provides a concrete assumption for the PM to ratify.

### Output 3 — Not resolved, attempts exhausted (`attempt_number >= max_attempts`)

```
To be clarified by PM during product meeting.
```

Elevates the gap to offline resolution. Does not block the workflow.

---

## Resolution Standard

A response is resolved only when it yields a **clear, actionable decision** — one that an engineer, policy analyst, or operations owner can act on without a follow-up question. Rigor standard is set by `GLOBAL_RIGOR_BLOCK`: surface ambiguity explicitly; prefer failing over silently smoothing over.

---

## `CONFIRMATION_RULE_BLOCK`

Shared with `DECISION_NORMALIZER_SYSTEM`. Defines what counts as confirmed:

**Accepted:**
- A clear "Yes"
- An explicit option selection
- A specific rule statement normalisable into a decision

**Rejected (treated as unresolved):**
- "maybe", "depends", "for now", "probably", "something like that", "we can decide later"

A hedged answer is unresolved regardless of length.

---

## Max Attempts

`DEFAULT_MAX_CLARIFICATION_ATTEMPTS_PER_REQUIREMENT = 3` (in `prompts/templates.py`).

After 3 attempts, the gap is parked as "To be clarified in product meeting" and the workflow continues.

---

## Do / Don't

| Do | Don't |
|---|---|
| Adjust the assumption/trade-off framing in Output 2 | Change the exact strings `RESOLVED:` or `To be clarified by PM` — downstream parsers match these literally |
| Add new hedging phrases to the unresolved list | Accept hedged answers to keep the workflow moving |
| Change `max_attempts` constant (update all import sites) | Use Output 2 format when `attempt_number >= max_attempts` |
