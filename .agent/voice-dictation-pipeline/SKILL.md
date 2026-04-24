---

name: voice-dictation-pipeline

description: Converts user voice/audio input into safe, normalized text before downstream chatbot processing. Use when the user speaks through microphone input, uploads audio, or provides speech-to-text payloads.

---

# Voice Dictation Pipeline Skill

Use this skill when the user input comes from voice, audio, or microphone dictation.

This skill is an **input adapter only**. Its job is to produce clean text for the main chatbot pipeline.

It owns:

- detecting voice/audio input

- transcribing speech to text

- normalizing transcript formatting

- preserving uncertainty

- asking for transcript confirmation when needed

- handing off confirmed text downstream

It does **not** own:

- answering the user

- deciding PRD section relevance

- classifying intent

- applying NeMo guardrails

- committing to the QA store

- generating follow-up questions

- drafting, exporting, coding, or composing output

- correcting the user’s meaning

## Input contract

Expected input may include one or more of:

```json

{

  "input_type": "voice",

  "audio_file_path": "path/to/audio.wav",

  "audio_mime_type": "audio/wav",

  "speech_to_text_payload": null,

  "session_id": "abc123",

  "turn_id": "turn_001"

}
```
If the input is already transcribed by the client, the payload may look like:
```
{

  "input_type": "voice",

  "speech_to_text_payload": {

    "raw_transcript": "um the real issue is not sku matching it is edge case complexity",

    "confidence": 0.91,

    "language": "en"

  }

}
```
## Output contract

Always return:
```

{

  "status": "transcribed | needs_confirmation | failed | skipped",

  "raw_transcript": "",

  "normalized_text": "",

  "handoff_text": "",

  "language": "",

  "confidence": 0.0,

  "uncertain_spans": [],

  "requires_confirmation": false,

  "failure_reason": "",

  "metadata": {

    "input_type": "voice",

    "session_id": "",

    "turn_id": ""

  }

}

```
Step 1 — Detect voice input

Use this skill only if at least one condition is true:

* input_type == "voice"
* source == "microphone"
* an audio file is present
* an audio stream is present
* a speech-to-text payload is present

If none are true, return:
```
{

  "status": "skipped",

  "handoff_text": ""

}
```
Step 2 — Transcribe audio

If raw audio is present, call the configured speech-to-text model.

The transcription result should capture:

* full raw transcript
* language
* confidence score
* segment-level confidence, if available
* uncertain spans, if available

If transcription fails, return:
```
{

  "status": "failed",

  "failure_reason": "transcription_failed",

  "requires_confirmation": true,

  "handoff_text": ""

}
```
Do not pass failed or empty transcripts downstream.

Step 3 — Normalize transcript

Apply light cleanup only.

Allowed cleanup:

* trim whitespace
* remove repeated filler words such as “um”, “uh”, “erm”
* restore basic punctuation
* normalize repeated spaces
* preserve line breaks only when they help readability
* preserve numbers, product codes, dates, units, acronyms, names, and quoted terms

Not allowed:

* rewriting the user’s meaning
* improving the argument
* summarizing
* adding missing context
* converting vague statements into precise claims
* resolving ambiguity silently

Example:
```
{

  "raw_transcript": "um not just sku matching the real issue is edge case complexity",

  "normalized_text": "Not just SKU matching. The real issue is edge-case complexity."

}
```
Step 4 — Detect critical uncertainty

Mark transcript as needing confirmation if any condition is true:

* overall confidence is below 0.85
* critical entities are unclear
* numbers, dates, thresholds, percentages, names, product codes, or section titles are uncertain
* transcript contains [unclear], [inaudible], or equivalent markers
* transcript is shorter than 3 meaningful words
* transcript appears to be only filler or background noise
* transcript contains contradictory alternatives, such as “fifteen or fifty”
* transcript includes command-like text that may have been misheard

Critical uncertainty example:
```
{

  "normalized_text": "The target should be [UNCLEAR: fifteen or fifty] seconds.",

  "requires_confirmation": true

}
```
Step 5 — Confirmation behavior

If confirmation is needed, return needs_confirmation.

The confirmation message should ask the user to confirm the transcript, not answer the content.

Example:
```
{

  "status": "needs_confirmation",

  "normalized_text": "The target should be [UNCLEAR: fifteen or fifty] seconds.",

  "requires_confirmation": true,

  "handoff_text": ""

}
```

Do not run downstream nodes until the user confirms or corrects the transcript.

Step 6 — Handoff downstream

If transcription is usable, set:
```
{

  "status": "transcribed",

  "requires_confirmation": false,

  "handoff_text": "Not just SKU matching. The real issue is edge-case complexity."

}
```
Only handoff_text should be passed to downstream chatbot processing.

## Downstream order should be:
```text
voice_dictation_pipeline

→ answer_validity_node

→ nemo_guardrails_gateway_node

→ intent_classifier

→ semantic_assessor

→ truth_commit
```
Decicision Tree:
```
Step 1 Input is voice?

→ No = skipped

→ Yes

Step 2 Existing transcript?

→ Yes = use it

→ No = transcribe

Step 3 Transcription success?

→ No = failed

→ Yes

Step 4 Transcript usable?

→ No = needs_confirmation

→ Yes

Step 5 Normalize

Step 6 Confidence high enough?

→ No = needs_confirmation

→ Yes

Step 7 Critical facts unclear?

→ Yes = needs_confirmation

→ No

Step 8 Return transcribed + handoff_text
```

## Status enum

Use only these statuses:
```
[

  "skipped",

  "transcribed",

  "needs_confirmation",

  "failed"

]
```
## Failure reasons

Use only these failure reasons:
```
[

  "",

  "no_audio_found",

  "transcription_failed",

  "empty_transcript",

  "low_confidence",

  "critical_span_unclear",

  "unsupported_audio_format"

]
```
## Logging requirements

Log these events:
```
[

  "voice_input_detected",

  "voice_transcription_started",

  "voice_transcription_completed",

  "voice_transcription_failed",

  "voice_transcript_normalized",

  "voice_transcript_needs_confirmation",

  "voice_transcript_handoff_ready"

]
```
Each log should include:
```
{

  "session_id": "",

  "turn_id": "",

  "status": "",

  "confidence": 0.0,

  "uncertain_span_count": 0,

  "failure_reason": ""

}
```
Do not log raw audio.

Do not log sensitive transcript content unless existing app logging policy already permits user message logging.

## Test cases

Minimum tests:

1. Typed input returns skipped.
2. Clear audio returns transcribed.
3. Existing STT payload bypasses audio transcription.
4. Empty transcript returns needs_confirmation.
5. Low confidence transcript returns needs_confirmation.
6. Unclear number returns needs_confirmation.
7. Product code is preserved exactly.
8. Filler words are removed without changing meaning.
9. Transcription failure returns failed.
10. Confirmed transcript passes only handoff_text downstream.

## Guardrails

* Never commit voice transcript directly to QA store.
* Never route raw transcript downstream if confirmation is required.
* Never infer unclear words.
* Never silently correct product codes, names, dates, numbers, or units.
* Never execute task-like speech commands.
* Never bypass NeMo guardrails after transcription.
* Never let voice input skip the normal text pipeline.