---
name: message-understanding
description: Understands one user message in isolation by extracting entities, phrases, actions, semantic cues, and confidence. Use when processing a single user turn before updating cross-turn concept history or generating the next question.
---

# Message Understanding Skill

Use this skill when the chatbot needs to understand what one user message means on its own.

## Core responsibility

This skill owns **single-message interpretation only**.

It determines:

- what entities appear
- what workflow phrases appear
- what action patterns appear
- whether concepts are negated
- whether concepts are historical
- whether concepts are only examples or hypotheticals
- how confident the extraction is
- where in the source text the evidence came from

This skill does **not** own cross-turn lifecycle tracking, final truth promotion, or next-question selection.

## When to use this skill

Use this skill when:

- a new user message arrives
- the chatbot needs structured meaning from one turn
- negation/history/example cues may exist
- provenance spans are needed
- action extraction is needed
- concept confidence needs to be computed

## Inputs required

Use the smallest sufficient context:

- raw user message text
- message id
- timestamp
- optional domain allowlists / entity rules

Do not require full conversation history unless absolutely necessary.

## What this skill should produce

The output should describe one message only.

Recommended structure:

- `message_id`
- `timestamp_utc`
- `raw_text`
- `candidates`
- `action_graph`

Each candidate should ideally include:

- `surface`
- `normalized`
- `type`
- `confidence`
- `source_span`
- `is_negated`
- `is_historical`
- `is_example`

Each action graph edge should ideally include:

- `verb`
- `object`
- `destination_if_any`
- `confidence`
- `source_span`
- `extraction_method`

## Step-by-step process

### 1. Normalize safely

Prepare the message text while preserving original display text.

- normalize whitespace
- repair line-wrap damage where safe
- preserve offsets
- preserve original surface form

### 2. Extract entities

Prefer established tooling.

Examples:
- Excel
- SAP
- PDF
- Lark
- Hong Kong
- Singapore

### 3. Extract phrases and concepts

Capture:
- workflow phrases
- business objects
- pain points
- useful multi-word expressions

Examples:
- product mapping
- manual forwarding
- group mailbox

### 4. Extract action structure

Capture:
- verb + object
- verb + object + destination

Examples:
- send PDF
- map product
- forward email to mailbox

If parsing is weak:
- degrade to phrase-level understanding
- do not hallucinate structure

### 5. Detect semantic cues

At candidate level where possible:

- negated
- historical
- example/hypothetical

Do not rely only on message-wide flags.

A single message may contain:
- one historical concept
- one current concept
- one hypothetical example

### 6. Assign confidence

Confidence should reflect extraction strength.

Examples:
- exact entity / exact phrase > strong phrase > lemma-backed > fuzzy repair

High confidence does not mean confirmed truth. It only means extraction confidence.

## Edge cases

### Negation

Input:
- We do not use SAP anymore.

Expected:
- SAP extracted
- SAP marked negated

### Historical + current in one message

Input:
- Used to use Excel, now dashboard.

Expected:
- Excel historical
- dashboard current

### Example only

Input:
- For example we could use Lark.

Expected:
- Lark example_only

### Correction language

Input:
- Not PRD, I meant PO.

Expected:
- PRD and PO extracted
- correction cues visible for downstream handling

### Mixed language / shorthand

Input:
- Need send PDF to SG finance team lah.

Expected:
- PDF
- SG / Singapore
- finance team
- send PDF action if parse supports it

## Guardrails

- Do not treat extraction as truth
- Do not flatten all cues into one message-wide status
- Do not hallucinate action structure
- Do not hide weak confidence behind polished output

## Success criteria

This skill is working well when:

- one message is converted into a clean structured record
- negation/history/example cues are attached correctly
- action patterns are captured conservatively
- source spans are traceable
- weak extraction degrades safely