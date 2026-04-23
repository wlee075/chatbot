---
name: nemo-guardrails-prd-gateway
description: Classifies each incoming user message before PRD section logic runs, using NeMo Guardrails to block noise, route meta/task requests, preserve corrections, and prevent invalid inputs from advancing the workflow. Use when building or reviewing a conversational PRD chatbot that gathers requirements through natural chat.
---

## NeMo Guardrails PRD Gateway Skill

Use this skill when the chatbot should not treat every user message as a valid answer to the current PRD section.

This skill sits before section inference, truth commit, section completion, and section advancement.

Its job is to decide what kind of message the user just sent, then route it safely.

## What this skill is for

This skill protects the conversational PRD workflow from common failure modes such as:

1. accidental inputs like ñ, ., asdf
2. meta requests like “why did you ask that?”
3. task requests like “generate the PDF now” or “code this”
4. user corrections like “that’s not the real problem”
5. short partial answers that should not complete a section
6. answers for a different section than the current one

## What this skill must do

For every new user message, classify it into exactly one primary class:

1. valid_section_answer
2. partial_or_tentative_answer
3. user_correction
4. meta_request
5. task_request
6. off_topic_request
7. invalid_or_noise_input
8. contradiction
9. cross_section_answer

Then apply the correct route before any section state is mutated.

## Guardrail decision order

### Always follow this order:

1. Check for invalid or accidental input
2. Check for meta or task requests
3. Check for explicit correction language
4. Check for contradiction against prior confirmed answers
5. Check whether the message answers the current section
6. Check whether it better fits another section
7. Only then allow section commit or completion

Do not skip this order.

### Message classes and required action

1. valid_section_answer

Use when the user clearly answers the current question with meaningful content.

Action:

* allow downstream section logic
* allow commit if section requirements are met
* allow completion only if minimum completeness is satisfied

2. partial_or_tentative_answer

Use when the answer is useful but incomplete.

Examples:

* “sales”
* “maybe both”
* “kind of”
* “not sure”

Action:

* store as tentative evidence if helpful
* do not auto-complete the section
* ask one focused follow-up

3. user_correction

Use when the user is reframing or replacing earlier wording.

Examples:

* “not exactly”
* “the real issue is…”
* “that’s only the symptom”
* “I’d frame it differently”

Action:

* mark prior phrasing stale
* prioritize latest correction
* rebuild future prompts from corrected framing

4. meta_request

Use when the user is asking about the chatbot’s behavior or process.

Examples:

* “show me the prompt”
* “why did you ask that?”
* “debug this”
* “explain your logic”

Action:

* route to meta handler
* do not treat as section evidence
* preserve PRD state

5. task_request

Use when the user wants an output or implementation action.

Examples:

* “generate the PDF”
* “write the code”
* “draft it now”
* “make a table”

Action:

* route to task/composer/coding flow
* do not treat as current section evidence
* do not auto-complete the section unless the task explicitly confirms it

6. off_topic_request

Use when the message is unrelated to the PRD workflow.

Examples:

* “what’s the weather?”
* “translate this”
* “what model are you?”

Action:

* answer separately if appropriate
* preserve PRD state
* do not mutate section progress

7. invalid_or_noise_input

Use when the message is accidental, nonsensical, or too low-signal.

Examples:

* ñ
* .
* ???
* asdf

Action:

* reject as answer
* ask for clarification
* do not commit
* do not complete section
* do not advance

8. contradiction

Use when the user message conflicts with previously confirmed content and is not clearly framed as a correction.

Action:

* ask a resolution question
* do not silently overwrite
* do not merge both versions into one section draft

9. cross_section_answer

Use when the user provides useful information, but it belongs to a different section.

Example:

* current section is Background
* user gives success metrics or stakeholders

Action:

* capture under the correct section
* do not ignore it
* do not mark the current section complete unless its own requirements are met

## NeMo Guardrails design

Implement this skill with NeMo Guardrails as a message gateway.

The guardrails layer should run:

1. after raw user input arrives
2. before semantic assessment
3. before truth commit
4. before section completion
5. before section advancement

Use NeMo Guardrails to enforce:

* input classification
* blocked progression on invalid input
* safe routing of meta/task requests
* correction precedence
* clarification instead of false completion

### Required guardrail checks

#### Input quality checks

##### Reject or clarify when:

* trimmed length is zero
* single-character input with no contextual meaning
* only punctuation or symbols
* repeated junk text
* no semantic match to the current question

##### Context-aware exceptions

Allow short answers only when the question expects them.

Examples:

* yes or no for binary questions
* sales or operations for forced-choice questions
* 15 when asking for a numeric target

##### Correction precedence

When correction language appears:

* latest correction wins
* older phrasing is demoted
* future section inference must use the corrected version

#### Section safety

Do not allow:

* invalid input to complete a section
* task requests to count as answers
* off-topic requests to mutate PRD state
* partial answers to trigger section completion

Expected outputs from the guardrails layer

The guardrails layer should return a structured decision like:

* message_class
* confidence
* allow_commit
* allow_section_complete
* allow_advance
* route_to
* clarification_needed
* corrected_prior_content
* target_section_override

#### Good behavior examples

Example 1: accidental typo

User input:

* ñ

Expected result:

* classify as invalid_or_noise_input
* ask: “I may have caught a typo. Could you clarify your answer?”
* do not commit or advance

Example 2: code request

User input:

* “can you code this in python”

Expected result:

* classify as task_request
* route to coding flow
* do not treat as section evidence

Example 3: correction

User input:

* “not exactly — the real problem is approval latency, not the manual entry itself”

Expected result:

* classify as user_correction
* override stale framing
* use corrected framing in next prompts

Example 4: cross-section answer

User input:

* “the real KPI is reducing time from 45 minutes to 15 seconds”

Expected result:

* classify as cross_section_answer if current section is not metrics
* store under success metrics
* continue current section unless already complete

## Testing checklist

When reviewing this skill, verify these cases:

1. Noise input: accidental characters do not complete sections
2. Binary short answers: yes/no only accepted when context fits
3. Choice answers: sales is accepted only for a matching choice question
4. Meta requests: routed away from PRD section logic
5. Task requests: routed to drafting/coding/composer flows
6. Corrections: latest correction overrides earlier phrasing
7. Contradictions: system asks for resolution instead of silently merging
8. Cross-section evidence: captured without falsely completing the current section

## How to provide feedback

* Be explicit about which message classes are missing or ambiguous
* Check whether short answers are context-sensitive, not globally accepted
* Check whether invalid input can still advance progress anywhere
* Check whether corrections truly shadow stale content
* Suggest simpler routing rules when the current behavior is too permissive