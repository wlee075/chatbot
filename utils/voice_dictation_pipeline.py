"""
voice_dictation_pipeline.py
────────────────────────────────────────────────────────────────────────────
App-layer voice preprocessing pipeline.

This module is an INPUT ADAPTER ONLY.  Its sole job is to convert audio
bytes into clean, confirmed text that can enter the chatbot graph via the
same _pending_payload path as typed input.

It does NOT:
  - answer the user
  - classify intent or PRD section relevance
  - mutate PRDState in any way
  - call LangGraph nodes directly

Public API
----------
process_voice_input(
    audio_bytes : bytes | None,
    mime_type   : str,
    session_id  : str = "",
    turn_id     : str = "",
    stt_payload : dict | None = None,   # pre-transcribed client payload
) -> dict                               # always the full output contract below

Output contract (always returned, even on failure):
{
    "status"              : "transcribed" | "needs_confirmation" | "failed" | "skipped",
    "raw_transcript"      : str,
    "normalized_text"     : str,
    "handoff_text"        : str,        # set only when status == "transcribed"
    "language"            : str,
    "confidence"          : float,      # -1.0 if unknown
    "uncertain_spans"     : list[dict], # [{"text": str, "reason": str}]
    "requires_confirmation": bool,      # True when uncertainty warrants a warning
    "failure_reason"      : str,        # "" | "no_audio_found" | "transcription_failed" |
                                        #   "empty_transcript" | "low_confidence" |
                                        #   "critical_span_unclear" | "unsupported_audio_format" |
                                        #   "missing_transcription_credentials" | "zero_length_audio"
    "exception_type"      : str,        # exception class name (dev mode display)
    "exception_message"   : str,        # str(exc) (dev mode display)
    "metadata"            : {
        "input_type" : "voice",
        "session_id" : str,
        "turn_id"    : str,
        "audio_bytes_len" : int,
        "mime_type_raw"   : str,        # raw mime_type before codec-strip
        "mime_type_used"  : str,        # cleaned mime_type sent to Gemini
    },
}
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re

logger = logging.getLogger("orchestrator_metrics")

# ── Constants ─────────────────────────────────────────────────────────────────

CONFIDENCE_THRESHOLD = 0.85

FILLER_RE = re.compile(
    r"^\s*(?:um+|uh+|er+|erm+|ah+|oh+|hmm+)[,\.\s]+",
    re.IGNORECASE,
)

UNCERTAINTY_MARKER_RE = re.compile(
    r"\[(?:unclear|inaudible|UNCLEAR|INAUDIBLE)[^\]]*\]",
    re.IGNORECASE,
)

# Tokens that are "critical facts" — uncertainty in these always triggers
# a confirmation request (numbers, percentages, product-code-like tokens, etc.)
CRITICAL_TOKEN_RE = re.compile(
    r"""
    \b(?:
        \d+(?:[.,]\d+)*(?:\s*%)?   # numbers / percentages
      | [A-Z]{2,}-\w+              # product codes like SKU-X92
      | \b\d{4}-\d{2}-\d{2}\b      # dates
    )\b
    """,
    re.VERBOSE,
)

SUPPORTED_MIME_TYPES = {
    "audio/wav",
    "audio/wave",
    "audio/x-wav",
    "audio/webm",
    "audio/mp4",
    "audio/mpeg",
    "audio/mp3",
    "audio/mpga",
    "audio/ogg",
    "audio/flac",
    "audio/aac",
    "audio/opus",
    "audio/pcm",
    "audio/m4a",
}

# ── Gemini transcription prompt ───────────────────────────────────────────────

_TRANSCRIPTION_PROMPT = (
    "You are a transcription engine. Return ONLY valid JSON with no markdown fences.\n"
    "Transcribe the provided audio exactly as spoken.\n"
    "Schema: "
    '{"transcript": <str>, "confidence": <float 0.0-1.0>, '
    '"language": <str BCP-47 e.g. "en">, '
    '"uncertain_spans": [{"text": <str>, "reason": <str>}]}\n'
    "Rules:\n"
    "- Do NOT paraphrase, summarize, or infer missing content.\n"
    "- Mark unclear words as {\"text\": \"[UNCLEAR: <heard_alternatives>]\", "
    "\"reason\": \"low_confidence\"}.\n"
    "- If audio is silent or completely unintelligible respond with "
    '{"transcript": "", "confidence": 0.0, "language": "en", "uncertain_spans": []}.'
)


# ── Internal helpers ──────────────────────────────────────────────────────────

def _clean_mime_type(raw: str) -> str:
    """Strip codec parameters and normalise the MIME type string.

    e.g. "audio/webm;codecs=opus" → "audio/webm"
         "" → "audio/wav" (fallback)
    """
    if not raw:
        return "audio/wav"
    base = raw.split(";")[0].strip().lower()
    return base if base else "audio/wav"


def _build_empty_result(
    status: str,
    failure_reason: str,
    session_id: str,
    turn_id: str,
    audio_bytes_len: int = 0,
    mime_type_raw: str = "",
    mime_type_used: str = "",
    requires_confirmation: bool = False,
    exception_type: str = "",
    exception_message: str = "",
) -> dict:
    return {
        "status": status,
        "raw_transcript": "",
        "normalized_text": "",
        "handoff_text": "",
        "language": "",
        "confidence": -1.0,
        "uncertain_spans": [],
        "requires_confirmation": requires_confirmation,
        "failure_reason": failure_reason,
        "exception_type": exception_type,
        "exception_message": exception_message,
        "metadata": {
            "input_type": "voice",
            "session_id": session_id,
            "turn_id": turn_id,
            "audio_bytes_len": audio_bytes_len,
            "mime_type_raw": mime_type_raw,
            "mime_type_used": mime_type_used,
        },
    }


def _transcribe_with_gemini(audio_bytes: bytes, mime_type: str) -> dict:
    """Call Gemini with inline audio. Returns raw JSON dict from the model.

    Uses google.generativeai directly (not LangChain wrapper) because
    audio inline_data requires the native SDK Blob object.

    The SDK's inline_data.data field must be raw bytes (the SDK handles
    base64 encoding internally when constructing the protobuf message).

    Raises on any error — caller handles the exception and logs it.
    """
    import google.generativeai as genai  # type: ignore

    genai.configure(api_key=os.environ["GOOGLE_API_KEY"])
    model = genai.GenerativeModel(
        model_name=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
    )

    # Use the explicit glm.Blob to avoid relying on dict coercion in the SDK.
    try:
        import google.ai.generativelanguage as glm  # type: ignore
        audio_part = glm.Part(
            inline_data=glm.Blob(mime_type=mime_type, data=audio_bytes)
        )
    except ImportError:
        # Fallback: dict format with base64-encoded data (REST-compatible).
        audio_part = {
            "inline_data": {
                "mime_type": mime_type,
                "data": base64.b64encode(audio_bytes).decode("utf-8"),
            }
        }

    response = model.generate_content(
        [_TRANSCRIPTION_PROMPT, audio_part],
        generation_config={"temperature": 0},
    )

    raw_text = response.text.strip()
    # Strip markdown fences if Gemini added them despite instructions
    raw_text = re.sub(r"^```(?:json)?\s*", "", raw_text, flags=re.IGNORECASE)
    raw_text = re.sub(r"\s*```$", "", raw_text)

    return json.loads(raw_text)


def _normalize_transcript(raw: str) -> str:
    """Regex-only light cleanup.  Never rewrites meaning."""
    if not raw:
        return ""
    text = raw.strip()
    # Remove leading filler words only (um, uh, er, erm, ah, oh, hmm)
    text = FILLER_RE.sub("", text).strip()
    # Collapse repeated spaces
    text = re.sub(r"  +", " ", text)
    # Capitalize first letter
    if text:
        text = text[0].upper() + text[1:]
    # Add trailing period if missing terminal punctuation
    if text and text[-1] not in ".!?,;:…":
        text += "."
    return text


def _count_meaningful_words(text: str) -> int:
    """Count words that aren't pure filler/punctuation."""
    fillers = {"um", "uh", "er", "erm", "ah", "oh", "hmm"}
    return sum(
        1
        for w in re.findall(r"\b\w+\b", text.lower())
        if w not in fillers
    )


