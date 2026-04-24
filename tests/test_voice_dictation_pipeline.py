"""
tests/test_voice_dictation_pipeline.py
────────────────────────────────────────────────────────────────────────────
Unit tests for utils.voice_dictation_pipeline.

All Gemini calls are mocked — no network required.
"""

import os
import pytest
from unittest.mock import patch, MagicMock


# ── Helpers ───────────────────────────────────────────────────────────────────

FAKE_AUDIO = b"RIFF\x00\x00\x00\x00WAVEfmt "  # minimal valid-ish WAV header


def _mock_gemini_return(transcript: str, confidence: float, uncertain_spans: list[dict] | None = None):
    return {
        "transcript": transcript,
        "confidence": confidence,
        "language": "en",
        "uncertain_spans": uncertain_spans or [],
    }


def _run(
    audio_bytes,
    mime_type="audio/wav",
    stt_payload=None,
    gemini_return=None,
    gemini_raises=None,
    env_override: dict | None = None,
):
    from utils.voice_dictation_pipeline import process_voice_input

    target = "utils.voice_dictation_pipeline._transcribe_with_gemini"
    base_env = {"GOOGLE_API_KEY": "test-key", **(env_override or {})}

    with patch.dict(os.environ, base_env, clear=False):
        if gemini_raises:
            with patch(target, side_effect=gemini_raises):
                return process_voice_input(audio_bytes, mime_type, "sess1", "turn1", stt_payload)
        else:
            mock_val = gemini_return or _mock_gemini_return("hello world this is a test", 0.95)
            with patch(target, return_value=mock_val):
                return process_voice_input(audio_bytes, mime_type, "sess1", "turn1", stt_payload)


# ── Test 1: No audio → skipped ────────────────────────────────────────────────

def test_no_audio_returns_skipped():
    result = _run(None)
    assert result["status"] == "skipped"
    assert result["handoff_text"] == ""
    assert result["exception_type"] == ""


# ── Test 2: Clear audio → transcribed ────────────────────────────────────────

def test_clear_audio_returns_transcribed():
    result = _run(
        FAKE_AUDIO,
        gemini_return=_mock_gemini_return("The system needs 95 percent uptime.", 0.97),
    )
    assert result["status"] == "transcribed"
    assert result["requires_confirmation"] is False
    assert result["handoff_text"] != ""
    assert result["failure_reason"] == ""
    assert result["exception_type"] == ""


# ── Test 3: Pre-existing STT payload bypasses Gemini ─────────────────────────

def test_existing_stt_payload_bypasses_gemini():
    stt = {
        "raw_transcript": "The real issue is edge case complexity.",
        "confidence": 0.91,
        "language": "en",
        "uncertain_spans": [],
    }
    with patch("utils.voice_dictation_pipeline._transcribe_with_gemini") as mock_g:
        from utils.voice_dictation_pipeline import process_voice_input
        result = process_voice_input(None, "audio/wav", "sess1", "turn1", stt_payload=stt)
        mock_g.assert_not_called()
    assert result["status"] == "transcribed"
    assert "edge case complexity" in result["handoff_text"]


# ── Test 4: Empty transcript → needs_confirmation ─────────────────────────────

def test_empty_transcript_returns_needs_confirmation():
    result = _run(FAKE_AUDIO, gemini_return=_mock_gemini_return("", 0.0))
    assert result["status"] == "needs_confirmation"
    assert result["failure_reason"] == "empty_transcript"
    assert result["handoff_text"] == ""


# ── Test 5: Low confidence → needs_confirmation ───────────────────────────────

def test_low_confidence_returns_needs_confirmation():
    result = _run(
        FAKE_AUDIO,
        gemini_return=_mock_gemini_return("something something unclear words here and more", 0.72),
    )
    assert result["status"] == "needs_confirmation"
    assert result["failure_reason"] == "low_confidence"
    assert result["handoff_text"] == ""


# ── Test 6: Unclear number span → needs_confirmation ─────────────────────────

def test_unclear_number_span_returns_needs_confirmation():
    result = _run(
        FAKE_AUDIO,
        gemini_return=_mock_gemini_return(
            "The target is [UNCLEAR: fifteen or fifty] seconds.",
            0.91,
            uncertain_spans=[{"text": "[UNCLEAR: fifteen or fifty]", "reason": "low_confidence"}],
        ),
    )
    assert result["status"] == "needs_confirmation"
    assert result["failure_reason"] in ("low_confidence", "critical_span_unclear")
    assert result["handoff_text"] == ""


