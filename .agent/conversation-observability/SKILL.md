---
name: conversation-observability
description: Provides logs, metrics, and incident tracing for the chatbot conversation flow. Use when debugging production issues, investigating repeated questions, wrong branch routing, crashes, slow replies, state corruption, or measuring conversation quality.
---

# Conversation Observability Skill

Use this skill whenever the chatbot needs operational visibility, debugging support, or measurable quality signals.

## Core responsibility

This skill owns instrumentation and diagnostics.

It helps answer:

- What happened in this session?
- Why did the agent repeat itself?
- Why was the wrong branch chosen?
- Why was the reply slow?
- Why did state become inconsistent?
- Where did the crash occur?
- Are production quality metrics improving or degrading?

This skill does **not** own runtime question logic, answer interpretation, or next-question generation.

## What to log

Every turn should produce structured events where possible.

## Core identifiers

Always capture:

- `timestamp`
- `session_id`
- `chat_id` (if available)
- `turn_id`
- `user_message_id` (if available)
- `request_id`
- `node_name`
- `event_type`

## Conversation state fields

Capture relevant state snapshots:

- `active_question_id`
- `active_question_text`
- `question_status`
- `resolved_option_id`
- `next_action`
- `blocking_fields_count`
- `store_version`
- `draft_version`

Only log minimal necessary values. Avoid dumping sensitive raw state unless explicitly enabled.

## Key event types

### Flow events

- `turn_started`
- `turn_completed`
- `question_asked`
- `question_answered`
- `question_superseded`
- `draft_started`
- `draft_completed`

### Resolution events

- `answer_fully_resolved`
- `answer_partially_resolved`
- `branch_resolved`
- `clarification_requested`
- `invalid_value_detected`

### Quality guardrail events

- `duplicate_question_blocked`
- `repeat_question_detected`
- `wrong_branch_suspected`
- `stale_draft_blocked`
- `mirror_rebuild_triggered`

### Failure events

- `exception_raised`
- `ui_crash`
- `llm_timeout`
- `structured_output_failed`
- `state_integrity_failure`

### Performance events

- `latency_recorded`
- `llm_call_completed`
- `cache_hit`
- `cache_miss`

## When to use this skill

Use this skill when:

- Users report repeated questions
- Clarification requests are ignored
- Wrong branch was selected
- Draft started too early or too late
- Replies exceed latency targets
- UI shows inconsistent messaging
- State appears lost or corrupted
- Production rollout needs health metrics

## Incident debugging workflow

### 1. Reconstruct the turn timeline

Gather:

- user input
- active question before reply
- resolution outcome
- route taken
- next question emitted
- state after turn

### 2. Find failure boundary

Identify exact node or stage where behavior diverged:

- answer-resolution
- question-lifecycle
- question-generation
- UI renderer
- persistence layer

### 3. Determine class of failure

Classify as:

- logic bug
- stale state
- duplicate suppression failure
- semantic misclassification
- parser/schema failure
- latency timeout
- UI rendering issue

### 4. Quantify impact

Measure:

- affected sessions
- occurrence rate
- severity
- rollback need

## Key metrics

## Quality metrics

Track:

- repeated question rate
- clarification ignored rate
- wrong branch routing rate
- invalid values accepted rate
- draft rework rate
- unresolved loop rate

## Reliability metrics

Track:

- crash-free sessions
- exception count
- state integrity failures
- structured output failure rate

## Performance metrics

Track:

- P50 latency
- P95 latency
- P99 latency
- LLM latency share
- non-LLM orchestration latency

## Suggested thresholds

### P0 (Immediate action)

- state corruption
- user-visible crash spike
- silent wrong facts stored
- repeated loops across many sessions

### P1 (Urgent)

- repeated question rate > 1%
- clarification ignored rate > 1%
- wrong branch routing noticeable

### P2 (Monitor)

- latency drift
- minor UI inconsistency
- low-volume retries

## Example logs

### Example 1: Repeat blocked

```json
{
  "event_type": "duplicate_question_blocked",
  "session_id": "abc123",
  "turn_id": 7,
  "active_question_id": "q_mapping_1"
}
```

### Example 2: Invalid value
```json
{
  "event_type": "invalid_value_detected",
  "session_id": "abc123",
  "turn_id": 9,
  "reason": "hours_per_day_exceeds_24"
}
```

### Example 3: Crash trace
```json
{
  "event_type": "exception_raised",
  "node_name": "draft_node",
  "error_type": "NameError"
}
```

## Guardrails
	•	Do not log sensitive raw user data unnecessarily
	•	Prefer structured enums over long free-text dumps
	•	Keep logs queryable and consistent
	•	Log enough to reproduce incidents
	•	Separate user-facing errors from internal traces
	•	Avoid excessive telemetry on hot paths

## Good practice

### For production incidents

Log:
	•	what failed
	•	where it failed
	•	why it likely failed
	•	affected scope
	•	remediation status

### For experimentation

Compare:
	•	old vs new repeat rates
	•	old vs new latency
	•	branch accuracy before/after
	•	clarification success before/after

## Success criteria

This skill is working well when:
	•	incidents can be root-caused quickly
	•	repeated-question bugs are measurable
	•	regressions are caught early
	•	rollout decisions use evidence
	•	logs explain user complaints clearly
	•	quality trends improve over time