---
name: file-upload-intake
description: Accepts and validates 1 or more uploaded JPG, PNG, or PDF files for the current task. Use when the user provides supporting files that must be checked and normalized before any downstream analysis.
---

# File Upload Intake Skill

When handling uploaded files, follow these steps:

## Purpose

This skill defines the intake contract for uploaded files used in the current workflow.

It owns only:
- file presence validation
- file type validation
- metadata normalization
- structured acceptance or rejection output
- downstream handoff of accepted file metadata

It does not own:
- OCR
- PDF parsing
- image understanding
- summarization
- drafting
- long-term storage
- downstream analyzer selection
- UI rendering beyond upload acknowledgement

## Supported file types

Accept only:
- `.jpg`
- `.jpeg`
- `.png`
- `.pdf`

Allowed MIME types:
- `image/jpeg`
- `image/png`
- `application/pdf`

## When to use

Use this skill when:
- the user uploads 1 or more screenshots, photos, diagrams, or PDFs
- the workflow requires file-backed context before the next step
- uploaded files must be validated before downstream processing
- the system needs a normalized file intake result

Do not use this skill for:
- generic chat without attachments
- OCR-specific workflows
- full image or document analysis
- storage workflows
- non-visual file handling beyond the allowed types

## Input contract

Expected input:

```json
{
  "uploaded_files": [
    {
      "file_id": "string",
      "filename": "string",
      "mime_type": "string",
      "size_bytes": 12345
    }
  ]
}
```
## Each file entry must include:
	•	file_id
	•	filename
	•	mime_type
	•	size_bytes

If the payload is malformed or required metadata is missing, the skill must reject the upload.

## Status enums

upload_status must be exactly one of:
	•	accepted
	•	accepted_partial
	•	rejected
	•	invalid_payload

Do not invent additional status values.

## Rejection reasons

reason for each rejected file or request must be one of:
	•	no_files_uploaded
	•	unsupported_file_type
	•	malformed_file_payload
	•	missing_required_metadata
	•	empty_file

Do not invent additional rejection reasons unless the contract is updated.

## Validation rules
	1.	At least 1 file must be provided.
	2.	Upload order must be preserved.
	3.	Unsupported files must be explicitly rejected.
	4.	Invalid files must never be silently dropped.
	5.	Accepted files must be normalized into a consistent metadata shape.
	6.	File contents must not be inspected by this skill.
	7.	If the payload itself is malformed, no downstream handoff is allowed.

## Output contract

Return a structured result in this format:
```
{
  "upload_status": "accepted | accepted_partial | rejected | invalid_payload",
  "accepted_files": [
    {
      "file_id": "string",
      "filename": "string",
      "file_type": "jpg | png | pdf"
    }
  ],
  "rejected_files": [
    {
      "filename": "string",
      "reason": "no_files_uploaded | unsupported_file_type | malformed_file_payload | missing_required_metadata | empty_file"
    }
  ],
  "needs_followup": true
}
```

## Decision rules

### If no files are uploaded

Return:
	•	upload_status = "rejected"
	•	accepted_files = []
	•	rejected_files containing one rejection with reason = "no_files_uploaded"
	•	needs_followup = true

### If all files are valid

Return:
	•	upload_status = "accepted"
	•	all normalized files in accepted_files
	•	rejected_files = []
	•	needs_followup = false

### If some files are valid and some are invalid

Return:
	•	upload_status = "accepted_partial"
	•	only valid files in accepted_files
	•	all invalid files in rejected_files
	•	needs_followup = true

### If all files are invalid

Return:
	•	upload_status = "rejected"
	•	accepted_files = []
	•	all invalid files in rejected_files
	•	needs_followup = true

### If the payload is malformed

Return:
	•	upload_status = "invalid_payload"
	•	accepted_files = []
	•	rejected_files with the relevant canonical reason
	•	needs_followup = true

Do not attempt partial recovery from a malformed payload.

## Ownership boundaries

### This skill must:
	•	validate whether files are present
	•	validate whether each file type is allowed
	•	validate whether required metadata exists
	•	normalize accepted file metadata
	•	emit a structured intake result
	•	preserve upload order

### This skill must not:
	•	inspect file contents
	•	infer meaning from images or PDFs
	•	summarize file contents
	•	parse PDFs
	•	perform OCR
	•	choose the next question to ask
	•	route to a specific analyzer
	•	draft downstream outputs

## Downstream handoff

### If upload_status is accepted or accepted_partial, this skill may hand off only:
	•	accepted_files
	•	upload_status
	•	needs_followup

### Downstream may assume:
	•	accepted files passed type validation
	•	accepted files include normalized metadata
	•	accepted files remain in original upload order

### Downstream must not assume:
	•	files have been parsed
	•	files have been summarized
	•	OCR has been performed
	•	file contents have been interpreted
	•	any rejected files are available for processing

## If upload_status is rejected or invalid_payload, no downstream analysis handoff is allowed.

### How to provide follow-up behavior

#### After successful intake:
	•	acknowledge the number of accepted files
	•	pass only accepted file metadata downstream
	•	do not claim the files have been interpreted

#### After partial intake:
	•	state which files were accepted
	•	state which files were rejected and why
	•	restate accepted formats
	•	pass only accepted file metadata downstream

#### After failed intake:
	•	state that the upload could not proceed
	•	list rejected files or request-level rejection reason
	•	restate accepted formats: JPG, PNG, PDF
	•	do not proceed to analysis

## Examples

### Valid upload

Input:
```
{
  "uploaded_files": [
    {
      "file_id": "file_1",
      "filename": "diagram.png",
      "mime_type": "image/png",
      "size_bytes": 482001
    },
    {
      "file_id": "file_2",
      "filename": "spec.pdf",
      "mime_type": "application/pdf",
      "size_bytes": 928331
    }
  ]
}
```

Output:
```
{
  "upload_status": "accepted",
  "accepted_files": [
    {
      "file_id": "file_1",
      "filename": "diagram.png",
      "file_type": "png"
    },
    {
      "file_id": "file_2",
      "filename": "spec.pdf",
      "file_type": "pdf"
    }
  ],
  "rejected_files": [],
  "needs_followup": false
}
```
### Mixed upload

Input:
```
{
  "uploaded_files": [
    {
      "file_id": "file_1",
      "filename": "screen.jpg",
      "mime_type": "image/jpeg",
      "size_bytes": 210944
    },
    {
      "file_id": "file_2",
      "filename": "notes.docx",
      "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
      "size_bytes": 55312
    }
  ]
}
```
Output:
```
{
  "upload_status": "accepted_partial",
  "accepted_files": [
    {
      "file_id": "file_1",
      "filename": "screen.jpg",
      "file_type": "jpg"
    }
  ],
  "rejected_files": [
    {
      "filename": "notes.docx",
      "reason": "unsupported_file_type"
    }
  ],
  "needs_followup": true
}
```

### No files uploaded

Input:
```
{
  "uploaded_files": []
}
```

Output:
```
{
  "upload_status": "rejected",
  "accepted_files": [],
  "rejected_files": [
    {
      "filename": "",
      "reason": "no_files_uploaded"
    }
  ],
  "needs_followup": true
}
```

### Malformed payload

Input:
```
{
  "files": [
    {
      "name": "diagram.png"
    }
  ]
}
```

Output:
```
{
  "upload_status": "invalid_payload",
  "accepted_files": [],
  "rejected_files": [
    {
      "filename": "",
      "reason": "malformed_file_payload"
    }
  ],
  "needs_followup": true
}
```