# ── Test 7: Product code preserved exactly ────────────────────────────────────

def test_product_code_preserved_in_handoff():
    result = _run(
        FAKE_AUDIO,
        gemini_return=_mock_gemini_return("Please look up SKU-X92 in the catalog.", 0.96),
    )
    assert result["status"] == "transcribed"
    assert "SKU-X92" in result["handoff_text"]


# ── Test 8: Leading filler words stripped ────────────────────────────────────

def test_filler_words_stripped_from_normalized():
    result = _run(
        FAKE_AUDIO,
        gemini_return=_mock_gemini_return(
            "um, not just sku matching the real issue is edge case complexity",
            0.95,
        ),
    )
    assert result["status"] == "transcribed"
    norm = result["normalized_text"]
    assert not norm.lower().startswith("um")


# ── Test 9: Gemini exception → failed with exception metadata ─────────────────

def test_gemini_exception_returns_failed_with_metadata():
    result = _run(FAKE_AUDIO, gemini_raises=RuntimeError("network error"))
    assert result["status"] == "failed"
    assert result["failure_reason"] == "transcription_failed"
    assert result["handoff_text"] == ""
    assert result["exception_type"] == "RuntimeError"
    assert "network error" in result["exception_message"]


# ── Test 10: transcribed → handoff_text is set, not empty ────────────────────

def test_transcribed_handoff_text_is_nonempty():
    result = _run(
        FAKE_AUDIO,
        gemini_return=_mock_gemini_return(
            "The system should handle ten thousand concurrent users.", 0.97,
        ),
    )
    assert result["status"] == "transcribed"
    assert result["handoff_text"].strip() != ""
    assert result["requires_confirmation"] is False


# ── Test 11: Unsupported MIME type → failed ───────────────────────────────────

def test_unsupported_mime_type_returns_failed():
    result = _run(FAKE_AUDIO, mime_type="video/mp4")
    assert result["status"] == "failed"
    assert result["failure_reason"] == "unsupported_audio_format"


# ── Test 12: Metadata always populated ───────────────────────────────────────

def test_metadata_always_present():
    result = _run(None)  # skipped path
    assert result["metadata"]["input_type"] == "voice"
    assert result["metadata"]["session_id"] == "sess1"
    assert result["metadata"]["turn_id"] == "turn1"


# ── Test 13: Zero-length bytes → skipped (not failed) ─────────────────────────

def test_zero_length_audio_returns_skipped():
    result = _run(b"")
    assert result["status"] == "skipped"
    assert result["metadata"]["audio_bytes_len"] == 0


# ── Test 14: Missing GOOGLE_API_KEY → specific failure reason ─────────────────

def test_missing_credentials_returns_specific_failure():
    from utils.voice_dictation_pipeline import process_voice_input
    # Temporarily remove the key
    env = {k: v for k, v in os.environ.items() if k != "GOOGLE_API_KEY"}
    with patch.dict(os.environ, env, clear=True):
        with patch("utils.voice_dictation_pipeline._transcribe_with_gemini") as mock_g:
            result = process_voice_input(FAKE_AUDIO, "audio/wav", "sess1", "turn1")
    mock_g.assert_not_called()
    assert result["status"] == "failed"
    assert result["failure_reason"] == "missing_transcription_credentials"
    assert result["exception_type"] == "EnvironmentError"


# ── Test 15: Codec-param MIME stripped correctly ──────────────────────────────

def test_codec_param_mime_is_stripped():
    result = _run(
        FAKE_AUDIO,
        mime_type="audio/webm;codecs=opus",
        gemini_return=_mock_gemini_return("The answer is forty two words long enough.", 0.96),
    )
    # Should pass MIME validation (audio/webm is supported after stripping)
    assert result["status"] == "transcribed"
    assert result["metadata"]["mime_type_raw"] == "audio/webm;codecs=opus"
    assert result["metadata"]["mime_type_used"] == "audio/webm"


# ── Test 16: Empty MIME falls back to audio/wav ───────────────────────────────

def test_empty_mime_falls_back_to_wav():
    result = _run(
        FAKE_AUDIO,
        mime_type="",
        gemini_return=_mock_gemini_return("Fallback mime should work fine here.", 0.96),
    )
    assert result["metadata"]["mime_type_used"] == "audio/wav"
    assert result["status"] == "transcribed"
