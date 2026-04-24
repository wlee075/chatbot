---
name: prd-derived-report-composer
description: Governs which PRD sections should be derived from confirmed conversational evidence and how derived report artifacts should be assembled at preview, download, and finalization time. Use when improving report generation, PDF behavior, or section derivation policy.
---

# PRD Derived Report Composer Skill

Use this skill when the goal is to decide which PRD sections should be synthesized from already-confirmed content rather than asked directly, and when those derived sections should be generated.

This skill is about **report composition**, not interactive questioning.

## Core Principle

Do not ask the user for anything that is merely a rewording of what the system already knows.

Ask only for:
- missing facts
- decisions
- priorities
- tradeoffs
- confirmations

Derive everything else.

## What This Skill Owns

This skill owns:
- deciding which sections are derived
- deciding when derived sections are generated
- defining source-of-truth precedence for derived content
- ensuring report synthesis uses confirmed content rather than noisy drafts
- defining graceful fallback when data is incomplete
- defining preview/download/final report behavior

This skill does **not** own:
- question generation
- infer-vs-ask orchestration
- conversation turn policy
- user-facing elicitation strategy

## Derived Sections

These should be derived, not directly elicited:
- Summary
- Executive Summary
- Report Title
- Open Questions
- Next Steps
- Cross-section Highlights

## Interactive Sections

These are confirmed through chat and then become source material:
- Background
- Pain Points
- Goals
- Non-goals
- Success Metrics
- Assumptions
- Risks
- Constraints
- Stakeholders
- Scope / Out of Scope
- Proposed Solution
- Validation / Evaluation
- Timeline Candidates
- Dependencies

## Source of Truth Order

When deriving report sections, use:

1. explicit confirmed user answers
2. canonical QA store
3. user-validated section content
4. synthesized section drafts
5. never use raw unfinished draft text as primary truth

## Required Summary Rule

Summary must be derived from the report content, not asked as a standalone section.

Generate or refresh Summary at:
- 80% completion preview
- PDF download
- final report generation

Summary should never block the conversation.

## Executive Summary Rule

Executive Summary is also derived.
It should be a concise synthesis of:
- problem
- pain
- goal
- proposed solution
- expected impact

## Graceful Incomplete-State Handling

If the report is incomplete:
- derive the report anyway
- mark incomplete sections as “Needs more data to fill”
- do not fabricate missing content
- keep download available if policy allows
- distinguish confirmed content from unresolved areas

## Open Questions Rule

Open Questions should be derived from:
- unresolved sections
- low-confidence sections
- contradiction flags
- missing targets
- missing validation plans

Never ask Open Questions as a first-class section.

## Next Steps Rule

Next Steps should be derived from:
- unresolved interview gaps
- implementation follow-ups
- validation needs
- stakeholder confirmation needs

## Output Requirements

Every response using this skill should contain:
1. Which sections are derived
2. Which remain interactive
3. When derivation happens
4. What sources are used
5. How incomplete content is handled

## Final Reminder

This skill is for report synthesis and composition.

It should make the system feel:
- coherent
- progressive
- trustworthy

It should prevent the experience from feeling:
- repetitive
- redundant
- over-elicited
- form-like