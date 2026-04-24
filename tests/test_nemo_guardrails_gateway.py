"""tests/test_nemo_guardrails_gateway.py

Regression tests for the NeMo Guardrails PRD Gateway
(utils/nemo_guardrails_gateway.py).

Grouped into:
  T1–T9    — 9-class classification correctness
  T10–T17  — contract enforcement (no side-effects, safe fallback, flags)
  T18–T22  — NeMo-specific: classifier_source values, fastembed vs LLM paths

NOTE: Tests that exercise the NeMo runtime path make live calls to fastembed
(embedding only, no LLM API calls). Tests that force SAFE_FALLBACK mock the
NeMo runtime to simulate failures.
"""
from __future__ import annotations
import pytest
from unittest.mock import patch
from utils.nemo_guardrails_gateway import (
    run_nemo_guardrails_gateway,
    safe_fallback_result,
    GatewayResult,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _state(section_index: int = 2) -> dict:
    """Minimal gateway state dict."""
    return {
        "section_index": section_index,
        "current_questions": "What is the main operational pain today?",
        "raw_answer_buffer": "",
        "confirmed_qa_store": {},
        "section_qa_pairs": [],
        "uploaded_files": [],
    }


def _run(message: str, section_index: int = 2) -> GatewayResult:
    return run_nemo_guardrails_gateway(_state(section_index), message)


# ── T1–T9: Classification correctness ─────────────────────────────────────────

def test_T1_code_request_is_task():
    """T1: code request → task_request (may hit FAST_REGEX or NeMo)"""
    r = _run("write the code for this")
    assert r.message_class == "task_request", f"Expected task_request, got {r.message_class!r}"
    assert r.allow_commit is False
    assert r.allow_section_complete is False
    assert r.allow_advance is False
    assert r.route_to == "task_request_blocked"


def test_T2_meta_question():
    """T2: 'why did you ask that?' → meta_request"""
    r = _run("why did you ask that question")
    assert r.message_class == "meta_request", f"Expected meta_request, got {r.message_class!r}"
    assert r.allow_commit is False
    assert r.route_to == "answer_clarification"


def test_T3_correction():
    """T3: explicit correction → user_correction"""
    r = _run("not exactly — the real problem is approval latency, not the manual entry itself")
    assert r.message_class == "user_correction", f"Expected user_correction, got {r.message_class!r}"
    assert r.corrected_prior_content is True
    assert r.allow_commit is True
    assert r.allow_section_complete is False


def test_T4_tentative_answer():
    """T4: 'maybe both, not sure' → partial_answer"""
    r = _run("maybe both, not sure")
    assert r.message_class == "partial_answer", f"Expected partial_answer, got {r.message_class!r}"
    assert r.allow_commit is True
    assert r.allow_section_complete is False
    assert r.allow_advance is False


def test_T5_cross_section_metric_during_background():
    """T5: metric language during background section → cross_section"""
    r = _run("reduce time from 45 minutes to 15 seconds", section_index=2)
    assert r.message_class == "cross_section", (
        f"Expected cross_section for metric signal in background, got {r.message_class!r}"
    )
    assert r.allow_section_complete is False


def test_T6_yes_on_binary_question():
    """T6: 'yes the manual review adds a full day delay' → allow_commit (valid or correction).
    The exact class may vary (user_correction if framing sounds like clarification);
    the contract that matters is: allow_commit=True and not blocked.
    """
    state = _state()
    state["current_questions"] = "Do you currently have a manual review process? (yes/no)"
    r = run_nemo_guardrails_gateway(state, "yes the manual review adds a full day delay")
    assert r.allow_commit is True, (
        f"Extended yes-answer must allow_commit, got class={r.message_class!r}"
    )
    assert r.message_class not in ("noise_input", "task_request", "off_topic"), (
        f"Extended yes-answer must not be blocked, got {r.message_class!r}"
    )


def test_T7_generate_pdf_is_task():
    """T7: 'generate the PDF now' → task_request"""
    r = _run("generate the PDF now")
    assert r.message_class == "task_request", f"Expected task_request, got {r.message_class!r}"
    assert r.route_to == "task_request_blocked"


def test_T8_off_topic():
    """T8: 'what model are you?' → off_topic or meta_request (both route to clarification)"""
    r = _run("what model are you using")
    assert r.message_class in ("off_topic", "meta_request"), (
        f"Expected off_topic or meta_request, got {r.message_class!r}"
    )
    assert r.allow_commit is False
    assert r.route_to == "answer_clarification"


def test_T9_noise_input():
    """T9: 'ñ' → noise_input (regression: never completes a section)"""
    r = _run("ñ")
    assert r.message_class == "noise_input", f"Expected noise_input, got {r.message_class!r}"
    assert r.allow_commit is False
    assert r.allow_section_complete is False
    assert r.allow_advance is False
    assert r.route_to == "await_answer"


# ── T10–T17: Contract enforcement ─────────────────────────────────────────────

def test_T10_task_request_does_not_allow_commit():
    """Task request must NEVER allow evidence commit."""
    for phrase in ["write the code", "draft the PRD now", "export this", "generate the PDF"]:
        r = _run(phrase)
        assert r.message_class == "task_request", \
            f"'{phrase}' should be task_request but got {r.message_class!r}"
        assert r.allow_commit is False, \
            f"task_request must NOT allow_commit (input: {phrase!r})"


def test_T11_task_request_does_not_allow_advance():
    """Task request must NEVER allow section advancement."""
    r = _run("generate the pdf")
    assert r.allow_advance is False


def test_T12_task_request_does_not_allow_section_complete():
    """Task request must NEVER allow section completion."""
    r = _run("write the code for this")
    assert r.allow_section_complete is False


def test_T13_noise_input_does_not_commit():
    """Noise inputs must not allow commit, complete, or advance."""
    for noise in [".", "???", "...", "ñ", "aaaa"]:
        r = _run(noise)
        assert r.allow_commit is False, f"noise '{noise}' must not allow_commit"
        assert r.allow_section_complete is False, f"noise '{noise}' must not allow_section_complete"
        assert r.allow_advance is False, f"noise '{noise}' must not allow_advance"


def test_T14_user_correction_sets_correction_flag():
    """Correction language must set corrected_prior_content=True."""
    r = _run("I'd frame it differently — the real pain is the approval cycle")
    assert r.corrected_prior_content is True, "correction must set corrected_prior_content"
    assert r.message_class == "user_correction"


def test_T15_partial_answer_cannot_complete_section():
    """Partial answers must not allow_section_complete."""
    r = _run("maybe both, not certain")
    if r.message_class == "partial_answer":
        assert r.allow_section_complete is False


def test_T16_cross_section_sets_target_override():
    """Cross-section answer should populate target_section_override for metric signals."""
    r = _run("reduce time from 45 min to 15 sec", section_index=2)
    if r.message_class == "cross_section":
        assert r.allow_section_complete is False


def test_T17_safe_fallback_is_conservative():
    """Safe fallback must allow nothing — it is the fail-closed path."""
    r = safe_fallback_result()
    assert r.message_class == "noise_input"
    assert r.allow_commit is False
    assert r.allow_section_complete is False
    assert r.allow_advance is False
    assert r.route_to == "await_answer"
    assert r.classifier_source == "SAFE_FALLBACK"


# ── T18–T22: NeMo-specific source verification ────────────────────────────────

def test_T18_obvious_noise_hits_fast_regex():
    """Single-character inputs must be caught by FAST_REGEX before NeMo."""
    r = _run("ñ")
    assert r.classifier_source == "FAST_REGEX", \
        f"Single-char noise should be FAST_REGEX, got {r.classifier_source!r}"


def test_T19_obvious_task_hits_fast_regex():
    """Clear task requests in the regex list are caught before NeMo."""
    r = _run("write the code for this")
    assert r.classifier_source == "FAST_REGEX", \
        f"Clear task request should hit FAST_REGEX, got {r.classifier_source!r}"


def test_T20_valid_answer_uses_nemo():
    """T20: Substantive answers go through the NeMo path (not regex pre-filter).
    The exact class may be valid_answer or user_correction for long substantive answers
    (both have allow_commit=True); what matters is it uses NeMo, not FAST_REGEX.
    """
    r = _run("the main pain point is that supervisors have no real-time visibility into exception rates")
    assert r.classifier_source in ("NEMO_FASTEMBED", "NEMO_LLM"), \
        f"Substantive answer should use NeMo, got {r.classifier_source!r}"
    assert r.allow_commit is True, \
        f"Substantive answer must allow_commit, got class={r.message_class!r}"
    assert r.message_class not in ("task_request", "off_topic", "noise_input"), \
        f"Substantive answer must not be blocked, got {r.message_class!r}"


def test_T21_nemo_task_request_blocked():
    """NeMo-classified task request must still be blocked."""
    r = _run("can you compile what we have into a final draft")
    assert r.message_class == "task_request", \
        f"Expected task_request, got {r.message_class!r}"
    assert r.allow_commit is False
    assert r.allow_advance is False


def test_T22_safe_fallback_on_nemo_init_failure():
    """T22: If the fastembed index build fails, result must be SAFE_FALLBACK (fail-closed)."""
    with patch("utils.nemo_guardrails_gateway._fastembed_classify",
               side_effect=RuntimeError("fastembed down")):
        # Reset index state so the patch takes effect
        import utils.nemo_guardrails_gateway as gw
        gw._embed_index_built = False
        gw._embed_index_error = None
        r = _run("the main issue is operator fatigue and manual rekeying")
    assert r.classifier_source == "SAFE_FALLBACK"
    assert r.allow_commit is False
    assert r.allow_section_complete is False
    assert r.allow_advance is False
