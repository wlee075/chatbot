---
name: dual-brain-prd-runtime
description: Governs a dual-brain PRD chatbot architecture where the orchestrator controls conversation flow and the composer controls derived report synthesis. Use when designing or improving a natural-chat requirements gathering system that must stay responsive during conversation while asynchronously keeping draft and PDF artifacts fresh.
---

# Dual-Brain PRD Runtime Skill

Use this skill when the system must support both:

- **natural conversational information gathering**
- **derived report generation and PDF export**

without letting those two responsibilities interfere with each other.

This skill defines a **dual-brain architecture**:

- **Orchestrator** = conversation brain
- **Composer** = report brain

They must cooperate, but they must not overlap.

---

# Core Principle

The system must separate:

- **what to ask next**
from
- **what the report currently says**

The chatbot should feel fast, grounded, and coherent.

That means:

- the **orchestrator** must stay on the hot path and remain cheap
- the **composer** must build derived report artifacts asynchronously and use caching

Do not collapse them into one subsystem.

---

# What This Skill Owns

This skill owns:

- defining the dual-brain runtime split
- deciding which component runs on the conversational hot path
- deciding which component runs on the report refresh path
- async compatibility rules
- cache and freshness rules for composed report artifacts
- preview, draft, and PDF refresh behavior
- separation of routing logic from report logic

This skill does **not** own:

- detailed section inference rules
- section-specific prompt wording
- PDF styling specifics
- UI theme or layout decisions
- low-level regex or extraction logic

---

# The Two Brains

## 1. Orchestrator

The orchestrator controls the **conversation**.

It decides:

- what section to focus on
- whether to infer, confirm, ask, or skip
- whether to surface a tradeoff
- whether to jump sections
- whether enough is already known

The orchestrator must be:

- deterministic
- low-latency
- async-compatible
- free of heavy synthesis work
- free of PDF/report rendering work

### Orchestrator owns

- infer-vs-ask routing
- confidence gating
- section jump decisions
- pain-point inference
- tradeoff questioning
- prompt override decisions
- no-question-needed decisions

### Orchestrator must not own

- executive summary generation
- open questions rollup
- next steps rollup
- report title generation
- PDF composition
- final report synthesis

---

## 2. Composer

The composer controls the **report**.

It decides:

- what the current report says
- which sections are derived
- how to roll up unresolved items
- how to build executive summary
- how to build open questions
- how to build next steps
- what content should go into PDF export

The composer must be:

- async-first
- cache-aware
- artifact-oriented
- driven by confirmed state
- independent of conversational routing

### Composer owns

- section summaries
- executive summary
- derived summary content
- report title
- open questions
- next steps
- completion rollups
- report artifact construction
- PDF-ready payload generation

### Composer must not own

- question generation
- infer-vs-ask routing
- section jumping
- conversation flow decisions
- prompting policy

---

# Async Runtime Model

## Orchestrator: async-compatible, hot-path safe

The orchestrator should expose:

- an async entrypoint
- optionally a synchronous wrapper for compatibility

Example shape:

- `async inference_first_prd_orchestrator_async(state, section) -> ActionPlan`
- `inference_first_prd_orchestrator(state, section) -> ActionPlan`

The async interface exists so the system can evolve cleanly, but the orchestrator’s work should stay cheap enough that it behaves like a fast deterministic hot-path decision layer.

### Orchestrator allowed work

- state snapshot
- section confidence evaluation
- candidate selection
- section jump selection
- action plan generation

### Orchestrator forbidden work

- LLM calls
- PDF rendering
- report synthesis
- full-section draft regeneration
- wide expensive rescans on every turn

---

## Composer: async-first, cached artifact producer

The composer should expose:

- an async entrypoint
- optionally a synchronous compatibility wrapper

Example shape:

- `async compose_report_async(state, trigger) -> ComposedReport`
- `compose_report(state, trigger) -> ComposedReport`

The composer may be heavier than the orchestrator because it assembles multiple derived outputs. That is why it must be async-first and cached.

### Composer allowed work

- section rollup
- executive summary generation
- open questions derivation
- next steps derivation
- report artifact assembly
- PDF-ready payload assembly

### Composer forbidden work

- conversational question routing
- next user prompt selection
- section jump logic
- conversation control

---

# Runtime Flow

## Hot Path: user conversation

When the user sends a message:

1. build current state snapshot
2. run **orchestrator**
3. get ActionPlan
4. generate assistant reply based on ActionPlan
5. update canonical state

