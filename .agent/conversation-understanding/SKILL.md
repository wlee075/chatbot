---
name: conversation-understanding
description: Helps the chatbot understand messy user messages, track meaning across turns, handle corrections and contradictions, and safely decide what becomes confirmed truth. Use when working on extraction, semantic state, concept tracking, clarification logic, or explainability.
---

# Conversation Understanding Skill

Use this skill when the chatbot needs to understand what the user really means across multiple turns, especially when the user is vague, changes their mind, corrects themselves, or mixes current facts with examples or old information.

## What this skill is for

This skill helps the system do five things well:

1. understand messy user messages
2. track meaning over time
3. ask better follow-up questions
4. explain why it asked, updated, or refused something
5. keep weak or uncertain information out of confirmed truth

## What this skill is not for

This skill is not for:
- UI styling
- pure keyword extraction only
- generic chat summarization
- directly writing final draft truth from raw extraction

## Core idea

The system must know the difference between:

- “the user mentioned SAP”
- “the user currently uses SAP”
- “the user used SAP before”
- “the user said not SAP, use Oracle”
- “the user gave SAP only as an example”
- “SAP is confirmed truth for the draft”

If the system cannot tell those apart, it is not understanding the conversation.

## The 3 layers

Keep these layers separate.

### 1. Message understanding

This is what one user message means by itself.

Store things like:
- entities
- keywords
- phrases
- actions
- confidence
- negation
- historical cues
- example/hypothetical cues
- source spans

### 2. Concept history

This is how a concept changes across turns.

Examples:
- SAP was mentioned
- then negated
- then corrected to Oracle
- then later confirmed

This is where you track:
- current
- historical
- negated
- example_only
- corrected
- superseded
- conflicted

### 3. Confirmed truth

This is the small set of information trusted enough to affect drafts and downstream behavior.

Do not promote directly from extraction into confirmed truth.

## Decision tree

When a concept is extracted, ask:

### A. Was it actually mentioned clearly?
If no:
- do not store it as a strong concept

If yes:
- store it as a message-level candidate

### B. Is it negated, historical, or just an example?
If yes:
- record that in message understanding
- do not treat it as current by default

### C. Does it conflict with earlier meaning?
If yes:
- mark as conflicted or corrected
- do not silently overwrite

### D. Has the user clearly committed to it?
If no:
- keep it in concept history only

If yes:
- allow confirmation logic to promote it into confirmed truth

## What to build

### Per-message understanding

Each user message should produce a structured record.

Recommended fields:
- `message_id`
- `timestamp_utc`
- `raw_text`
- `candidates`
- `action_graph`

Each candidate should include:
- `surface`
- `normalized`
- `type`
- `confidence`
- `source_span`
- `is_negated`
- `is_historical`
- `is_example`

### Cross-turn concept history

Track one normalized concept across time.

Recommended fields:
- `concept_key`
- `mentions`
- `source_message_ids`
- `status`
- `status_reason`
- `was_corrected`
- `superseded_by`
- `corrected_from`
- `last_seen_at`
- `last_transition_at`

### Action understanding

Do not only extract nouns.

Try to capture:
- verb + object
- verb + object + destination

Examples:
- map invoices
- send PDF to mailbox
- retrieve report from SAP

If parsing is weak:
- degrade safely to phrase-level understanding
- do not invent action structure

## Statuses to support

Use explicit statuses such as:
- `mentioned`
- `current`
- `historical`
- `negated`
- `example_only`
- `superseded`
- `conflicted`
- `confirmed`

Do not collapse everything unclear into one generic bucket.

## How to handle common cases

### Negation
Input:
- “We do not use SAP anymore.”

Expected:
- SAP extracted
- marked negated
- not treated as current
- likely ask what is used now

### Historical mention
Input:
- “Used to use Excel, now dashboard.”

Expected:
- Excel marked historical
- dashboard marked current
- both preserved in concept history

### Correction
Input:
- “Not PRD, I meant PO.”

Expected:
- PRD marked corrected/superseded
- PO promoted in concept history
- confirmed truth changes only through explicit confirmation flow

### Example / hypothetical
Input:
- “For example we could use Lark.”

Expected:
- Lark extracted
- marked example_only
- not treated as current truth

### Mixed meaning in one message
Input:
- “Ops uses Excel, finance uses SAP.”

Expected:
- both concepts remain active
- ideally with scope/owner context

## Promotion rule

A concept should only affect confirmed truth if it is:

- current
- non-negated
- non-example
- non-conflicted
- tied to explicit answer/confirmation logic
- traceable to a source message

High extraction confidence alone is not enough.

## Explainability rule

The system must be able to answer:

- Why was this concept extracted?
- Why is it current?
- Why is it historical?
- Why was it not promoted?
- Why did this question get asked?
- Why did one concept replace another?

If it cannot answer those, the design is incomplete.

## Audit logs to emit

Recommended events:
- `semantic_extraction_observability`
- `concept_state_transition`
- `concept_negated`
- `concept_marked_historical`
- `concept_corrected`
- `concept_promoted_to_confirmed_truth`

Each should include:
- concept
- source message(s)
- trigger
- reason
- confidence

## Conservative failure behavior

When unsure:
- keep concept tentative
- ask clarification
- refuse promotion
- show plain text / no chip if needed

Never:
- fake certainty
- flatten contradictions
- silently promote examples into truth

## Build order

### Phase 1
Per-message understanding:
- extraction
- candidate confidence
- negation/history/example cues
- action graph basics

### Phase 2
Concept history:
- lifecycle states
- correction
- supersession
- conflict handling

### Phase 3
Confirmed truth boundary:
- strict promotion rules
- no direct promotion from extraction

### Phase 4
Explainability:
- semantic transition logs
- reason fields
- “why this question” support

### Phase 5
Provenance UI:
- evidence chips
- snippets
- timestamps

## Edge cases to test

At minimum:

- “We don’t use SAP anymore.”
- “Used to use Excel, now dashboard.”
- “Not PRD, I meant PO.”
- “For example we could use Outlook.”
- “Ops uses Excel, finance uses SAP.”
- “Need send PDF to SG finance team lah.”
- “mailbo”
- “group mailbox -> shared inbox -> distro list”

For each one, verify:
- what was extracted
- what the semantic flags are
- what changed in concept history
- what stayed out of confirmed truth
- what next question changed
- what logs were emitted

## How to report progress

Report in product language.

Bad:
- spaCy integrated
- tests green
- noun chunks added

Good:
- system now distinguishes current vs historical tools
- system now prevents examples from becoming truth
- system now tracks corrections across turns
- system now explains why a concept was not promoted
- system now asks sharper follow-up questions

## Success criteria

A strong conversation understanding system should:

- survive messy human language
- track meaning across turns
- separate observation from commitment
- support better questions
- remain auditable
- protect confirmed truth from weak extraction