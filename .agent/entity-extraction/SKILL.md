---
name: keyword-extraction
description: Extracts high-value entities, phrases, and business concepts from messy user text. Use when converting free-text replies into structured concepts for provenance, routing, follow-up questions, or conversational memory.
---

# Keyword Extraction Skill

Use this skill when the system needs to turn messy user text into a small set of useful concepts.

This skill is for:
- provenance chips
- follow-up question generation
- routing
- memory updates
- extraction debugging

This skill is **not** for:
- summarizing full documents
- deciding final business truth
- generating user-facing prose

## Core rule

Do not extract everything that looks like a noun.

Extract only concepts that are likely to be useful for:
- business meaning
- workflow understanding
- traceability
- downstream decisions

A good extractor returns a few strong concepts.  
A bad extractor returns many weak nouns.

## When to use this skill

Use this when:
- user input is long or messy
- user replies contain workflow details
- provenance chips need evidence terms
- routing depends on domain concepts
- memory needs stable concept keys
- you are debugging extraction quality

## Extraction pipeline

### 1. Normalize the text

Create a clean working copy:

- preserve original text for display
- normalize whitespace
- repair obvious line-break damage
- lowercase for matching
- preserve numbers and units
- preserve offsets if later highlighting is needed

Do not lose the original surface form.

### 2. Extract entities first

Use established NLP packages where possible.

Preferred stack:
- spaCy tokenizer
- spaCy NER
- spaCy EntityRuler for domain terms

Extract entities before generic keywords.

Typical entity classes:
- PERSON
- ORG
- LOCATION
- DATE
- TIME
- TOOL
- SYSTEM
- FILE_TYPE
- DOCUMENT_TYPE
- BUSINESS_IDENTIFIER

Examples:
- Hong Kong
- Singapore
- Excel
- SAP
- PDF
- SKU
- PRD

Entities usually outrank generic nouns.

### 3. Extract multi-word phrases

Then extract strong phrases such as:
- product mapping
- group mailbox
- manual retrieval
- unified excel sheet
- approval workflow

Prefer meaningful multi-word phrases over weak single tokens.

### 4. Extract important tokens only if needed

Use token-level extraction as a fallback, not the default.

Keep tokens only if they are:
- domain-specific
- high-value
- not already covered by a better entity or phrase

Examples:
- pdf
- mapping
- invoice
- automation

### 5. Normalize to canonical forms

Store:
- `surface_text`
- `normalized_text`
- `candidate_type`
- `source`

Examples:
- PDFs → pdf
- emailing → email
- mappings → mapping

Do not lose the original phrase.

## Candidate types

Every candidate should be labeled as one of:

- `ENTITY_SPACY`
- `ENTITY_RULER`
- `PHRASE`
- `TOKEN`
- `ALLOWLIST`

This makes debugging and ranking much easier.

## Ranking rules

Use this ranking order:

1. exact entity or exact surface phrase
2. exact normalized phrase
3. domain entity / EntityRuler match
4. strong multi-word phrase
5. approved domain token
6. weak generic token

Examples:
- SAP > system
- group mailbox > mailbox
- PDF > file
- product mapping > mapping

## What to reject

Reject or down-rank:
- pronouns
- determiners
- vague chunks
- malformed truncations
- duplicate substrings
- stopword-heavy phrases
- generic filler nouns
- one-character tokens

Examples to reject:
- it
- that
- this
- thing
- the end goal
- mailbo

## Business-chat overrides

Use domain-aware rules for common workplace terms.

Examples:
- Excel
- PowerPoint
- PDF
- CSV
- PRD
- KPI
- SLA
- SAP
- Salesforce
- Outlook
- Lark
- mailbox
- shared drive
- client code
- SKU

These may require EntityRuler or allowlist support.

## Output contract

Return structured output like:

```json
{
  "entities": [
    {
      "text": "Hong Kong",
      "normalized": "hong kong",
      "label": "LOCATION",
      "source": "ENTITY_SPACY"
    },
    {
      "text": "Excel",
      "normalized": "excel",
      "label": "TOOL",
      "source": "ENTITY_RULER"
    }
  ],
  "phrases": [
    {
      "text": "product mapping",
      "normalized": "product mapping",
      "source": "PHRASE"
    }
  ],
  "keywords": [
    {
      "text": "automation",
      "normalized": "automation",
      "source": "TOKEN"
    }
  ],
  "final_ranked": [
    "Excel",
    "Hong Kong",
    "product mapping",
    "automation"
  ]
}
```

## How to Use Results

### Provenance Chips

Best clickable candidates are entities.

Good chips:
	•	Excel
	•	Hong Kong
	•	PDF
	•	Finance Team

Weak chips:
	•	system
	•	file
	•	thing

### Better Questions

Bad:
	•	What system?

Good:
	•	You mentioned SAP. Which module is used today?

Bad:
	•	Which office?

Good:
	•	You mentioned Hong Kong. Is that where approvals happen?

### Memory

Store stable objects:
	•	current_tool = Excel
	•	target_market = Hong Kong
	•	owner_team = Finance Team

### Routing

Use entities to route flows:
	•	SAP / Salesforce → enterprise systems
	•	PRD / roadmap → product flow
	•	Invoice / PO → finance flow
	•	Hong Kong / Singapore → regional ops flow

### Debugging Checklist

When extraction looks wrong:
	1.	Did we miss obvious named objects?
	2.	Did generic nouns get promoted over entities?
	3.	Did abbreviations overfire?
	4.	Did fragments survive?
	5.	Are labels consistent?
	6.	Did EntityRuler patterns conflict with base NER?
	7.	Are duplicate entities merged?

### Edge Cases

#### Broken Input

Input:

send pdf to hk fin team

Possible output:
	•	PDF
	•	Hong Kong
	•	Finance Team (if alias exists)

### Mixed Tool + Action

Input:

update excel then email mailbox

Entities:
	•	Excel
	•	mailbox (if allowlisted)

Actions belong to keyword skill, not entity skill.

### Short Input

Input: 

SAP

Return:
	•	SAP (SYSTEM)

## Performance Guidance

Preferred stack:
	•	spaCy tokenizer
	•	spaCy NER
	•	EntityRuler
	•	simple normalization rules

Avoid heavy custom models unless accuracy demands it.

## Relationship to Keyword Extraction Skill

Use this skill first.

Then run keyword extraction for:
	•	workflows
	•	actions
	•	pain points
	•	generic concepts

Example:

Input:

We manually map invoices in Excel and send PDFs to Hong Kong

Entity skill returns:
	•	Excel
	•	PDF
	•	Hong Kong

Keyword skill returns:
	•	manual mapping
	•	invoices
	•	send PDFs

## Guardrails
	•	Do not invent entities.
	•	Do not over-promote generic nouns.
	•	Do not trust weak abbreviations blindly.
	•	Do not output duplicate aliases repeatedly.
	•	Do not confuse concepts with named objects.

## Success Criteria

A strong entity extractor should:
	•	surface specific named objects
	•	ignore generic filler nouns
	•	label consistently
	•	improve provenance quality
	•	improve routing precision
	•	support sharper follow-up questions