Do **not** run report composition as part of the default conversational hot path, except for lightweight cache invalidation signals.

## Warm Path: report refresh

Run the **composer** when:

- progress reaches 80%
- user clicks View Draft
- user clicks Download PDF
- session ends
- final report is requested
- a major section completion changes report meaning

The composer should refresh a cached report artifact that the UI can reuse.

---

# Artifact Cache Model

Composer output must be cached.

## Minimum cached artifact contents

A report artifact should contain:

- executive_summary
- section_summaries
- open_questions
- next_steps
- report_title
- completion_pct
- trigger
- generated_at
- source_hash

## Minimum cache key

Cache must be based on state content, not only the trigger.

Use a hash or equivalent derived from:

- confirmed_qa_store
- prd_sections
- section_scores
- completion percentage

If state has not changed meaningfully, do not recompute the report.

---

# Freshness Rules

## Orchestrator
Always recompute.

Reason:
- cheap
- conversation-critical
- turn-dependent

## Composer
Recompute only when:
- no report artifact exists
- state changed meaningfully
- report trigger requires freshness

Otherwise use cached artifact.

---

# 80% Completion Behavior

When completion reaches 80%:

- composer should refresh the report artifact
- this should happen asynchronously or as a warm-path refresh
- do not automatically render/export PDF
- do not block the conversation waiting for report completion

The goal is:
- when the user clicks download, the artifact is already fresh

---

# Download Behavior

When user clicks **Download PDF**:

1. check whether a fresh composed artifact exists
2. if stale, refresh composer artifact first
3. then render/export PDF from the composer artifact
4. never assemble PDF content ad hoc from scattered UI state

The PDF renderer must render what the composer already prepared.

It must not decide report meaning itself.

---

# View Draft Behavior

When user clicks **View Draft**:

1. check artifact freshness
2. refresh via composer if needed
3. show composed draft sections
4. show derived summary, open questions, and next steps from composer output

Do not build draft view from raw unfinished text blobs if a composed artifact exists.

---

# Derived vs Interactive Responsibility

This skill assumes the system separates:

## Interactive sections
These belong to the conversation engine:
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
- Timeline
- Dependencies

## Derived sections
These belong to the report engine:
- Summary
- Executive Summary
- Report Title
- Open Questions
- Next Steps
- Cross-section Highlights

The orchestrator may recognize that some sections are derived, but it must not generate them itself.

---

# Strict Separation Invariants

These rules are mandatory:

- Orchestrator never builds PDFs
- Orchestrator never composes open questions
- Orchestrator never composes next steps
- Composer never chooses next user question
- Composer never changes section routing
- Composer consumes state but does not control the interview
- PDF renderer renders composed payload only
- Derived sections must never be asked as standalone user questions

---

# Logging Requirements

At minimum, the runtime should emit logs like:

## Orchestrator logs
- orchestrator_async_started
- orchestrator_async_finished
- orchestrator_action_decided
- orchestrator_prompt_override_applied
- orchestrator_section_jump_reason

## Composer logs
- composer_refresh_started
- composer_refresh_finished
- composer_cache_hit
- composer_cache_miss
- composer_summary_regenerated
- composer_pdf_export_started

These logs make it possible to diagnose:
- latency issues
- stale artifacts
- accidental overlap of roles
- skipped refreshes
- bad routing/report coupling

---

# Output Requirements

When using this skill, the response should contain:

1. Current dual-brain snapshot  
2. Which work belongs to orchestrator  
3. Which work belongs to composer  
4. Async execution model  
5. Cache/freshness rules  
6. Trigger behavior for draft/download/finalization  
7. Recommended next implementation step  

---

# Good Output Example

A strong output using this skill would sound like:

- “The orchestrator should remain on the conversational hot path and stay deterministic.”
- “The composer should refresh report artifacts asynchronously at 80%, on draft view, and on download.”
- “PDF export must consume composer output, not reconstruct report meaning inline.”
- “These two layers should cooperate but never overlap.”

---

# Bad Output Example

Do not do this:

- merge orchestrator and composer into one runtime
- run full report synthesis on every user turn
- let the composer control next-question logic
- let the orchestrator render PDF content
- treat async as a reason to add uncontrolled parallel work
- recompute report artifacts every time without cache checks

---

# Final Reminder

This skill exists to protect both:

- **conversation responsiveness**
- **report coherence**

The orchestrator should feel like:
- a fast conversation planner

The composer should feel like:
- a reliable report builder

If one starts doing the other’s job, the product will become:
- slow
- confusing
- brittle
- hard to debug