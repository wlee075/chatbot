---
name: question-generation
description: Generates the single best next question using structured conversation understanding inputs such as message-level meaning, concept history, known facts, blockers, and draft readiness. Use when the previous question is resolved, ambiguity must be narrowed, contradictions need clarification, or one focused next step is needed without repeating yourself.
---

# Question Generation Skill

Use this skill whenever the chatbot needs to decide **what to ask next**.

This skill works best when upstream systems already provide structured meaning from:

- `message-understanding`
- `concept-history`
- `conversation-understanding`

This skill should **consume understanding**, not recreate it.

---

# Core responsibility

This skill owns next-question creation only.

It decides:

- what single question should be asked next
- how to narrow ambiguity
- how to continue the chosen branch
- how to uncover missing business details efficiently
- how to move toward drafting readiness
- how to avoid repeating itself
- when not to ask another question

This skill does **not** own:

- raw keyword extraction
- semantic lifecycle tracking
- contradiction detection itself
- answer validation
- final truth commitment
- telemetry / logging implementation
- UI controls

Those belong upstream.

---

# Primary standard

Every turn should produce:

- **One focused question**
- **High information gain**
- **Plain English wording**
- **No repeated asks**
- **Natural conversational flow**
- **Uses known context intelligently**

Bad output:

- 5 stacked questions
- vague resets
- evaluator jargon
- asking already-known facts
- ignoring corrections already given
- broad lazy prompts

Good output:

- one sharp next step that closes the biggest gap

---

# Upstream inputs expected

Use the smallest sufficient structured context.

## Preferred Inputs

- `active_question_id`
- prior question text
- resolution result
- `known_facts`
- `current_concepts`
- `historical_concepts`
- `conflicted_concepts`
- `example_only_concepts`
- chosen branch / option
- unresolved blockers
- draft readiness state
- recent asked questions
- concept corrections / supersessions

## Example

Instead of raw chat history:

Use:

```json
{
  "known_facts": {
    "current_tool": "Excel",
    "target_output": "Send PDF to mailbox"
  },
  "conflicted_concepts": ["SAP"],
  "unresolved_blockers": ["daily volume"],
  "recent_questions": ["Who owns the process?"]
}
```
Do not require full transcript unless absolutely necessary.

## Decision hierarchy

Choose the next question in this order.

1. Highest leverage blocker

Ask the question whose answer most reduces uncertainty.

Examples:
	•	volume
	•	owner
	•	trigger
	•	frequency
	•	current pain severity
	•	workflow bottleneck
	•	missing destination
	•	source system

2. Resolve semantic conflicts

If concepts are contradicted or unclear, clarify before expanding scope.

Examples:
	•	SAP mentioned as current and negated
	•	two owners named
	•	old system vs current system unclear

3. Continue chosen branch

If the user selected a branch, stay within it.

Example:

Main issue = mapping creation

Ask mapping workflow next, not email sending.

4. Unlock drafting readiness

Ask for the one missing field preventing a useful first draft.

5. Decide not to ask

If enough is known:
	•	start drafting, or
	•	summarize and confirm

Do not keep asking filler questions forever.


---

## One-question-per-turn rule

Always ask exactly one primary question.

Allowed:
	•	one question sentence
	•	one short lead-in + one question

Not allowed:
	•	stacked interrogations
	•	bullet list forms
	•	multi-part surveys
	•	“A and B and C?” in one turn unless truly inseparable


---

## Use structured meaning properly

If concept is current

Use it naturally.

Example:

Known current tool = Excel

Ask:

“What part of the Excel process is still manual today?”

If concept is historical

Do not treat it as current.

Bad:

“How many Excel files are processed daily?”

Good:

“You mentioned Excel was used before. What tool is used now?”

If concept is example_only

Do not assume adoption.

Bad:

“How should Lark send the PDF?”

Good:

“You mentioned Lark as an example. What tool do you actually use today?”

If concept is conflicted

Clarify first.

Good:

“I heard two versions — do you currently use SAP or not?”


---

## Narrow, not broad

Every next question should become narrower or more concrete than before.

Bad:

“Tell me more about the process.”

Good:

“How many PDF files are usually sent each day?”

Bad:

