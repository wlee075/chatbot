---
name: uploaded-image-description
description: Describes 1 or more accepted uploaded JPG or PNG images using bounded, plain-English visual observations only. Use after file-upload-intake has already accepted image files and downstream needs image description before further questioning or analysis.
---

# Uploaded Image Description Skill

Use this skill only **after** `file-upload-intake` has successfully accepted uploaded image files.

This skill is the next boundary after file intake for **image description only**.

It owns only:
- selecting accepted image files from the normalized intake payload
- producing bounded visual descriptions of uploaded JPG/PNG images
- distinguishing visible content from uncertainty
- returning structured image-description output for downstream use

It does not own:
- OCR as a primary task
- PDF analysis
- drafting
- file validation
- file storage
- downstream question selection
- business interpretation
- inferring intent beyond what is visibly supported by the image

## Purpose

This skill converts accepted uploaded image files into clear, bounded visual descriptions that downstream logic can use safely.

It should describe:
- what is visibly present
- the overall scene or layout
- major objects, text regions, diagrams, screenshots, forms, charts, or UI elements
- uncertainty where visibility is weak or ambiguous

It should not:
- invent unseen content
- infer business meaning that is not visually supported
- claim OCR-quality extraction unless another OCR-specific step owns that
- describe PDFs
- revalidate file types

## Pairing rule with file-upload-intake

This skill must only run when:
- `upload_status` is `accepted` or `accepted_partial`
- and at least one accepted file has `file_type` of `jpg` or `png`

This skill must not run when:
- `upload_status` is `rejected` or `invalid_payload`
- accepted files are only PDFs
- no accepted image files exist

## Supported file types

This skill may describe only:
- `jpg`
- `png`

It must ignore:
- `pdf`
- rejected files
- unsupported file types

## When to use

Use this skill when:
- the user uploaded screenshots, photos, diagrams, or interface captures
- downstream logic needs a neutral image description before asking the next question
- the workflow needs bounded visual context from uploaded images

Do not use this skill when:
- the upload contains only PDFs
- the workflow requires OCR-specific extraction rather than visual description
- the workflow already has sufficient image understanding
- the task is purely file validation

## Input contract

Expected input:

```json
{
  "upload_status": "accepted | accepted_partial",
  "accepted_files": [
    {
      "file_id": "string",
      "filename": "string",
      "file_type": "jpg | png | pdf"
    }
  ]
}
```

## Preconditions
	•	upload_status must already be valid from file-upload-intake
	•	accepted_files must already be normalized
	•	this skill must only consume accepted image files

## Selection rules
	1.	Preserve upload order
	2.	Select only files where file_type is jpg or png
	3.	Ignore accepted PDFs entirely
	4.	Ignore all rejected files
	5.	If no accepted image files remain, return a structured no-image result and do not invent analysis

## Output contract

Return a structured result in this format:
```
{
  "image_description_status": "described | no_accepted_images | failed",
  "described_images": [
    {
      "file_id": "string",
      "filename": "string",
      "high_level_description": "string",
      "visible_elements": [
        "string"
      ],
      "uncertainties": [
        "string"
      ]
    }
  ],
  "needs_followup": false
}
```
Status enums

image_description_status must be exactly one of:
	•	described
	•	no_accepted_images
	•	failed

Do not invent additional status values.

Description rules

For each accepted image:
	•	provide one short high-level description
	•	list the main visible elements
	•	explicitly separate uncertainty from confirmed visual observations

Good description style
	•	“The image appears to be a screenshot of a dashboard with a table and several filter controls.”
	•	“The photo shows a handwritten sheet with rows of numbers and column labels.”
	•	“The image looks like a process diagram with boxes connected by arrows.”

Bad description style
	•	“This definitely proves the warehouse system is broken.”
	•	“The user wants to automate procurement.”
	•	“This PDF likely contains requirements.”
	•	“The chart says revenue dropped 20%” unless that is clearly legible and directly visible

Observation boundaries

This skill must:
	•	describe only what is visible
	•	use phrases like “appears to show,” “looks like,” or “visible in the image” when appropriate
	•	mention unreadable or ambiguous areas explicitly
	•	keep descriptions concise and structured

This skill must not:
	•	hallucinate unreadable text
	•	infer user goals from image content alone
	•	summarize the entire business problem unless visible in the image
	•	treat uncertain content as confirmed fact

OCR boundary

This skill is not an OCR skill.

It may mention:
	•	“there appears to be text”
	•	“a table or form is visible”
	•	“the screenshot contains labels/buttons”

It must not:
	•	claim exact text extraction unless that text is clearly legible and visually obvious
	•	attempt full transcription as its core task

