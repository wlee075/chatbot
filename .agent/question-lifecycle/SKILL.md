---
name: question-lifecycle
description: Manages the lifecycle of the active conversation question. Use when deciding whether a user reply resolves the current question, updating OPEN / ANSWERED / SUPERSEDED states, preventing repeated questions, and routing to the next step.
---

# Question Lifecycle Skill

Use this skill whenever the chatbot is handling an active question and needs to determine whether to keep asking, close the question, or move forward.

## Core responsibility

This skill owns the state of the current question only.

It decides:

- What question is currently active
- Whether the question is still OPEN
- Whether the user's reply ANSWERED it
- Whether the question should be SUPERSEDED by a better or narrower question
- Whether to ask the next question or continue workflow

## State model

Use these core fields:

- `active_question_id`
- `active_question_text`
- `question_status`
- `asked_at`
- `answered_at`
- `superseded_at`

Allowed statuses:

1. `OPEN`  
   Current question is awaiting a valid answer.

2. `ANSWERED`  
   User provided a satisfactory answer.

3. `SUPERSEDED`  
   Question is no longer relevant because a narrower, clearer, or newer question replaced it.

## When to use this skill

Use this skill when:

- The user replies to a question
- The assistant needs to know if the reply resolved the question
- The assistant is about to ask another question
- A duplicate question might be repeated
- A new question should replace the old one
- Clarification sub-flows reopen the current question

## Lifecycle rules

### 1. Only one active OPEN question at a time

There should never be multiple unresolved active questions unless the system explicitly supports batching.

### 2. A question must be closed before moving on

Before asking the next main question:

- mark current as `ANSWERED`, or
- mark current as `SUPERSEDED`

### 3. Do not silently overwrite questions

If replacing a question:

- old question becomes `SUPERSEDED`
- new question gets a new `active_question_id`

### 4. Suspicious or invalid answers do not close the question

Examples:

- impossible numeric values
- contradictory responses
- obvious typos
- “not sure”

Keep status as `OPEN` and trigger repair clarification.

### 5. Clarification requests do not answer the question

If user says:

- “What do you mean?”
- “Can you explain that?”
- “Which trigger?”

Answer clarification, then keep the question `OPEN`.

## Duplicate prevention

Before asking a new question:

1. Compare against current `active_question_text`
2. Compare against recent answered questions
3. If same intent already answered, do not repeat
4. Ask a narrower follow-up instead

## Next-step routing

After each user reply:

### If fully answered

- mark `ANSWERED`
- route to next question generation or drafting

### If partially answered

- keep `OPEN`
- ask one narrower follow-up

### If invalid / typo / impossible

- keep `OPEN`
- ask one repair question

### If no longer relevant

- mark `SUPERSEDED`
- ask replacement question

## Good examples

### Example 1: Answered

Question: “How long does the task take today?”  
User: “About 3 hours per day.”

Result:

- status = `ANSWERED`
- move to next question

### Example 2: Invalid answer

Question: “How long does it take?”  
User: “30 hours per day.”

Result:

- status = `OPEN`
- ask repair question

### Example 3: Clarification

Question: “What triggers the workflow?”  
User: “What do you mean by trigger?”

Result:

- explain term
- status remains `OPEN`

## Guardrails

- Never mark vague replies as answered automatically
- Never ask the exact same unresolved question repeatedly
- Never ask multiple broad questions at once
- Never lose track of which question is active
- Prefer one focused next step every turn

## Success criteria

This skill is working well when:

- users are not stuck in loops
- answered questions stay closed
- invalid replies trigger repair prompts
- each turn clearly advances the conversation
- state transitions are deterministic and traceable