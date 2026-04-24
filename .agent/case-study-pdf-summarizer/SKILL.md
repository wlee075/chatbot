---
name: case-study-pdf-summarizer
description: Summarizes PDF case studies from <project_working_directory>/case-study/ into .txt files in <project_working_directory>/case_study_summary/. Use when you need structured 5-question summaries plus concrete next steps for adapting the application.
---

# Case Study PDF Summarizer Skill

Use this skill when you need to process PDF case studies in a folder and generate one structured text summary per file.

## What this skill does

For every PDF in:

`<project_working_directory>/case-study/`

this skill should:

1. Read the PDF content.
2. Extract the text reliably.
3. Summarize it using the required 5-question prompt.
4. Add practical next steps for how the application should adapt.
5. Save one `.txt` file per PDF into:

`<project_working_directory>/case_study_summary/`

---

## When to use this skill

- Use this when source material is stored as PDF case studies.
- Use this when each PDF needs its own summary file.
- Use this when summaries must follow a repeatable structure.
- Use this when outputs should include implementation recommendations.

---

## Input / Output Contract

### Input Directory

`<project_working_directory>/case-study/`

### Output Directory

`<project_working_directory>/case_study_summary/`

Create the output directory if it does not exist.

### Output Naming Rule

For:

`customer_onboarding.pdf`

write:

`customer_onboarding_summary.txt`

---

## Required Summary Prompt

Use this exact prompt on extracted PDF text:

```text
<instructions>
You are a summarizer. You write a summary of the input using following steps:
1.0: Analyze the <inputText> below and generate 5 essential questions that, when answered, capture the main points and core meaning of the text.

2.0: When formulating your questions:
2.1: Address the central theme or argument.
2.2: Identify key supporting ideas.
2.3: Highlight important facts or evidence.
2.4: Reveal the author's purpose or perspective.
2.5: Explore any significant implications or conclusions.

3.0: Answer each question in 4-5 sentences. Include a specific example to illustrate your point.
</instructions>
<inputText>
```
Append extracted PDF text after <inputText>.

## Output File Format

Each .txt file must contain:

```text
Source File: <filename>

==================================================
1. Structured Summary
==================================================

Q1. <question>
<4-5 sentence answer with specific example>

Q2. <question>
<4-5 sentence answer with specific example>

Q3. <question>
<4-5 sentence answer with specific example>

Q4. <question>
<4-5 sentence answer with specific example>

Q5. <question>
<4-5 sentence answer with specific example>

==================================================
2. Next Steps for Our Application to Adapt
==================================================

Step-by-step implications:
1. ...
2. ...
3. ...

Recommended changes:
- ...
- ...
- ...

Risks / assumptions:
- ...
- ...
```

## Decision Tree

### Case 1: Clean Text Extraction
	•	Use extracted text directly.
	•	Summarize normally.

### Case 2: Poor Extraction Quality
	•	Retry extraction.
	•	Inspect whether encoding is broken.
	•	Flag quality concerns if needed.

### Case 3: Scanned PDF
	•	Use OCR only if necessary.
	•	Validate OCR readability first.

### Case 4: Very Short PDF
	•	Still produce 5 questions.
	•	Keep answers concise.

### Case 5: Long PDF
	•	Cover full document.
	•	Preserve key arguments, evidence, conclusions.

## How to Use It

### Step 1: Find PDFs

Scan:

<project_working_directory>/case-study/

Process .pdf files only.

### Step 2: Create Output Folder

Ensure:

<project_working_directory>/case_study_summary/

exists.

### Step 3: Extract Text

Preferred order:
    1.	Direct extraction
    2.	Render / inspect if poor quality
    3.	OCR if image-based

If scripts exist, run with --help first.

### Step 4: Generate Summary

For each file:
    •	Produce 5 essential questions.
    •	Answer each in 4–5 sentences.
    •	Include a specific example.

### Step 5: Generate Adaptation Steps

Think step by step:
    •	What user pain does this reveal?
    •	What workflow should our app support?
    •	What validations are needed?
    •	What UX changes are needed?
    •	What automation opportunities exist?
    •	What risks remain?

### Step 6: Write Output

Write one .txt per PDF.

Do not merge multiple PDFs unless explicitly requested.


## Quality Checklist

Before finishing, verify:
1.	One .txt per PDF.
2.	Exactly 5 questions.
3.	Each answer is 4–5 sentences.
4.	Each answer has a specific example.
5.	Recommendations are concrete.
6.	File names are correct.
7.	Extraction issues are flagged honestly.
8.	No invented facts.

## Completion Report

When done, report:
•	Number of PDFs found
•	Number of summaries created
•	Output folder path
•	Files needing OCR
•	Files with extraction issues

## Guardrails
•	Do not summarize from filenames alone.
•	Do not invent PDF contents.
•	Do not ignore extraction quality issues.
•	Do not give generic recommendations disconnected from the case study.
•	Do not combine multiple PDFs unless explicitly asked.
