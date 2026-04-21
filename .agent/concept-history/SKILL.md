---
name: concept-history
description: Tracks how concepts evolve across turns by maintaining statuses such as current, historical, negated, example_only, corrected, superseded, and conflicted. Use when updating cross-turn semantic state from message-level understanding.
---

# Concept History Skill

Use this skill when the chatbot needs to track how meaning changes over time across multiple user turns.

## Core responsibility

This skill owns **cross-turn concept lifecycle tracking**.

It determines:

- whether a concept is merely mentioned
- whether it is current
- whether it is historical
- whether it is negated
- whether it is example-only
- whether it was corrected
- whether it was superseded
- whether it is conflicted

This skill does **not** own raw extraction or next-question wording.

## When to use this skill

Use this skill when:

- a new `message_semantics` record arrives
- the chatbot must update concept state across turns
- the user corrects themselves
- old vs current meaning must be tracked
- contradictions need to be preserved before clarification
- canonical truth must remain protected

## Inputs required

Use:

- normalized concepts from `message_semantics`
- source message ids
- timestamps
- confidence
- candidate-level semantic cues
- existing `concept_history`

## What this skill should produce

A stable cross-turn concept tracker.

Recommended per-concept fields:

- `concept_key`
- `mentions`
- `source_message_ids`
- `status`
- `status_reason`
- `is_current`
- `is_negated`
- `is_historical`
- `is_example`
- `was_corrected`
- `superseded_by`
- `corrected_from`
- `last_seen_at`
- `last_transition_at`

## Supported statuses

Use explicit statuses such as:

- `mentioned`
- `current`
- `historical`
- `negated`
- `example_only`
- `superseded`
- `conflicted`
- `confirmed`

Do not collapse conflict into generic “not current.”

## Step-by-step process

### 1. Read the new message-level candidates

For each candidate from `message_semantics`, identify:
- concept key
- confidence
- semantic flags
- source message
- timestamps

### 2. Decide whether the concept is new or existing

If new:
- create a concept entry
- start as `mentioned` unless stronger rules apply

If existing:
- update mentions
- evaluate lifecycle transition

### 3. Apply lifecycle rules

Examples:

#### Mentioned → Current
Only when the new mention is an affirmative present-state assertion or later confirmed by answer-resolution logic.

#### Current → Historical
When the new mention clearly indicates past state.

#### Current → Negated
When the new mention clearly negates the concept.

#### Mentioned → Example Only
When the concept appears only as illustration or hypothetical suggestion.

#### Current → Superseded
When the user explicitly corrects or replaces it.

#### Any → Conflicted
When strong evidence points in incompatible directions and no correction resolves it yet.

### 4. Preserve correction links

If the user says:
- Not PRD, I meant PO

Then:
- PRD should be marked corrected/superseded
- PO should be linked as replacement
- the relationship must be auditable

### 5. Preserve scope where needed

A concept may be current only in some context.

Example:
- Ops uses Excel, finance uses SAP

Do not flatten these into one oversimplified state if scope matters.

Use optional qualifiers such as:
- owner_scope
- team_scope
- subject_scope

### 6. Do not promote to confirmed truth here

This skill updates semantic state.

It does **not** directly change canonical confirmed truth unless explicit confirmation/promotion rules are invoked elsewhere.

## Common cases

### Negation

Input:
- We do not use SAP anymore.

Expected:
- SAP becomes negated
- SAP not current

### Historical replacement

Input:
- Used to use Excel, now dashboard.

Expected:
- Excel historical
- dashboard current

### Explicit correction

Input:
- Not PRD, I meant PO.

Expected:
- PRD corrected/superseded
- PO current or mentioned, depending on commitment strength

### Example only

Input:
- For example we could use Lark.

Expected:
- Lark example_only
- not current

### Conflict

Input:
- We use SAP.
- Later: We do not use SAP anymore.
- Later: Actually finance still uses SAP.

Expected:
- preserve conflict or scoped coexistence until clarified

## Explainability requirements

For every state transition, the system should be able to say:

- what changed
- why it changed
- which message triggered it
- what the prior state was
- whether conflict remains

## Recommended audit logs

Emit:
- `concept_state_transition`
- `concept_negated`
- `concept_marked_historical`
- `concept_corrected`
- `concept_conflicted`
- `concept_promoted_to_confirmed_truth`

Each should include:
- concept key
- previous status
- new status
- trigger
- source message ids
- reason

## Guardrails

- Do not let extraction confidence alone create `current`
- Do not silently resolve conflict
- Do not promote examples into active truth
- Do not erase old meaning without an audit trail
- Do not confuse concept history with canonical truth

## Success criteria

This skill is working well when:

- concepts evolve cleanly across turns
- corrections are preserved and explainable
- current vs historical is tracked correctly
- conflicts are visible instead of flattened
- canonical truth remains protected until confirmed