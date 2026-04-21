---
name: image-description-session-context
description: Converts uploaded-image-description output into editable structured session context. Shows a review popup on first upload, allows edit or revert, and keeps the approved context available until removed or replaced.
---

# Image Description Session Context Skill

Use this skill only **after** `uploaded-image-description` has successfully produced image descriptions.

This skill is the boundary for:

- converting generated image descriptions into structured editable context
- presenting a first-upload review popup
- allowing the user to edit, submit, or revert the generated draft
- storing approved image context for the current session
- removing or replacing active image context later

It does not own:

- image generation or image description itself
- file validation
- OCR
- PDF analysis
- business interpretation
- downstream question selection
- permanent memory storage

---

# Purpose

This skill transforms successful image-description output into a user-reviewable structured draft.

The approved draft becomes session context that downstream conversation steps may use until:

- user removes it
- user replaces it
- session ends (if session-scoped storage is used)

---

# Upstream dependency

The image description is assumed to already be generated (for example by Gemini through `uploaded-image-description`).

This skill consumes normalized description output only.

---

# Pairing rule with uploaded-image-description

This skill must only run when:

- `image_description_status = "described"`
- `described_images` contains at least one item

This skill must not run when:

- `image_description_status = "no_accepted_images"`
- `image_description_status = "failed"`
- no described images exist

---

# When to use

Use this skill when:

- uploaded images should provide persistent conversation context
- the user should review generated image context before activation
- downstream questioning benefits from approved visual context

Do not use this skill when:

- no image descriptions succeeded
- the workflow does not need persistent image context
- silent background context is preferred with no user review step

---

# Core behavior

## Step 1 — Build structured draft

Convert `described_images` into a structured text draft using fixed sections.

Required sections:

```text
[what_is_going_on]
[entities]
[visible_text]
[layout_and_structure]
[key_details]
[uncertainties]
```
For multiple uploaded images:
	•	preserve upload order
	•	create one block per image
	•	merge into one session draft


## Step 2 — Show review popup on first upload

Before context becomes active, show popup containing:
	•	editable text area prefilled with generated draft
	•	Submit button
	•	Revert to generated description button

Popup must appear before activation.

## Step 3 — Handle user actions


### Submit without edits

Store original generated draft as active context.

### Submit after edits

Store edited draft as active context.

### Revert

Reset editable field to original generated draft for current upload batch.

## Step 4 — Persist context

After submit:
	•	active context becomes available to later turns
	•	downstream may use it as bounded supporting context
	•	remains until removed or replaced

## Scope and lifetime

This context is session-scoped.

It must:
	•	remain available during current conversation
	•	be replaceable
	•	be removable on user request

It must not:
	•	auto-save to permanent memory
	•	persist into unrelated future sessions unless another memory system owns that

## Input contract
```
{
  "image_description_status": "described",
  "described_images": [
    {
      "file_id": "string",
      "filename": "string",
      "high_level_description": "string",
      "visible_elements": ["string"],
      "uncertainties": ["string"]
    }
  ]
}
```

## Output contract
{
  "session_context_status": "pending_user_review | active | removed | failed",
  "context_source": "generated | user_edited",
  "generated_context_text": "string",
  "active_context_text": "string",
  "popup_required": true,
  "popup_state": {
    "is_visible": true,
    "can_edit": true,
    "can_submit": true,
    "can_revert": true
  },
  "context_version": 1
}

## Status enums

session_context_status must be exactly one of:
	•	pending_user_review
	•	active
	•	removed
	•	failed

context_source must be exactly one of:
	•	generated
	•	user_edited

Do not invent new values.

--

## Draft schema

Each image block must follow:

```
[image]
<filename>

[what_is_going_on]
<string>

[entities]
- <entity>
- <entity>

[visible_text]
- <clearly legible text>

[layout_and_structure]
<string>

[key_details]
- <detail>
- <detail>

[uncertainties]
- <uncertainty>
- <uncertainty>
```

Section rules

[what_is_going_on]

Short naive summary of what the image appears to show.

Examples:
	•	screenshot of dashboard with filters
	•	scanned form with fields
	•	flowchart with connected boxes

[entities]

Only visible concrete objects/components.

Examples:
	•	table
	•	button
	•	dropdown
	•	chart
	•	person
	•	laptop

[visible_text]

Only clearly readable text.

Do not guess.

[layout_and_structure]

Describe spatial arrangement.

Examples:
	•	search bar at top, table below
	•	fields stacked vertically
	•	boxes linked left to right

[key_details]

Important visible facts.

Examples:
	•	multiple rows of values
	•	checkbox present
	•	warning banner visible

[uncertainties]

Anything unreadable, blurry, cropped, ambiguous.

---

## Draft generation rules

This skill must:
	•	remain faithful to upstream description
	•	preserve upload order
	•	keep concise wording
	•	separate certainty from uncertainty

This skill must not:
	•	infer business goals
	•	add OCR text not clearly visible
	•	reinterpret image beyond source description
	•	hallucinate missing content

---

## UI behavior

Initial state
	•	popup visible
	•	editable text area filled with generated draft
	•	submit enabled
	•	revert enabled

After submit
	•	popup closes
	•	context activates

After revert
	•	editable field resets to generated draft
	•	popup remains open

---

## Decision rules

### If described images exist

Return:
```
{
  "session_context_status": "pending_user_review"
}
```
### If user submits
Return:
```
{
  "session_context_status": "active"
}
```
### If user removes later
Return:
```
{
  "session_context_status": "removed"
}
```
### If storage or construction fails
Return:
```
{
  "session_context_status": "failed"
}
```
---
## Ownership boundaries

This skill must:
	•	consume successful image-description output
	•	build structured reviewable draft
	•	support edit / submit / revert
	•	store approved session context

This skill must not:
	•	describe images itself
	•	inspect raw files
	•	perform OCR workflows
	•	create PRD/business conclusions
	•	store permanent memory

---

## Downstream handoff

If active:

Downstream may use:
	•	active_context_text
	•	context_source
	•	context_version

Downstream may assume:
	•	user reviewed before activation
	•	content came from image descriptions
	•	context is session scoped

Downstream must not assume:
	•	OCR completed
	•	PDFs included
	•	permanent memory exists
	•	business meaning approved

---

## Example generated draft
```
[image]
dashboard.png

[what_is_going_on]
This appears to be a screenshot of a dashboard showing table data and filters.

[entities]
- table
- search bar
- dropdown filters
- export button

[visible_text]
- Status
- Date Range
- Export to CSV

[layout_and_structure]
A search area appears near the top, filters below it, and a large table occupies most of the page.

[key_details]
- multiple rows of numeric values are visible
- filters appear selectable

[uncertainties]
- some column headers are too small to read
- smaller table values are unclear
```

## Tests to expect
	•	test_runs_only_after_described_status
	•	test_popup_shown_before_activation
	•	test_generated_draft_uses_required_sections
	•	test_user_can_edit_draft
	•	test_revert_restores_original_draft
	•	test_submit_activates_context
	•	test_context_persists_until_removed
	•	test_removed_context_not_used
	•	test_upload_order_preserved
	•	test_no_hallucinated_text_added
	•	test_uncertainties_separated
	•	test_multiple_images_create_multiple_blocks

## Summary

The image-description-session-context skill converts successful uploaded image descriptions into a structured editable draft with fixed sections such as [what_is_going_on], [entities], and [uncertainties]. The user reviews this draft in a popup, may edit or revert it, and the approved result becomes session context available until removed or replaced.