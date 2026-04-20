---
name: prompt-refinement
description: Refines prompts in templates.py for clarity, UX, and reliability. Use when improving prompt wording, tightening output contracts, reducing repetition, preventing robotic phrasing, or deciding whether a problem should be fixed in prompts versus code/state.
---

# Prompt Refinement Skill

When refining prompts in `templates.py`, follow these steps:

## Review checklist

1. **Responsibility**: Does each prompt block do one job only?
2. **User experience**: Does user-facing output sound natural, plain-English, and helpful?
3. **Contract clarity**: Are output requirements explicit and easy for the model to follow?
4. **Repetition risk**: Could the prompt encourage broad, repeated, or multi-question asks?
5. **Prompt vs code boundary**: Is this truly a prompt problem, or should it be fixed in code/state?
6. **Simplicity**: Is the prompt over-constrained, duplicated, or bloated?
7. **Reliability**: Could the current wording cause parser failures, fallback drift, or ambiguous outputs?

## How to use it

### 1. Find the single highest-priority prompt problem

Do not rewrite the whole file at once.

Start by identifying the most harmful prompt-level issue, such as:
- internal evaluator language leaking to the user
- too many questions in one turn
- parroting the user instead of answering
- broad or repetitive questioning
- prompt bloat causing structured-output failures

### 2. Locate the responsible block(s)

Pinpoint exactly which block(s) in `templates.py` are contributing to the problem.

Common candidates:
- `ELICITOR_SYSTEM`
- `ELICITOR_ITERATION_BLOCK`
- `CLARIFICATION_ANSWER_PROMPT`
- `ECHO_INTERPRET_PROMPT`
- `INTENT_FALLBACK_CLASSIFICATION_PROMPT`
- `REFLECTOR_SYSTEM`

Do not blame the whole file if only one or two blocks are involved.

### 3. Decide whether this is really a prompt fix

Before editing prompt text, decide whether the issue belongs in:
- prompt wording
- prompt scope split
- output contract tightening
- deterministic code/state, not prompt

Examples:
- repeated questions after a valid answer are often a **state/lifecycle** bug first
- internal jargon shown to the user is often a **prompt wording** problem
- multi-question output may need both **prompt tightening** and **backend enforcement**

If it belongs in code/state, say so clearly.

### 4. Make the smallest high-leverage edit

Prefer small, precise edits over broad rewrites.

Good:
- tighten one output rule
- remove one vague instruction
- split one overloaded prompt responsibility
- replace one robotic phrase with plain-English guidance

Bad:
- rewrite every block
- add five more rules without removing anything
- hide a state bug with nicer wording

### 5. Keep prompts focused

Each prompt block should do one thing well.

Examples:
- classification prompt should classify
- clarification prompt should explain and re-ask simply
- elicitor prompt should ask one focused next question
- reflector prompt should evaluate draft quality

Do not let one prompt handle classification, UX rewriting, and state repair at the same time.

### 6. Protect one-question-per-turn behavior

When reviewing any user-facing question prompt:
- ensure it asks only one primary question
- remove instructions that encourage checklists or stacked asks
- prefer one high-leverage uncovering question when many details are missing
- use direct slot-filling only when 1–2 precise gaps remain

### 7. Rewrite internal language into plain English

User-facing prompts must not sound like evaluator output.

Avoid phrasing like:
- contradictory
- ambiguous
- undefined
- unmeasurable
- implementation blocker
- we need to align on this detail

Prefer:
- one clear explanation of what is still unclear
- one focused next question
- natural colleague-like language

### 8. Reduce duplication across shared blocks

If multiple shared blocks say the same thing, recommend consolidation.

Examples:
- repeated one-question-per-turn rules
- repeated anti-jargon rules
- repeated “do not invent” instructions

Only consolidate when it improves clarity without weakening enforcement.

### 9. Watch for parser-failure risk

Prompts can become too long, too contradictory, or too constrained.

If structured output is failing:
- simplify
- remove overlapping instructions
- tighten format expectations
- avoid mixing too many goals in one prompt

Do not “fix” parser failures by dumping more instructions into the prompt.

## Decision tree

### Case 1: User-facing output sounds robotic or evaluative
- Review `CLARIFICATION_ANSWER_PROMPT`, `ELICITOR_SYSTEM`, and `ECHO_INTERPRET_PROMPT`
- Tighten wording toward plain English
- Remove internal-review phrasing

### Case 2: Model asks too many questions at once
- Review `ELICITOR_SYSTEM` and `ELICITOR_ITERATION_BLOCK`
- Tighten output contract to one question only
- Check whether backend should also enforce this

### Case 3: Model parrots the user
- Review `CLARIFICATION_ANSWER_PROMPT` and `ECHO_INTERPRET_PROMPT`
- Ensure clarification questions are answered directly, not echoed back

### Case 4: Model repeats already-answered questions
- First determine whether this is mainly a code/state issue
- Only refine prompt wording if the prompt encourages broad re-asks

### Case 5: Prompt is too slow or brittle
- Look for bloated instructions, duplicated blocks, and conflicting goals
- Simplify before adding more constraints

## How to provide feedback

- Identify the exact block causing the problem
- Explain whether the fix belongs in prompt or code/state
- Suggest the smallest viable edit
- Show before/after text only for the affected block(s)
- Explain expected behavior change
- Call out risks or regressions
- Recommend targeted tests

## Good output pattern

For each refinement pass, provide:

1. **Highest-priority issue**
2. **Where it lives**
3. **Fix type**
4. **Proposed edit**
5. **Why this is better**
6. **Risks**
7. **Tests to run**
8. **Merge recommendation**

## Guardrails

- Do not rewrite the whole file without reason
- Do not hide state-machine bugs with prompt polish
- Do not add instructions blindly
- Do not let prompts grow without bound
- Keep user-facing prompts natural and short
- Prefer focused edits over sprawling rewrites