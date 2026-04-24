---
name: answer-resolution
description: Determines whether a user reply satisfies the active question. Use when interpreting vague answers, mapping free-text replies to known options, detecting branch choices, handling partial answers, or deciding if clarification is needed.
---

# Answer Resolution Skill

Use this skill whenever the chatbot receives a user reply and must determine whether that reply actually answers the current active question.

## Core responsibility

This skill owns reply interpretation only.

It decides:

- Did the user answer the question?
- Was the answer full, partial, unclear, contradictory, or invalid?
- Which option or branch did the user choose?
- Is clarification required before proceeding?

This skill does **not** own question status transitions or next-question generation. It only returns a resolution judgment for downstream routing.

## Resolution outputs

Return one of these normalized outcomes:

1. `FULLY_RESOLVED`  
   Reply sufficiently answers the active question.

2. `PARTIALLY_RESOLVED`  
   Reply contains useful signal but still misses a key detail.

3. `BRANCH_RESOLVED`  
   Reply selects one known option or path.

4. `UNCLEAR`  
   Reply is too vague, off-topic, or ambiguous.

5. `CONTRADICTORY`  
   Reply conflicts with earlier confirmed facts or itself.

6. `INVALID_VALUE`  
   Reply contains impossible, malformed, or suspicious values.

7. `CLARIFICATION_REQUEST`  
   User is asking what the question means instead of answering it.

## When to use this skill

Use this skill when:

- A user replies to the current question
- The reply is short or vague
- The user chooses between options
- Free-text must be mapped to a known branch
- Numeric or date values need sanity checking
- The user appears confused
- Semantic matching is needed

## Resolution process

### 1. Identify active question intent

First determine what kind of question was asked:

- numeric estimate
- yes/no
- multiple choice
- process description
- prioritization
- timeline
- ownership
- clarification branch

Do not judge the answer without considering question type.

### 2. Attempt exact match first

Use lightweight deterministic matching:

- exact option text
- clear yes/no
- direct numeric response
- named owner/date/tool/etc.

Prefer exact matching when possible.

### 3. Apply synonym mapping

Recognize common variants.

Examples:

- “mapping creation” = create mappings
- “the mapping part” = product mapping creation
- “pdf sending” = retrieval and sending PDFs
- “approx 3 hrs” = 3 hours

Use canonical option IDs internally.

### 4. Use semantic fallback only if needed

If no exact match:

- infer likely meaning from context
- compare intent, not wording
- keep confidence conservative

If confidence is low, return `UNCLEAR`.

### 5. Validate plausibility

Check obvious sanity constraints.

Examples:

- 30 hours per day → `INVALID_VALUE`
- negative duration
- impossible dates
- impossible percentages

Use deterministic checks before LLM inference where possible.

### 6. Detect clarification requests

If user says:

- “What do you mean?”
- “Can you explain?”
- “Which trigger?”
- “I don’t understand the question.”

Return `CLARIFICATION_REQUEST`.

## Confidence rules

### High confidence

Use `FULLY_RESOLVED` or `BRANCH_RESOLVED` only when clear.

### Medium confidence

Use `PARTIALLY_RESOLVED` if some useful detail exists.

### Low confidence

Use `UNCLEAR` and ask one tighter follow-up.

When uncertain, prefer asking rather than assuming.

## Good examples

### Example 1: Full resolution

Question: “How long does this take each day?”  
User: “Around 3 hours daily.”

Output:

- `FULLY_RESOLVED`
- normalized_value = `3 hours/day`

### Example 2: Branch resolved

Question: “Is the problem mapping creation or PDF retrieval?”  
User: “The mapping part.”

Output:

- `BRANCH_RESOLVED`
- option_id = `MAPPING_CREATION`

### Example 3: Partial resolution

Question: “Who performs this process?”  
User: “Usually operations.”

Output:

- `PARTIALLY_RESOLVED`
- missing exact team/person

### Example 4: Invalid value

Question: “How long per day?”  
User: “30 hours per day.”

Output:

- `INVALID_VALUE`

### Example 5: Clarification request

Question: “What triggers the workflow?”  
User: “What do you mean by trigger?”

Output:

- `CLARIFICATION_REQUEST`

## Guardrails

- Do not mark vague answers as fully resolved
- Do not over-trust semantic guesses
- Do not invent values
- Do not ignore impossible numeric responses
- Do not confuse clarification requests with answers
- Prefer deterministic logic before LLM inference

## Performance target

Default runtime path should be fast.

Use this order:

1. exact match  
2. synonym map  
3. rule checks  
4. semantic fallback only if needed

Target normal resolution path: **sub-second internal decisioning**.

## Success criteria

This skill is working well when:

- clear answers resolve quickly
- vague answers trigger focused follow-up
- branch choices are recognized reliably
- typo/impossible values are intercepted
- clarification requests are handled correctly
- repeated misunderstanding loops decrease