“What are the pain points?”

Good:

“Which step takes the most time today: mapping, retrieval, or sending?”

---

## Branch-aware generation

If a branch is chosen, stay inside it.

Example:

Question:
Is the main issue mapping creation or file sending?

User:
Mapping creation.

Good next question:

“What part of creating the mappings is most manual today?”

Bad next question:

“How are PDFs forwarded to the mailbox?”


---

## Correction-aware generation

If the user corrected something, honor the correction immediately.

Example:

User:
Not PRD, I meant PO.

Good next question:

“What information needs to go into the PO?”

Bad next question:

“How is the PRD approved?”


---

## Plain English rule

Never expose internal system language like:
	•	unresolved blocker
	•	contradiction class
	•	missing required field
	•	ambiguity bucket
	•	semantic conflict

Rewrite naturally.

Bad:

“There is a contradiction in ownership.”

Good:

“I’ve heard two different owners mentioned. Who handles this today?”


---

## Repeat prevention

Before emitting a question:
	1.	compare against active question
	2.	compare against recent questions
	3.	compare semantic intent, not just wording
	4.	if repeated, regenerate narrower or move on

Example:

Asked before:
“How many files per day?”

Do not ask later:
“What is the daily file volume?”

That is the same question.


---

## Draft-aware routing

If enough information exists, do not ask filler questions.

Instead:
	•	start draft
	•	summarize current understanding
	•	ask for confirmation only if needed

Examples:

If all core fields known except cosmetic preference → start drafting.

If only one critical gap remains → ask that one gap.


---
## Templates by scenario

Scenario 1: Partial answer

Pattern:

“Got it. One thing I still need to understand: . ”

Example:

“Got it. One thing I still need to understand: volume. How many PDFs are sent daily?”


---

## Scenario 2: Branch selected

Pattern:

“Understood — the main issue is . ”

Example:

“Understood — the main issue is mapping. Which mapping step is still manual?”


---

## Scenario 3: Conflict

Pattern:

“I heard two different versions. Which is correct:  or ?”

Example:

“I heard two different versions. Do you currently use SAP or Excel?”


---

## Scenario 4: Historical replaced by current

Pattern:

“You mentioned  was used before. What is used now?”


---
## Scenario 5: Draft-ready

Pattern:

“Thanks — I have enough to start drafting now.”


---

## Good examples

Example 1: Operational volume

Known:
Manual PDF forwarding issue.

## Good next question:

“How many PDF files does the team usually process in a day?”


---

Example 2: Ownership

Known:
Problem exists, owner unclear.

Good next question:

“Who currently handles this forwarding work?”


---

Example 3: Trigger

Known:
Need automation trigger.

Good next question:

“What event should trigger the forwarding automatically?”


---

Example 4: Correction-aware

Known:
PRD corrected to PO.

Good next question:

“What currently creates the PO today?”


---
## Bad examples
	•	“Tell me more.”
	•	“Any other details?”
	•	“Please provide workflow, owner, trigger, volume, pain points, SLA.”
	•	asking already answered facts
	•	asking about historical systems as if current
	•	ignoring user correction
	•	repeating prior semantic intent


---

## Quality checks before emitting

Confirm:
	•	one question only
	•	not repeated
	•	plain English
	•	uses known context
	•	respects corrections
	•	branch aware
	•	highest leverage
	•	answerable now
	•	moves workflow forward


---

## Escalation logic

If no good next question exists:
	•	start draft, or
	•	summarize understanding and confirm

Do not keep interrogating low-value details.


---

## Pairing contract with upstream skills

From message-understanding

Consume:
	•	entities
	•	phrases
	•	actions
	•	semantic cues from latest message

From concept-history

Consume:
	•	current concepts
	•	historical concepts
	•	conflicts
	•	corrections
	•	supersessions

From conversation-understanding

Consume:
	•	confirmed facts
	•	blockers
	•	readiness
	•	active scope

This skill should not redo their work.


---

## Success criteria

This skill is working well when:
	•	conversations move steadily forward
	•	users rarely feel interrogated
	•	questions feel relevant and smart
	•	repeated questions decrease
	•	corrections are respected immediately
	•	contradictions are clarified early
	•	drafts start at the right time