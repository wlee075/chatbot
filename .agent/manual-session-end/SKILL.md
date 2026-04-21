---
name: manual-session-end
description: Adds and handles a user-facing button to end the current session immediately. Use when the app should let the user manually stop the session before retry exhaustion. Pair with graceful-session-termination to disable input, show a final message, and optionally expose a draft download.
---
## Manual Session End Skill

When handling a manual end-session button, follow these steps:

### Purpose

This skill lets the user explicitly end the current session on demand.

It pairs with graceful-session-termination, but the trigger is different:
	•	graceful-session-termination is used when the system decides the session is exhausted
	•	manual-session-end is used when the user clicks a button to stop the session now

### When to use

#### Use this skill when:
	•	the UI includes an End Session button
	•	the user clicks that button
	•	the current session should be closed immediately
	•	the app should stop accepting new input for the current session

#### Do not use this skill when:
	•	the session is still active and no end-session button was clicked
	•	retry exhaustion logic alone should decide termination
	•	the user is only cancelling one question rather than ending the whole session

#### What this skill does
	1.	Detects that the user clicked End Session
	2.	Marks the current session as ended
	3.	Disables the textbox/input for that session
	4.	Shows a terminal message
	5.	If a draft exists, keeps it visible and shows a download button
	6.	If no draft exists, shows only a simple session-ended message

#### What this skill must not do
	•	decide retry exhaustion
	•	generate new follow-up questions
	•	rewrite the draft
	•	delete existing draft content
	•	continue normal elicitation after session end

## Required state contract

Set a terminal state like:
```
{
  "session_status": "ended_manual",
  "session_end_reason": "user_ended_session",
  "session_end_message": "Session ended.",
  "input_disabled": true,
  "draft_available": false,
  "draft_download_available": false
}
```
If a draft exists:
```
{
  "session_status": "ended_manual",
  "session_end_reason": "user_ended_session",
  "session_end_message": "Session ended.",
  "input_disabled": true,
  "draft_available": true,
  "draft_download_available": true
}
```

## Button behavior

### When the button is clicked
	•	immediately stop the live session
	•	do not ask another question
	•	do not continue drafting
	•	do not continue retry or repair loops

### After click
	•	textbox should be greyed out or disabled
	•	session should render a final assistant message
	•	if draft exists, show it
	•	if draft exists, show a download button
	•	if no draft exists, show only:
	•	Session ended.

### UI behavior

#### If no draft exists

Show:
	•	terminal message: Session ended.
	•	disabled textbox

Do not show:
	•	download button
	•	follow-up question
	•	retry prompt

#### If draft exists

Show:
	•	terminal message: Session ended.
	•	draft panel
	•	download button
	•	disabled textbox

### Integration with graceful-session-termination

Use this skill together with graceful-session-termination like this:
	•	graceful-session-termination
	•	system-triggered ending
	•	usually because of retry exhaustion or lack of progress
	•	manual-session-end
	•	user-triggered ending
	•	caused by explicit button click

Both should converge on the same UI rules:
	•	ended session state
	•	disabled input
	•	preserve draft if available
	•	show download button only if draft exists

Recommended implementation flow
	1.	Add an End Session button in the UI
	2.	On click, emit a manual end-session event
	3.	Route that event to a handler using this skill
	4.	Set:
	•	session_status = "ended_manual"
	•	session_end_reason = "user_ended_session"
	•	session_end_message = "Session ended."
	•	input_disabled = true
	5.	Check whether a draft exists
	6.	If yes:
	•	keep draft visible
	•	enable download button
	7.	If no:
	•	show only the terminal message

## Decision rules

### If the user clicks End Session and no draft exists

Return:
	•	session_status = "ended_manual"
	•	session_end_message = "Session ended."
	•	draft_available = false
	•	draft_download_available = false

### If the user clicks End Session and a draft exists

Return:
	•	session_status = "ended_manual"
	•	session_end_message = "Session ended."
	•	draft_available = true
	•	draft_download_available = true

### Tests to expect
	•	test_manual_end_session_sets_session_status_to_ended_manual
	•	test_manual_end_session_disables_textbox
	•	test_manual_end_session_shows_session_ended_message_when_no_draft
	•	test_manual_end_session_preserves_existing_draft
	•	test_manual_end_session_shows_download_button_when_draft_exists
	•	test_manual_end_session_stops_followup_question_rendering

### Summary

The manual-session-end skill adds a user-controlled End Session button that cleanly closes the current session. It disables input, shows a final message, and preserves any existing draft with a download button. If no draft exists, it simply shows: Session ended.