def _detect_critical_uncertainty(
    normalized: str,
    confidence: float,
    uncertain_spans: list[dict],
) -> tuple[bool, str]:
    """Return (requires_confirmation, failure_reason)."""
    # Low overall confidence
    if 0 <= confidence < CONFIDENCE_THRESHOLD:
        return True, "low_confidence"

    # Uncertainty markers in transcript
    if UNCERTAINTY_MARKER_RE.search(normalized):
        # Check if any uncertain token overlaps with a critical fact
        critical_overlap = any(
            CRITICAL_TOKEN_RE.search(span.get("text", ""))
            for span in uncertain_spans
        )
        reason = "critical_span_unclear" if critical_overlap else "low_confidence"
        return True, reason

    # Critical facts in uncertain spans
    for span in uncertain_spans:
        if CRITICAL_TOKEN_RE.search(span.get("text", "")):
            return True, "critical_span_unclear"

    # Too short to be meaningful
    if _count_meaningful_words(normalized) < 3:
        return True, "empty_transcript"

    return False, ""


def _log(event_type: str, session_id: str, turn_id: str, **kwargs) -> None:
    logger.info(
        event_type,
        extra={
            "event_type": event_type,
            "session_id": session_id,
            "turn_id": turn_id,
            **kwargs,
        },
    )


