---
name: inference-first-prd-orchestrator
description: Guides a natural-chat requirements gathering workflow by first taking a current snapshot of system behavior, then proposing the next steps needed to reach evidence-first inference for interactive PRD sections. Use when evaluating or improving a chatbot that gathers information conversationally rather than through rigid form filling.
---

# Inference-First PRD Orchestrator Skill

Use this skill when the goal is to transform a requirements-gathering chatbot from section-by-section form filling into evidence-first conversational PRD gathering.

The chatbot should infer likely PRD content from natural conversation, then ask the user to confirm, correct, refine, prioritize, or reject those inferences.

This is **not** a form-filling workflow.

## Core Product Philosophy

The system should feel like it is:
- listening
- understanding context
- connecting earlier statements
- proposing likely interpretations
- validating understanding
- asking only what is still missing

It should **not** feel like:
- a survey
- a rigid wizard
- restarting each section from zero
- forcing users to repeat themselves
- pretending certainty when evidence is weak
- asking generic questions when evidence already exists

## Grounding Principle

All downstream inference quality depends on strong grounding from the conversation.

Background and Pain Points are especially important because they anchor later inference.

Therefore:
- Background may be partially inferred, but often still requires direct clarification.
- Pain Points / Current State should be treated as **hybrid inference-first**:
  - infer likely pain points from conversation signals
  - reflect them back in plain English
  - confirm severity, frequency, owner, and impact

Pain Points should not be treated as blank unless there is truly no usable evidence.

## What This Skill Owns

This skill owns:
- taking a live snapshot of current chatbot behavior
- identifying what sections already support inference
- identifying where generic elicitation still exists
- defining evidence-first orchestration logic
- deciding when to infer vs ask
- deciding which sections are interactive
- proposing implementation priorities
- protecting natural-chat UX

This skill does **not** own:
- final report derivation rules
- PDF structure rules
- report formatting policy
- blindly rewriting code
- inventing unsupported requirements
- replacing explicit user answers
- using markdown drafts as truth
- over-expanding architecture before proving quality

## Mandatory Operating Order

Always follow this sequence:

1. Get current system snapshot
2. Assess what already works
3. Identify inference gaps
4. Decide infer-vs-ask policy
5. Decide which sections are interactive
6. Recommend smallest high-leverage next step
7. Present proposal for user confirmation

Never skip step 1.

## Step 1 — Get Current Snapshot First

Before suggesting improvements, inspect current behavior.

Determine:
- where question generation happens
- whether orchestration exists before LLM calls
- whether prior structured evidence is reused
- whether drafts are incorrectly used as truth
- which sections already support inference
- whether explicit answers override inference
- whether confidence levels exist
- whether pain points are inferred or directly asked
- whether user experience still feels like form filling

### Snapshot Questions

1. Are sections treated as blank until directly answered?
2. Is structured evidence reused, or only passive prose drafts?
3. Which sections already support inference?
4. Which sections still rely on generic prompts?
5. Are explicit answers protected?
6. Is there confidence gating?
7. Are Pain Points inferred from conversation yet?
8. Can the system ask confirm/correct questions?

### Snapshot Output Format

Return:
- Current behavior
- What works
- Where it fails
- Main architecture gap
- Highest-value next upgrade

Do not jump to solutions before this.

## Step 2 — Section Model

### Interactive Sections

These require user interaction because they depend on judgment, confirmation, prioritization, tradeoffs, or boundary-setting.

They may still be inference-first, but they are **not** fully derived.

Interactive sections include:
- Background
- Pain Points / Current State
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

### Derived Sections

These should not be treated as blocking interview sections. They should be synthesized from already-confirmed content.

Derived sections include:
- Summary
- Executive Summary
- Report Title
- Open Questions
- Next Steps
- Cross-section Highlights

This skill does not govern how derived sections are rendered; it only recognizes that they should not be directly elicited.

## Step 3 — Evidence-First Decision Policy

For each interactive section:

### If Explicitly Answered
- Use user answer as source of truth
- Clarify only contradictions or missing specifics
- Stop asking broad questions

### If Grounded Evidence Exists
- Infer candidates
- Ask confirm/correct/prioritize/extend

### If Weak Evidence Exists
- Ask one narrow seed question

### If Evidence Conflicts
- Surface tradeoff
- Ask which matters more

### If No Evidence Exists
- Ask one specific grounding question

## Pain Points Inference Rules

Pain Points are high value because users naturally describe them before goals.

Infer pain points from signals like:
- repeated manual work
- delays
- rework
- wrong outputs
- mismatches
- returns
- duplicate effort
- detective work
- scaling pain
- frustration

### Good Pain Point Question
“From what you shared, it sounds like the main pain is repeated manual mapping work plus errors slipping through. Is that accurate, or is another issue worse?”

### Bad Pain Point Question
“What are your pain points?”

## Persona Guardrail

### Persona Evidence Hierarchy

