---
name: keyword-extraction
description: Extracts high-value workflows, actions, business concepts, pain points, metrics, and operational phrases from user text after entities are handled separately. Use when you need process understanding beyond named objects.
---

# Keyword Extraction Skill

Use this skill **after entity extraction**.

Entity extraction handles:

- people
- tools
- systems
- places
- files
- named teams
- dates

This skill handles everything else that matters:

- workflows
- actions
- business objects
- pain points
- bottlenecks
- metrics
- priorities
- rules
- dependencies

Example:

Input:

`We manually map invoices in Excel and send PDFs to Hong Kong`

Entity skill returns:

- Excel
- PDF
- Hong Kong

Keyword skill returns:

- manually map invoices
- invoice mapping
- send documents
- manual workflow

---

# Core Rule

Keywords are **meaningful concepts**, not just leftover nouns.

Good keywords:

- manual mapping
- approval delay
- duplicate entry
- retrieve report
- monthly reconciliation
- send invoices
- slow turnaround
- reduce workload

Weak keywords:

- thing
- work
- process
- item
- task
- data (without context)

---

# When to Use This Skill

Use when you need to understand:

- what the user does
- what is painful
- what should improve
- what process exists today
- what output is needed
- what metrics matter
- what rules block execution

Use for:

- better follow-up questions
- routing
- summarization
- PRD drafting
- memory
- workflow provenance

---

# Extraction Pipeline

## Step 1: Remove / Protect Entities

Run entity extraction first.

Protect entity spans so they are not repeatedly extracted as generic keywords.

Example:

If `Excel` already tagged as TOOL, avoid low-value keyword `excel`.

---

## Step 2: Extract Action + Object Patterns

Look for verbs linked to business objects.

Examples:

- map invoices
- send reports
- retrieve PDFs
- reconcile payments
- update spreadsheet
- chase approvals

These are usually stronger than nouns alone.

---

## Step 3: Extract Multi-word Operational Phrases

Examples:

- manual product mapping
- approval bottleneck
- duplicate data entry
- shared mailbox workflow
- monthly reporting cycle
- delayed handoff process

Prefer phrases over single words.

---

## Step 4: Detect Pain / Goal Signals

Pain examples:

- slow
- manual
- error-prone
- duplicate
- delayed
- confusing
- backlog

Goal examples:

- automate
- simplify
- speed up
- reduce errors
- centralize
- track status

---

## Step 5: Detect Metrics / Scale

Examples:

- 3 days turnaround
- 20 reports daily
- 500 invoices monthly
- 2 staff required
- weekly manual work

---

## Step 6: Normalize

Store:

- surface_text
- normalized_text
- keyword_type
- confidence

Examples:

- mapping products → product mapping
- sending emails → send email
- delays in approval → approval delay

---

# Recommended Keyword Types

## Workflow

- product mapping
- invoice processing
- report generation

## Action

- send email
- retrieve file
- update tracker

## Pain Point

- manual work
- duplicate entry
- slow approval

## Goal

- automate retrieval
- reduce turnaround
- improve visibility

## Metric

- 3 days
- 20 daily
- monthly cycle

## Dependency

- waiting for finance
- needs manager approval
- requires SAP access

---

# Ranking Rules

1. action + object phrase
2. strong operational phrase
3. pain / goal phrase
4. metric phrase
5. useful token
6. weak generic noun

Examples:

- manual product mapping > mapping
- approval delay > delay
- send invoice > invoice
- 3 day turnaround > days

---

# What to Reject

Reject:

- thing
- work
- task
- item
- stuff
- process (alone)
- issue (alone)
- data (alone)

Reject weak leftovers from entity layer:

- hong
- kong
- excel (if already entity)
- pdf (if already entity)

---

# Output Contract

```json
{
  "keywords": [
    {
      "text": "manual product mapping",
      "normalized": "manual product mapping",
      "type": "WORKFLOW"
    },
    {
      "text": "send PDFs",
      "normalized": "send documents",
      "type": "ACTION"
    },
    {
      "text": "reduce manual work",
      "normalized": "reduce manual work",
      "type": "GOAL"
    },
    {
      "text": "3 days turnaround",
      "normalized": "3 day turnaround",
      "type": "METRIC"
    }
  ],
  "final_ranked": [
    "manual product mapping",
    "send documents",
    "reduce manual work",
    "3 day turnaround"
  ]
}
```

## How to Use Results

### Better Questions

Bad:
	•	What do you mean?

Good:
	•	You mentioned manual product mapping. Which fields are matched by hand today?

Bad:
	•	What is the issue?

Good:
	•	You mentioned approval delay. Where does the wait usually happen?

### Memory

Store:
	•	current_workflow = manual product mapping
	•	pain_point = duplicate entry
	•	goal = automate retrieval

### Routing
	•	approval / policy / owner → governance flow
	•	report / dashboard / KPI → analytics flow
	•	invoice / payment / reconciliation → finance flow
	•	mapping / import / export → ops flow


### Relationship to Entity Skill

Input:

We manually map invoices in Excel and send PDFs to Hong Kong

Entity skill:
	•	Excel
	•	PDF
	•	Hong Kong

Keyword skill:
	•	manually map invoices
	•	invoice workflow
	•	send documents
	•	manual process

Combined understanding:
	•	tool = Excel
	•	destination = Hong Kong
	•	workflow = manual invoice mapping
	•	artifact = PDF

### Debugging Checklist
	1.	Are we extracting verbs + objects?
	2.	Are phrases stronger than tokens?
	3.	Are pain signals detected?
	4.	Are metrics captured?
	5.	Are entity leftovers leaking in?
	6.	Are generic nouns dominating?
	7.	Is ranking useful?

### Edge Cases

#### Noisy Input

we use excel manually key in and send pdf

Return:
	•	manual key in
	•	send documents
	•	manual workflow

(Excel / PDF belong to entity layer)

#### Very Short Input

manual

Return only if context supports pain signal.

#### Mixed Message

Need automate report. takes 2 days now

Return:
	•	automate reporting
	•	2 day turnaround
	•	current delay

### Performance Guidance

Use lightweight parsing:
	•	dependency parse
	•	noun chunks
	•	verb-object heuristics
	•	regex for metrics
	•	phrase ranking rules

Avoid heavy generation for extraction.

### Guardrails
	•	Do not invent workflows.
	•	Do not treat every noun as useful.
	•	Do not duplicate entities as keywords.
	•	Do not ignore verbs.
	•	Do not output giant noisy lists.

### Success Criteria

A strong keyword extractor should:
	•	explain what work happens
	•	reveal pain points
	•	capture goals
	•	detect scale / metrics
	•	complement entity extraction
	•	enable sharper follow-up questions