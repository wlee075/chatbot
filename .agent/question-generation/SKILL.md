---
name: question-generation
description: Generates the best next question in the conversation flow. Use when the previous question is resolved, a follow-up is needed, ambiguity must be narrowed, branch-specific questioning is required, or the assistant needs one focused next step without repeating itself.
---

# Question Generation Skill

Use this skill whenever the chatbot needs to decide **what to ask next**.

## Core responsibility

This skill owns next-question creation only.

It decides:

- What single question should be asked next
- How to narrow ambiguity
- How to follow the chosen branch
- How to uncover missing business details efficiently
- How to move toward drafting readiness
- How to avoid repeating earlier questions

This skill does **not** own answer validation, question lifecycle state transitions, telemetry, or performance tuning.

## Primary standard

Every turn should produce:

- **One focused question**
- **High information gain**
- **Plain English wording**
- **No repeated asks**
- **Natural conversational flow**

Bad output:

- 5 questions at once  
- vague broad resets  
- internal evaluator jargon  
- repeating previous questions  
- asking for data already known

Good output:

- one clear next step that closes the biggest gap

## When to use this skill

Use this skill when:

- Previous question was answered
- Reply was partial and needs one follow-up
- A branch/option was selected
- The assistant needs one narrower question
- Drafting is not yet ready
- Clarification is complete and flow should resume
- Multiple gaps exist and one must be prioritized

## Inputs required

Use the smallest sufficient context:

- `active_question_id`
- prior question text
- resolution result
- chosen branch / option
- known facts
- unresolved blockers
- draft readiness state
- recent asked questions

Do not require full chat history unless necessary.

## Question selection hierarchy

Choose next question in this priority order:

### 1. Highest leverage blocker

Ask the question whose answer most reduces uncertainty.

Examples:

- volume
- owner
- trigger
- frequency
- current pain severity
- current workflow steps

### 2. Branch continuation

If the user selected a branch, continue within that branch.

Example:

If issue = mapping creation  
Next ask mapping workflow details, not PDF sending.

### 3. Repair unresolved contradictions

If conflicting facts exist, clarify before expanding scope.

### 4. Draft readiness unlocker

Ask for the one missing field preventing draft start.

## One-question-per-turn rule

Always ask exactly one primary question.

Allowed:

- one question sentence
- one short lead-in + one question

Not allowed:

- multiple stacked questions
- bullet list interrogations
- giant forms unless explicitly requested

## Narrow, not broad

Every next question should be narrower or more specific than before.

Bad:

“What else can you tell me about the process?”

Good:

“How many PDF files are usually processed each day?”

## Branch-aware generation

If a branch is selected, stay inside it.

Example:

Question: Is the main issue mapping creation or file sending?  
User: Mapping creation.

Good next question:

“What part of creating the mappings is most manual today?”

Bad next question:

“How are PDFs forwarded to the mailbox?”

## Plain English rule

Do not expose internal terms like:

- blocker
- contradiction
- evaluator gap
- unresolved required field
- ambiguity class B

Rewrite naturally.

Good:

“I’m still unclear on one point: who owns this step today?”

## Repeat prevention

Before finalizing a question:

1. Compare against current active question
2. Compare against recent asked questions
3. Compare semantic intent, not only wording
4. If repeated, regenerate narrower or move on

## Draft-aware routing

If enough information exists, do not ask filler questions.

Instead route to draft start.

Examples:

If only cosmetic curiosity remains → start drafting.

If one critical business field missing → ask that one question.

## Templates by scenario

## Scenario 1: Partial answer

User gave some info but missing one detail.

Pattern:

“Got it. One thing I still need to understand: <gap>. <question>”

## Scenario 2: Branch selected

Pattern:

“Understood — the main issue is <branch>. <next branch-specific question>”

## Scenario 3: Contradiction

Pattern:

“I’ve heard two different versions. Which is correct: <A> or <B>?”

## Scenario 4: Draft-ready

Pattern:

“Thanks — I have enough to start drafting now.”

## Good examples

### Example 1: Operational volume

Known:
Manual PDF forwarding issue.

Good next question:

“How many PDF files does the team usually process in a day?”

### Example 2: Ownership

Known:
Problem exists, unclear owner.

Good next question:

“Who currently handles this forwarding work?”

### Example 3: Trigger

Known:
Need automation trigger.

Good next question:

“What event should trigger the forwarding automatically?”

## Bad examples

- “Tell me more.”
- “Any other details?”
- “Please provide workflow, owner, trigger, volume, pain points, SLA.”
- repeating previous binary question
- asking already answered facts

## Quality checks before emitting question

Confirm:

- one question only
- not repeated
- plain English
- branch aware
- highest leverage
- answerable by user now
- moves workflow forward

## Escalation logic

If no good next question exists:

- start draft, or
- summarize current understanding and ask for confirmation

Do not keep asking low-value questions forever.

## Success criteria

This skill is working well when:

- conversations move steadily forward
- users rarely feel interrogated
- questions feel relevant and smart
- repeated questions decrease
- branch transitions feel natural
- drafts start at the right time