Personas must be grounded in evidence. Apply in this order:

| Tier | Role | Condition |
|------|------|-----------|
| **Primary** | Operator / hands-on user | Who experiences the problem directly. Required. |
| **Secondary** | Manager / team lead | Who oversees the workflow or approves changes. Infer cautiously. |
| **Approver** | Director / Head of function | Who owns budget or governance. Mention only if relevant. |
| **Non-target** | Executive / C-suite / VP | Strategic receiver, not daily user. Never make them the main lens. |

### Rules

1. **Do not invent personas** unless there is zero user evidence. Prefer real roles named by the user.
2. **Primary persona = who experiences the problem most directly** — usually the operator or hands-on user.
3. **Economic buyer ≠ primary persona.** Executives may approve, but they do not feel the daily pain.
4. **Non-target personas must not become the main focus of a follow-up question.** They may be mentioned briefly only if strategically relevant.
5. **Prestige-persona suppression:** Do not default to "executive," "VP," "leadership," or "C-suite" as the primary business framing unless the user explicitly named them.
6. **When confidence is low**, ask a role-clarifying question rather than inventing a named persona.

### Question Policy

**Bad:** "What's the most compelling business outcome for executives from cutting mapping time?"

**Good:**
- "Who feels this pain most today — product ops, data operations, category managers, or leadership?"
- "Who would champion buying this internally, and who would use it daily?"
- "Which outcome matters most in practice: fewer manual hours, faster launches, fewer errors, or better scalability?"

### Stakeholder Taxonomy

When eliciting stakeholders, prefer this role frame:

- **Operator** — who does the work today
- **Manager** — who coordinates or delegates the work
- **Approver / Buyer** — who controls the budget or signs off
- **Downstream consumer** — who uses the output
- **Non-target** — executives who receive reports but don't touch the product

## Conversation Continuity Guardrail

The chatbot must build on its own immediately prior reasoning state.

### Rule

**If the previous assistant turn declared a persona hierarchy, the next question must respect that hierarchy unless new evidence changes it.**

Violating this feels random and breaks user trust even when individual answers are correct.

### Pre-Question Checklist

Before emitting a follow-up question, verify:

1. What personas were prioritized or named in the previous turn?
2. Which of those personas are still unresolved?
3. Did the previous turn explicitly deprioritize or label any audience as non-target?
4. Does the proposed next question contradict the prior framing?

If yes to (4): rewrite toward the primary persona, or explicitly explain why the audience is shifting.

### Consistency Rules

- If prior turn named Persona X as primary → next question should deepen X first.
- If prior turn labeled Persona Y as non-target → do not center the next question on Y.
- Prefer advancing unresolved primary personas before switching audiences.
- An executive question is only valid in the next turn if the user explicitly asked for it.

### Valid Audience Shifts

An audience shift is acceptable when:
- User asks for a board pitch, ROI narrative, or buyer persona
- Budget approval path becomes the explicit topic
- Go-to-market messaging is requested
- User introduces a new stakeholder themselves

### Invalid Audience Shift Example

```
Turn N:   "Primary users are Product Ops and Mapping Ops team.
           Executive is a non-target persona."
Turn N+1: "What is the most compelling outcome for executives?"   ← INVALID
```

```
Turn N:   "Primary users are Product Ops and Mapping Ops team."
Turn N+1: "Of the hands-on mapping team, who feels the most
           friction today: operators, leads, or analysts?"        ← VALID
```

## Confidence Model

### LOW Confidence
- no candidate list
- ask one anchored seed question

### MEDIUM Confidence
- propose one candidate
- ask confirm/correct

### HIGH Confidence
- propose 2–3 candidates
- ask confirm/correct/extend

Never present inferred content as final truth.

## Required Distinctions

### Method vs Outcome
Methods are not goals.

Methods:
- use an LLM
- build dashboard
- deploy classifier
- create rules engine

Outcomes:
- reduce manual review effort
- lower mapping errors
- cut onboarding time
- reduce repeated corrections

If user gives method:
- redirect toward business outcome

### Baseline vs Target
Examples:
- “8 hours a week” = baseline
- “under 2 hours a week” = target

If only baseline known:
- infer metric candidate
- ask only for target

Never invent targets.

## Recommended Upgrade Order

1. Improve Pain Points inference quality
2. Improve Goals / Non-goals / Metrics precision
3. Improve tradeoff detection
4. Improve section jumping logic
5. Expand to assumptions / risks / timeline
6. Only then widen architecture

Build a small strong brain first.

## Output Requirements

Every response using this skill should contain:
1. Current Snapshot
2. Gap Analysis
3. Recommended Next Step
4. Why It Matters
5. Confirmation Frame

## Final Reminder

This skill is for natural information gathering.

The chatbot should feel like:
- a smart analyst
- an attentive consultant
- a thoughtful collaborator

Not:
- a questionnaire
- a wizard
- a checklist
- a bureaucratic form