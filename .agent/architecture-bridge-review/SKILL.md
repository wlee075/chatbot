---
name: architecture-bridge-review
description: Reviews the current implemented architecture, identifies the next planned steps already in flight, and maps a concrete bridge from the current system to a target design where each future agent has exactly one responsibility and only the orchestrator coordinates. Use before proposing new architectural changes, adding new agents, or redistributing responsibilities.
---

# Architecture Bridge Review Skill

Use this skill before making architecture recommendations.

This skill exists to prevent a common failure:
- proposing a cleaner future architecture
- without grounding it in what is already implemented
- and without showing how to bridge from today’s system to the target where each future agent has one responsibility and only the orchestrator coordinates

This skill always does two things in order:

1. **Get a status update of the current architecture and the next steps already planned**
2. **Map how the current architecture can be bridged to the target where each future agent has exactly one responsibility and all coordination goes through the orchestrator**

## Core purpose

This skill is for **architectural grounding and bridge planning**.

It helps answer:

- What is actually implemented today?
- What parts are still monolithic?
- What next steps are already agreed or in flight?
- Which current components already resemble future agent boundaries?
- Which responsibilities are mixed together today?
- What is the shortest credible bridge to a single-responsibility-agent architecture?

## Architectural target this skill assumes

The destination architecture is:

- one **Orchestrator Agent**
- several **future single-responsibility agents**
- no direct agent-to-agent communication
- typed inputs and outputs
- all sequencing, routing, escalation, and final assembly owned by the orchestrator

This skill does **not** assume the system is already there.

It is specifically for reviewing the gap between current reality and that target.

## Non-negotiable rules

### Rule 1: Do not skip the current-state review
Always review the current implemented architecture before proposing changes.

### Rule 2: Keep terminology clean
Use:
- **current components/nodes/modules**
- **future agents**

Do not casually label current overloaded nodes as if they were already clean future agents.

### Rule 3: One responsibility per future agent
Every proposed future agent must own exactly one responsibility.

### Rule 4: Orchestrator owns coordination only
The orchestrator may own:
- sequencing
- routing
- escalation
- final response assembly

The orchestrator must **not** own:
- extraction
- contradiction detection
- evidence selection
- question generation logic
- truth commitment logic
- any other domain-specific decision logic that belongs in a future agent

### Rule 5: No overlapping future-agent ownership
For every proposed future agent, you must state:
- what responsibility it owns
- what primary output it produces
- what decision(s) it alone owns

If two proposed future agents both:
- classify the same thing
- mutate the same state
- decide the next question
- determine truth eligibility
- or otherwise share the same decision authority

then the architecture is still overlapping and needs revision.

---

# When to use this skill

Use this skill when:

- the user asks about architecture direction
- a refactor is being proposed
- a new agent is about to be introduced
- responsibilities may overlap
- the workflow feels too monolithic
- the system needs a status review before more design changes
- you need to decide whether a new responsibility belongs in an existing current component or should be split into a distinct future agent

## When not to use this skill

Do not use this skill for:
- frontend-only polish
- isolated bug fixes with no architecture impact
- pure prompt tuning with no effect on ownership boundaries
- low-level implementation details unless they change architecture

---

# Output requirements

A correct output from this skill should always include:

1. **Current architecture status**
2. **Current orchestration**
3. **Already implemented next steps**
4. **Responsibilities currently mixed together**
5. **Bridge plan toward single-responsibility future agents**
6. **Risks / sequencing cautions**
7. **Anti-overlap check for proposed future agents**

---

# Step 1: Review the current architecture and next planned steps

Start by identifying what is real today.

## Required questions

Answer all of these:

- What major current components/nodes/modules exist today?
- Which are actually implemented versus only designed?
- What does each one do today?
- What inputs and outputs does each have?
- Which parts are deterministic code, LLM-based, or hybrid?
- How does one user message flow through the system today?
- How is the current flow orchestrated today?
- How does the system currently decide:
  - what question to ask next
  - whether to answer a clarification request
  - whether to enter repair flow
  - whether to commit truth
  - whether to enter draft mode
- What classification logic already exists?
- What state artifacts already exist?
- What next steps are already planned or partially implemented?

## Required categories

### A. Implemented today
List real current components/nodes/modules.

### B. Partially implemented / experimental
List things that exist but are not yet strong enough to rely on fully.

### C. Designed but not implemented
List planned structures only.

### D. Current workflow
Explain the real turn flow.

### E. Current orchestration
Be explicit about:
- execution order
- routing precedence
- who decides what happens next
- who decides question generation
- who decides clarification behavior
- who decides repair behavior
- who decides truth commit
- who decides draft mode

### F. Existing planned next steps
List what is already in motion.

---

# Step 2: Diagnose responsibility overlap

This section is the diagnosis.

Do not jump straight to the solution yet.

## Required questions

- Which current components mix multiple responsibilities?
- Where are interpretation, routing, question generation, repair, and truth commitment bundled together?
- Which decisions are currently duplicated in more than one place?
- Which state mutations happen in more than one place?
- Which current components are closest to future-agent boundaries?
- Which current components are too overloaded to map cleanly to a single future agent?

## Review lens

Look specifically for current components that do more than one of these:
- classify intent
- understand message meaning
- update semantic state
- detect contradictions
- manage blockers
- answer clarification questions
- detect repeats
- choose citations
- generate the next question
- commit truth
- coordinate routing

