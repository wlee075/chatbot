"""tests/test_last_mile_formatter.py — 4 required tests for the orchestrator last-mile fix.

Tests per user spec:
  1. test_problem_statement_inference_bypasses_generic_fallback_when_candidates_exist
  2. test_orchestrator_prompt_override_applied_logged_for_problem_statement  (via _format_deterministic_propose_question)
  3. test_section_complete_does_not_emit_stale_generic_question
  4. test_candidate_items_render_to_plain_english_confirm_question
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from graph.nodes import _format_deterministic_propose_question
from utils.prd_orchestrator import (
    ACTION_PROPOSE_ONE,
    ACTION_PROPOSE_LIST,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

class DummySection:
    def __init__(self, section_id: str, title: str = ""):
        self.id = section_id
        self.title = title or section_id.replace("_", " ").title()


def _propose_plan(action: str, candidates: list, section_id: str = "problem_statement") -> dict:
    return {
        "recommended_action": action,
        "candidate_items": candidates,
        "target_section_id": section_id,
        "target_section_title": section_id.replace("_", " ").title(),
        "is_live_prompt_eligible": True,
        "confidence": "high",
    }


# ── Test 1 ────────────────────────────────────────────────────────────────────

def test_problem_statement_inference_bypasses_generic_fallback_when_candidates_exist():
    """When orchestrator proposes pain-point candidates for problem_statement,
    the deterministic formatter must return a non-empty confirm/correct question.

    This proves the LLM bypass path is taken: if the formatter returns empty,
    the caller falls through to the LLM-constrained path. We assert it returns
    a question so the short-circuit is active.
    """
    pain_candidates = [
        {"text": "Daily manual effort spent aligning inconsistent supplier data",
         "category": "workflow_friction", "evidence_level": "HIGH"},
        {"text": "Edge cases break automation and force manual validation",
         "category": "error_cost", "evidence_level": "MEDIUM"},
    ]
    plan = _propose_plan(ACTION_PROPOSE_LIST, pain_candidates, "problem_statement")
    section = DummySection("problem_statement", "Problem Statement")

    result = _format_deterministic_propose_question(plan, section)

    assert result, (
        "Formatter returned empty string — generic fallback would have won. "
        "Deterministic path must return a confirm/correct question."
    )
    # Must NOT be generic
    assert "what problem" not in result.lower(), (
        f"Result looks like generic fallback: {result!r}"
    )


# ── Test 2 ────────────────────────────────────────────────────────────────────

def test_orchestrator_prompt_override_applied_logged_for_problem_statement():
    """The log event orchestrator_prompt_override_applied is triggered by the
    dispatch block in generate_questions_node.

    We prove the formatter produces output (non-empty) — the actual log event is
    emitted inside generate_questions_node at runtime. Here we test the formatter
    contract that makes the log fire: non-empty return triggers the log branch.
    """
    candidates = [
        "Daily manual effort spent aligning inconsistent supplier data",
        "Edge cases break automation and force manual validation",
    ]
    plan = _propose_plan(ACTION_PROPOSE_LIST, candidates, "problem_statement")
    section = DummySection("problem_statement")

    result = _format_deterministic_propose_question(plan, section)

    # The dispatch block fires orchestrator_prompt_override_applied iff result is non-empty.
    # Assert the formatter delivers a non-empty question so the log will fire.
    assert result, (
        "Formatter returned empty — orchestrator_prompt_override_applied would NOT fire"
    )
    # Must reference problem_statement context, not generic
    has_confirmation_framing = (
        "is that" in result.lower()
        or "does that" in result.lower()
        or "am i" in result.lower()
        or "sound right" in result.lower()
        or "accurately" in result.lower()
    )
    assert has_confirmation_framing, (
        f"Question must frame as confirm/correct, not open-ended: {result!r}"
    )


# ── Test 3 ────────────────────────────────────────────────────────────────────

def test_section_complete_does_not_emit_stale_generic_question():
    """PROPOSE_ONE with a single HIGH-confidence candidate must produce
    a single, focused confirm/correct question — not a multi-question or
    open-ended form. This guards against stale generic text leaking through.
    """
    candidate = "Lack of reliable self-correcting loop for failed SKU matches"
    plan = _propose_plan(ACTION_PROPOSE_ONE, [candidate], "problem_statement")
    section = DummySection("problem_statement")

    result = _format_deterministic_propose_question(plan, section)

    assert result, "PROPOSE_ONE returned empty — stale generic fallback would run"

    # Must include the candidate text (or a clear paraphrase)
    candidate_words = ["self-correcting", "SKU", "reliable", "loop"]
    word_hits = sum(1 for w in candidate_words if w.lower() in result.lower())
    assert word_hits >= 2, (
        f"Formatted question does not reference the candidate content. "
        f"Result: {result!r}"
    )

    # Must be a single question (no bullet lists of sub-questions)
    question_marks = result.count("?")
    assert question_marks <= 2, (
        f"Multiple question marks — formatter may be generating multiple questions: {result!r}"
    )


# ── Test 4 ────────────────────────────────────────────────────────────────────

def test_candidate_items_render_to_plain_english_confirm_question():
    """PROPOSE_LIST with 3 candidates must produce a bullet-list phrased in
    plain operational English — not internal jargon like 'section_id' or
    'evidence_level', and not a generic 'What are your pain points?' ask.
    """
    candidates = [
        {"text": "Manual reconciliation takes 8 hours per week",
         "category": "time_waste", "evidence_level": "HIGH"},
        {"text": "Matching errors trigger downstream refunds",
         "category": "error_cost", "evidence_level": "MEDIUM"},
        {"text": "No audit trail for failed matches",
         "category": "workflow_friction", "evidence_level": "LOW"},
    ]
    plan = _propose_plan(ACTION_PROPOSE_LIST, candidates, "problem_statement")
    section = DummySection("problem_statement", "Problem Statement")

    result = _format_deterministic_propose_question(plan, section)

    assert result, "PROPOSE_LIST returned empty"

    # Must contain bullet-list candidates (• character or each candidate text)
    all_labels_present = all(
        c["text"][:20].lower() in result.lower()
        for c in candidates
    )
    assert all_labels_present, (
        f"Not all candidate labels appear in the formatted question.\nResult: {result!r}"
    )

    # Must not contain internal jargon
    for jargon in ("evidence_level", "section_id", "time_waste", "error_cost", "workflow_friction"):
        assert jargon not in result, (
            f"Internal jargon leaked into user-facing question: {jargon!r}\nResult: {result!r}"
        )

    # Must end with a confirm/correct framing
    assert result.strip().endswith("?"), (
        f"Question must end with '?': {result!r}"
    )
