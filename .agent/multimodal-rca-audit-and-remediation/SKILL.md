---
name: multimodal-rca-audit-and-remediation
description: Audits the current image and multimodal workflow against the approved RCA, reports what has already changed, identifies what still needs to change, and enforces the correct fix order before any further patching.
---

## Multimodal RCA Audit and Remediation Skill

Use this skill before changing any image or multimodal workflow code.

### Purpose

This skill prevents local patching without architectural review.

It requires the implementer to:
	•	inspect the current code
	•	compare it against the approved Multimodal Workflow Root Cause Analysis
	•	report the remaining gaps
	•	only then apply fixes in the correct order

The approved Multimodal Workflow Root Cause Analysis is the source of truth.

## Use this skill when

Use it for:
	•	image upload flows
	•	image-only submit
	•	text + image submit
	•	first-turn image flows
	•	background image context generation
	•	image summary edit/remove flows
	•	multimodal routing
	•	wait-node payload extraction
	•	multimodal context injection

Do not use it for unrelated text-only issues.

## Operating rules

You must:
	•	read the live implementation before proposing a fix
	•	compare the current code against the RCA
	•	write an audit report before patching
	•	identify which RCA phases are complete, partial, or broken
	•	point to exact files and functions for remaining work
	•	tie major issues to violated RCA invariants
	•	follow the approved fix order after the audit

You must not:
	•	patch before the audit is written
	•	assume earlier fixes are still valid
	•	remove legacy fields before migration is complete
	•	patch route labels without updating builder mappings and tests
	•	duplicate wait-node extraction logic
	•	treat session-level context as message-level ownership
	•	refine prompts before orchestration is stable

## Audit procedure

1. Inspect the current implementation

Review:
	•	app.py
	•	graph/state.py
	•	graph/nodes.py
	•	graph/routing.py
	•	graph/builder.py

Also inspect any helper modules used by:
	•	wait nodes
	•	image description
	•	context editing/removal
	•	prompt/context assembly

2. Compare against the RCA

For each RCA phase, classify it as exactly one of:
	•	not started
	•	partially complete
	•	complete
	•	implemented incorrectly

3. Write the audit report

Before patching, report all of the following:

1. What has already been changed

2. Which RCA phases are fully complete

3. Which RCA phases are partially complete

4. Which RCA invariants are still violated

5. Exact files/functions that still need changes

6. Recommended next implementation step

7. Risks if we patch the wrong layer first

Do not begin implementation before this report exists.

## RCA phases to check

### Phase 1 — Submit-contract stabilization

Check that:
	•	_handle_answer_submit always emits event_type = "ANSWER"
	•	content may be empty
	•	uploaded_files may be empty
	•	a turn is valid if content is non-empty or uploaded_files is non-empty
	•	no placeholder-text hacks remain

## Phase 2 — Route-label contract auditing

Check that:
	•	every router in routing.py is enumerated
	•	every returned label is listed
	•	every returned label exists in the matching builder.py edge map
	•	label/name mismatches are normalized
	•	route-label contract tests exist

## Phase 3 — Routing stabilization

Check that:
	•	text-only submit routes to the normal text path
	•	text + image submit routes to file_upload_intake
	•	image-only first turn routes to file_upload_intake then detect_framing
	•	post-session-context routing differs correctly for first-turn vs later-turn flows
	•	edit-save and remove-context return to the correct wait state without unintended assistant turns

## Phase 4 — Wait-node structurization

Check that:
	•	pending_event parsing is shared
	•	uploaded_files extraction is shared
	•	all wait nodes use the same extraction contract
	•	uploaded_files survives wait-node transit unless explicitly rejected
	•	undefined local attachment/context flags are removed

## Phase 5 — State-ownership consolidation

Check that:
	•	uploaded_files is turn-level state only
	•	background_generated_contexts is session-level state
	•	background_generated_contexts is append-only except for edit/remove by context_id
	•	source_turn_id is bound to the committed user turn
	•	removing an image clears future active usage only, not historical ownership
	•	legacy fields are only removed after migration is complete

## Phase 6 — Prompt refinement

Check that:
	•	_build_visual_context_block(state) is deterministic and concise
	•	visual context is injected into all intended first-response paths
	•	effective_summary = edited_summary if present else generated_summary
	•	injected contexts are capped according to RCA policy
	•	the first response after text + image attempts a bounded connection
	•	text-only behavior is unchanged

## RCA invariants

Audit every issue against these invariants:
	1.	Route target consistency
Every label returned by routing.py must exist in the relevant builder.py edge map.
	2.	Multimodal submit validity
A turn is valid if content is non-empty or uploaded_files is non-empty.
	3.	Payload preservation
If uploaded_files enters a wait node, it must leave unchanged unless explicitly rejected.
	4.	Wait-node consistency
All wait nodes must parse and emit payload fields using the same contract.
	5.	Turn vs session separation
Turn-level state such as uploaded_files and pending_event must remain distinct from session-level state such as background_generated_contexts.

## Fix order after the audit

After the audit, fixes must proceed in this order:
	1.	submit-contract stabilization
	2.	route-label contract auditing
	3.	routing stabilization
	4.	wait-node structurization
	5.	state-ownership consolidation
	6.	prompt refinement

Do not change this order unless you explicitly justify the dependency change.

Review checkpoint after each phase

After each phase, report:
	•	exact files touched
	•	tests added or updated
	•	one healthy execution trace for the relevant multimodal path
	•	which RCA invariant the phase enforces

## Summary

This skill enforces one workflow:
	•	audit first
	•	compare against the approved RCA
	•	report the remaining gaps
	•	then fix in the correct order

It exists to prevent further multimodal regressions caused by local, unscoped patching.