If one current component owns several of these, it is overloaded.

---

# Step 3: Bridge to the single-responsibility future-agent target

This section is the treatment plan.

After grounding the current state and diagnosing overlap, map the bridge.

## Required questions

- Which current components already resemble future single-responsibility agent boundaries?
- Which current components should be split?
- For each responsibility, which future agent boundary should own it?
- What current component should be refactored to become that future agent?
- Which responsibilities should remain separate no matter what?
- What should the orchestrator own now?
- What should stay monolithic for now?
- What is the safest implementation order?

## Important wording rule

Do **not** ask:
> which existing agent should absorb this responsibility?

Ask instead:
> which future single-responsibility agent boundary should own this responsibility, and what current component should be refactored to become that future agent?

That keeps the review aligned with the one-responsibility-per-agent rule.

---

# Step 4: Anti-overlap check for future agents

Before finalizing the bridge plan, run an explicit anti-overlap check.

For every proposed future agent, state:

1. **Owned responsibility**
2. **Primary input**
3. **Primary output**
4. **Decision(s) it alone owns**
5. **What it explicitly does not own**

Then verify:

- no two future agents classify the same thing
- no two future agents mutate the same state for the same purpose
- no two future agents decide the next question
- no two future agents determine truth eligibility
- no two future agents own the same repair behavior

If any of those overlap, revise the bridge plan.

---

# Decision tree

## Case 1: Current system is still monolithic
Focus on:
- identifying overloaded current components
- defining the first seam split
- preserving behavior while separating responsibilities

## Case 2: Some semantic state already exists
Focus on:
- making downstream current components actually consume that state
- not inventing new future agents unless ownership still overlaps

## Case 3: A new classifier/agent is being proposed
Focus on:
- whether the responsibility truly deserves its own future agent
- whether the current component should be refactored into that future agent boundary
- whether introducing a new future agent would reduce or increase overlap

## Case 4: The user asks about long-term multi-agent design
Focus on:
- orchestrator-centered hub-and-spoke architecture
- one job per future agent
- typed outputs
- no direct agent-to-agent interaction
- incremental bridge from current system

---

# What to look for in the current architecture

## Good signs
- a current component already has one clear responsibility
- typed artifacts already exist
- orchestration order is explicit
- output ownership is clear
- state mutations are localized

## Warning signs
- one current node both interprets and commits truth
- question generation ignores structured state
- repair logic and main logic are mixed
- UI rendering decisions leak into architecture logic
- multiple current components classify the same thing differently
- a new future agent is being proposed before current overlaps are understood
- the orchestrator is quietly becoming a domain-logic owner

---

# Recommended response structure

Use this structure whenever you apply the skill:

## 1. Current architecture
- implemented current components
- what each does now
- current turn flow

## 2. Current orchestration
- execution order
- routing precedence
- who decides next question
- who decides clarification behavior
- who decides repair behavior
- who decides truth commit
- who decides draft mode
- whether orchestration is rule-based, LLM-based, or hybrid

## 3. Existing next steps
- what is already planned
- what is partially implemented
- what should not be duplicated

## 4. Responsibility overlap diagnosis
- what is mixed together today
- what decisions are duplicated
- what needs separating

## 5. Bridge to future single-responsibility agents
- which future agent should own each responsibility
- what current component should be refactored into that future agent
- what the orchestrator should own
- what the next seam split should be

## 6. Anti-overlap check
- future agent by future agent
- owned responsibility
- primary output
- sole decisions owned
- explicit non-responsibilities

## 7. Recommendation
- safest next move
- what not to do yet

---

# Review checklist

Before finalizing, check:

1. **Grounded in current reality**  
   Did you clearly separate implemented vs planned?

2. **Workflow-aware**  
   Did you explain how one turn flows today?

3. **Orchestration-aware**  
   Did you explain how the system currently decides:
   - next question
   - clarification
   - repair
   - truth commit
   - draft mode

4. **Overlap-aware**  
   Did you identify which responsibilities are mixed together today?

5. **Bridge-focused**  
   Did you show how to get from current state to the single-responsibility future-agent target?

6. **Anti-overlap enforced**  
   Did you verify that no two proposed future agents own the same decision?

7. **Minimal sprawl**  
   Did you avoid proposing unnecessary new future agents?

---

# Good output example

Good output says:

- “Today, these current components exist…”
- “Current next-question orchestration is mostly LLM-driven with rule-based routing gates…”
- “This current node is overloaded and should split first…”
- “This responsibility should belong to the future Contradiction Agent, and the current conflict-checking logic should be refactored into it…”
- “The orchestrator should own routing only and must not own contradiction logic…”

## Bad output example

Bad output says:

- “We should create many new agents”
- “We need an orchestrator”
- “The future architecture should be cleaner”

without:
- current architecture status
- existing next steps
- actual workflow ownership
- concrete bridge path
- anti-overlap check

---

# Success criteria

This skill is working well when:

- every architecture recommendation starts from the current implemented system
- current and planned work are clearly separated
- new future agents are introduced only when ownership is truly distinct
- the bridge to one-responsibility-per-agent is incremental and concrete
- the orchestrator’s role stays central and coordination-only
- future-agent ownership boundaries are explicit and non-overlapping