# ── Public API ────────────────────────────────────────────────────────────────

def process_voice_input(
    audio_bytes: bytes | None,
    mime_type: str = "audio/wav",
    session_id: str = "",
    turn_id: str = "",
    stt_payload: dict | None = None,
) -> dict:
    """
    Full voice-input processing pipeline.

    Parameters
    ----------
    audio_bytes  : Raw audio bytes from st.audio_input (or None).
                   Extract with audio_obj.getvalue() or audio_obj.read().
    mime_type    : MIME type of the audio (e.g. "audio/wav").
                   Codec parameters (e.g. ";codecs=opus") are stripped.
    session_id   : Session identifier for logging.
    turn_id      : Turn identifier for logging.
    stt_payload  : Optional pre-transcribed payload from the client
                   {"raw_transcript": str, "confidence": float, "language": str}.
                   When provided, Gemini transcription is bypassed.

    Returns
    -------
    Full output contract dict (see module docstring).
    """
    mime_type_raw = mime_type or ""
    mime_type_used = _clean_mime_type(mime_type_raw)
    audio_bytes_len = len(audio_bytes) if audio_bytes else 0

    # ── Step 1: Detect input ─────────────────────────────────────────────────
    has_audio = bool(audio_bytes) and audio_bytes_len > 0
    has_stt = bool(stt_payload and stt_payload.get("raw_transcript"))

    if not has_audio and not has_stt:
        _log("voice_input_skipped", session_id, turn_id,
             has_audio=False, has_stt=False)
        return _build_empty_result(
            "skipped", "", session_id, turn_id,
            audio_bytes_len=audio_bytes_len,
            mime_type_raw=mime_type_raw,
            mime_type_used=mime_type_used,
        )

    _log("voice_input_detected", session_id, turn_id,
         has_audio=has_audio, has_stt=has_stt,
         audio_bytes_len=audio_bytes_len,
         mime_type_raw=mime_type_raw,
         mime_type_used=mime_type_used)

    # ── Step 2: Validate MIME type ───────────────────────────────────────────
    if has_audio and mime_type_used not in SUPPORTED_MIME_TYPES:
        _log("voice_transcription_failed", session_id, turn_id,
             failure_reason="unsupported_audio_format",
             mime_type_used=mime_type_used)
        return _build_empty_result(
            "failed", "unsupported_audio_format", session_id, turn_id,
            audio_bytes_len=audio_bytes_len,
            mime_type_raw=mime_type_raw,
            mime_type_used=mime_type_used,
            requires_confirmation=True,
        )

    # ── Step 3: Zero-byte guard ──────────────────────────────────────────────
    if has_audio and audio_bytes_len == 0:
        _log("voice_input_skipped", session_id, turn_id,
             failure_reason="zero_length_audio")
        return _build_empty_result(
            "skipped", "zero_length_audio", session_id, turn_id,
            audio_bytes_len=0,
            mime_type_raw=mime_type_raw,
            mime_type_used=mime_type_used,
        )

    # ── Step 4: Credential + SDK pre-check ──────────────────────────────────
    if has_audio and not stt_payload:
        # Guard: SDK availability (google-generativeai may not be installed)
        try:
            import google.generativeai  # noqa: F401  # type: ignore
        except ModuleNotFoundError:
            _log("voice_transcription_failed", session_id, turn_id,
                 failure_reason="missing_transcription_credentials",
                 exception_type="ModuleNotFoundError",
                 exception_message="google-generativeai package is not installed. "
                                   "Run: pip install google-generativeai")
            return _build_empty_result(
                "failed", "missing_transcription_credentials", session_id, turn_id,
                audio_bytes_len=audio_bytes_len,
                mime_type_raw=mime_type_raw,
                mime_type_used=mime_type_used,
                requires_confirmation=True,
                exception_type="ModuleNotFoundError",
                exception_message="google-generativeai package is not installed. "
                                  "Run: pip install google-generativeai",
            )

        api_key = os.environ.get("GOOGLE_API_KEY", "")
        if not api_key:
            _log("voice_transcription_failed", session_id, turn_id,
                 failure_reason="missing_transcription_credentials")
            return _build_empty_result(
                "failed", "missing_transcription_credentials", session_id, turn_id,
                audio_bytes_len=audio_bytes_len,
                mime_type_raw=mime_type_raw,
                mime_type_used=mime_type_used,
                requires_confirmation=True,
                exception_type="EnvironmentError",
                exception_message="GOOGLE_API_KEY is not set",
            )


    # ── Step 5: Transcribe or use supplied payload ───────────────────────────
    if has_stt:
        # Fast path: use client-supplied transcript
        raw_transcript = stt_payload.get("raw_transcript", "")          # type: ignore[union-attr]
        confidence = float(stt_payload.get("confidence", -1.0))         # type: ignore[union-attr]
        language = stt_payload.get("language", "en")                    # type: ignore[union-attr]
        uncertain_spans: list[dict] = stt_payload.get("uncertain_spans", [])  # type: ignore[union-attr]
    else:
        _log("voice_transcription_started", session_id, turn_id,
             audio_bytes_len=audio_bytes_len, mime_type_used=mime_type_used)
        try:
            result = _transcribe_with_gemini(audio_bytes, mime_type_used)    # type: ignore[arg-type]
        except Exception as exc:
            exc_type = type(exc).__name__
            exc_msg = str(exc)
            _log("voice_transcription_failed", session_id, turn_id,
                 failure_reason="transcription_failed",
                 exception_type=exc_type,
                 exception_message=exc_msg)
            return _build_empty_result(
                "failed", "transcription_failed", session_id, turn_id,
                audio_bytes_len=audio_bytes_len,
                mime_type_raw=mime_type_raw,
                mime_type_used=mime_type_used,
                requires_confirmation=True,
                exception_type=exc_type,
                exception_message=exc_msg,
            )

        raw_transcript = result.get("transcript", "")
        confidence = float(result.get("confidence", -1.0))
        language = result.get("language", "en")
        uncertain_spans = result.get("uncertain_spans", [])

        _log("voice_transcription_completed", session_id, turn_id,
             confidence=confidence, language=language,
             uncertain_span_count=len(uncertain_spans))

    # ── Step 6: Empty transcript ─────────────────────────────────────────────
    if not raw_transcript or not raw_transcript.strip():
        _log("voice_transcript_needs_confirmation", session_id, turn_id,
             failure_reason="empty_transcript", confidence=confidence)
        return {
            "status": "needs_confirmation",
            "raw_transcript": raw_transcript,
            "normalized_text": "",
            "handoff_text": "",
            "language": language,
            "confidence": confidence,
            "uncertain_spans": uncertain_spans,
            "requires_confirmation": True,
            "failure_reason": "empty_transcript",
            "exception_type": "",
            "exception_message": "",
            "metadata": {
                "input_type": "voice",
                "session_id": session_id,
                "turn_id": turn_id,
                "audio_bytes_len": audio_bytes_len,
                "mime_type_raw": mime_type_raw,
                "mime_type_used": mime_type_used,
            },
        }

    # ── Step 7: Normalize ────────────────────────────────────────────────────
    normalized = _normalize_transcript(raw_transcript)
    _log("voice_transcript_normalized", session_id, turn_id,
         confidence=confidence, uncertain_span_count=len(uncertain_spans))

    # ── Step 8: Detect critical uncertainty ──────────────────────────────────
    requires_confirmation, failure_reason = _detect_critical_uncertainty(
        normalized, confidence, uncertain_spans
    )

    if requires_confirmation:
        _log("voice_transcript_needs_confirmation", session_id, turn_id,
             failure_reason=failure_reason, confidence=confidence,
             uncertain_span_count=len(uncertain_spans))
        return {
            "status": "needs_confirmation",
            "raw_transcript": raw_transcript,
            "normalized_text": normalized,
            "handoff_text": "",          # no handoff until user confirms
            "language": language,
            "confidence": confidence,
            "uncertain_spans": uncertain_spans,
            "requires_confirmation": True,
            "failure_reason": failure_reason,
            "exception_type": "",
            "exception_message": "",
            "metadata": {
                "input_type": "voice",
                "session_id": session_id,
                "turn_id": turn_id,
                "audio_bytes_len": audio_bytes_len,
                "mime_type_raw": mime_type_raw,
                "mime_type_used": mime_type_used,
            },
        }

    # ── Step 9: Handoff ──────────────────────────────────────────────────────
    _log("voice_transcript_handoff_ready", session_id, turn_id,
         confidence=confidence, uncertain_span_count=0, failure_reason="",
         status="transcribed")
    return {
        "status": "transcribed",
        "raw_transcript": raw_transcript,
        "normalized_text": normalized,
        "handoff_text": normalized,      # clean text for downstream
        "language": language,
        "confidence": confidence,
        "uncertain_spans": uncertain_spans,
        "requires_confirmation": False,
        "failure_reason": "",
        "exception_type": "",
        "exception_message": "",
        "metadata": {
            "input_type": "voice",
            "session_id": session_id,
            "turn_id": turn_id,
            "audio_bytes_len": audio_bytes_len,
            "mime_type_raw": mime_type_raw,
            "mime_type_used": mime_type_used,
        },
    }