Decision rules

If accepted image files exist

Return:
	•	image_description_status = "described"
	•	one structured description per accepted image
	•	needs_followup = false unless downstream policy says more clarification is needed

If accepted files exist but none are images

Return:
	•	image_description_status = "no_accepted_images"
	•	described_images = []
	•	needs_followup = true

If image description cannot be completed safely

Return:
	•	image_description_status = "failed"
	•	described_images = []
	•	needs_followup = true

Ownership boundaries

This skill must:
	•	consume only accepted image metadata
	•	preserve upload order
	•	produce bounded visual descriptions
	•	separate observation from uncertainty
	•	emit structured image-description output

This skill must not:
	•	revalidate files
	•	inspect rejected files
	•	parse PDFs
	•	perform OCR-heavy extraction
	•	decide the next question
	•	generate PRD text
	•	infer domain conclusions beyond visible content

Downstream handoff

If image_description_status = "described", this skill may hand off only:
	•	described_images
	•	image_description_status
	•	needs_followup

Downstream may assume:
	•	only accepted JPG/PNG images were described
	•	descriptions are bounded to visible content
	•	uncertainties are explicitly surfaced

Downstream must not assume:
	•	OCR was completed
	•	PDFs were analyzed
	•	business meaning was inferred
	•	rejected files were used

Recommended follow-up behavior

After successful image description:
	•	acknowledge that the uploaded images were reviewed
	•	use the image descriptions as bounded supporting context
	•	ask only one relevant next question if the workflow requires it

After no_accepted_images:
	•	state that no accepted JPG/PNG images were available for image description
	•	continue only if another skill should handle PDFs or other accepted files

After failure:
	•	state that the images could not be described safely
	•	do not invent content
	•	request clearer uploads only if the workflow requires that

Examples

Valid image description input
```
{
  "upload_status": "accepted",
  "accepted_files": [
    {
      "file_id": "img_001",
      "filename": "dashboard.png",
      "file_type": "png"
    },
    {
      "file_id": "img_002",
      "filename": "form.jpg",
      "file_type": "jpg"
    }
  ]
}
```

Valid image description output
```
{
  "image_description_status": "described",
  "described_images": [
    {
      "file_id": "img_001",
      "filename": "dashboard.png",
      "high_level_description": "The image appears to be a screenshot of a web application dashboard with a grid of data and several filter controls.",
      "visible_elements": [
        "A grid of rows and columns with numeric values",
        "A search bar at the top",
        "Dropdown filters labeled 'Status' and 'Date Range'",
        "A button labeled 'Export to CSV'"
      ],
      "uncertainties": [
        "The exact meaning of the column headers is not fully clear",
        "The specific values in the grid are too small to read precisely"
      ]
    },
    {
      "file_id": "img_002",
      "filename": "form.jpg",
      "high_level_description": "The image shows a scanned form with fields for personal information and project details.",
      "visible_elements": [
        "Fields for 'Name', 'Email', and 'Phone'",
        "A section labeled 'Project Information'",
        "A checkbox labeled 'NDA Required'",
        "A signature line at the bottom"
      ],
      "uncertainties": [
        "The handwriting in the signature field is not legible",
        "Some handwritten notes in the margins are unclear"
      ]
    }
  ],
  "needs_followup": false
}
```

No accepted images output
```
{
  "image_description_status": "no_accepted_images",
  "described_images": [],
  "needs_followup": true
}
```

Failed image description output
```
{
  "image_description_status": "failed",
  "described_images": [],
  "needs_followup": true
}
```

Tests to expect
	•	test_only_jpg_and_png_are_described
	•	test_accepted_pdfs_are_ignored
	•	test_rejected_files_are_ignored
	•	test_upload_order_is_preserved
	•	test_high_level_description_is_provided
	•	test_visible_elements_are_listed
	•	test_uncertainties_are_explicitly_surfaced
	•	test_no_ocr_claims_are_made
	•	test_no_business_meaning_is_inferred
	•	test_no_followup_is_added_unless_needed
	•	test_no_accepted_images_returns_no_accepted_images_status
	•	test_failed_description_returns_failed_status
	•	test_downstream_receives_only_described_images
	•	test_downstream_does_not_see_rejected_files
	•	test_downstream_does_not_see_pdfs

Summary

The uploaded-image-description skill converts accepted JPG and PNG images into bounded visual descriptions after file-upload-intake has already accepted the files. It preserves upload order, distinguishes visible content from uncertainty, and returns structured output that downstream skills can use safely without making unverified assumptions about OCR, business meaning, or rejected files.