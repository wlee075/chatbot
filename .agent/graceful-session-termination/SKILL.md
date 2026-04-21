---
name: graceful-session-termination
description: End a stalled session cleanly when retry exhaustion is reached and the system still cannot gather enough usable information. Use when repeated non-progressing turns, insufficient information, unresolved conflicts, or meta-looping mean the conversation should stop instead of continuing to ask more questions.
---

# Graceful Session Termination Skill

Use this skill when the session has stopped making meaningful progress and the system should end the interaction cleanly rather than continue looping.

This skill defines:
- when a session is considered exhausted
- how to represent terminal session state
- how to explain the termination to the user
- how to preserve and expose any draft that already exists
- how the UI should behave after session end

This skill is for **terminal session control and user-facing closure**.  
It does **not** own drafting, retry counting internals, or question generation logic itself.

## Purpose

This skill prevents the chatbot from getting stuck in repetitive, unhelpful loops.

When the user keeps replying in ways that do not move the workflow forward, the system should:
- stop asking more questions
- explain why the session ended
- disable the input box
- preserve any draft already created
- provide a download option if a draft exists

## When to use

Use this skill when:
- the retry limit has been reached
- the conversation has remained in a non-progressing state
- the agent still lacks enough usable information to continue
- continuing the session would only repeat clarification or repair attempts

Typical triggers include:
- repeated non-answers
- repeated meta-looping
- insufficient information after multiple attempts
- unresolved contradictory answers
- unusable uploads or empty context after repeated attempts

Do not use this skill when:
- the user is still making clear forward progress
- the system has enough information to continue asking targeted follow-ups
- the session can be rescued with one normal clarification turn
- a temporary ambiguity has not yet crossed the retry threshold

## Core idea

Retry exhaustion is not just another clarification.

It is a **terminal session state**.

Once triggered, the system must stop acting like the conversation is still live.

## Required state contract

The system should surface a terminal session payload like:

```json
{
  "session_status": "ended_retry_limit",
  "session_end_reason": "insufficient_information",
  "session_end_message": "Unable to get enough information because key details were still missing. Session has ended.",
  "input_disabled": true,
  "draft_available": true,
  "draft_download_available": true
}
```

## Integration notes

This skill should be invoked:
	•	after retry exhaustion is confirmed
	•	before any further follow-up question is rendered
	•	before the UI decides whether the input stays enabled

This skill should NOT be invoked:
	•	during ordinary clarification
	•	during a normal repair turn
	•	while the session is still making progress

## Error handling

If session termination is triggered but the reason is missing:
	•	use a safe generic message:
	•	Unable to get enough information to continue. Session has ended.

If the session ends and draft availability is unknown:
	•	default to not showing a download button until draft existence is confirmed

## Tests to expect
	•	test_retry_limit_sets_session_status_to_ended_retry_limit
	•	test_terminal_session_message_is_rendered_with_reason
	•	test_textbox_is_disabled_after_session_end
	•	test_no_followup_question_is_rendered_after_retry_limit_end
	•	test_existing_draft_is_rendered_after_terminal_end
	•	test_download_button_shows_when_draft_exists
	•	test_terminal_session_state_is_not_overwritten_by_later_render_pass

## Summary

The graceful-session-termination skill defines how to end a stalled session cleanly once retry exhaustion is reached. It stops further questioning, explains why the session ended, disables input, and preserves any usable draft so the user does not lose work.

