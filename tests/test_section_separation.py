"""tests/test_section_separation.py

Regression tests for Background / Problem Statement section separation.

Design rule enforced here:
  Background   → describes the CURRENT-STATE WORKFLOW (confirm/correct)
  Problem Stmt → names the UNDERLYING FAILURE in that workflow (escalate)

They must not share the same evidence basis or produce identical prompt anchors.

Tests (5):
  T1 – test_problem_statement_prefers_latest_background_reframe
  T2 – test_background_and_problem_statement_do_not_use_identical_prompt_basis
  T3 – test_corrected_background_answer_shadows_older_operator_pain_snippets
  T4 – test_problem_statement_escalates_from_workflow_to_underlying_failure
  T5 – test_no_verbatim_repeat_between_background_and_problem_statement
"""
from __future__ import annotations
import pytest
from config.sections import PRD_SECTIONS
from utils.section_inference import infer_section_candidates


# ── Helpers ───────────────────────────────────────────────────────────────────

def _section_obj(section_id: str):
    for s in PRD_SECTIONS:
        if s.id == section_id:
            return s
    raise ValueError(f"Unknown section_id: {section_id!r}")


def _qa_entry(
    section_id: str,
    answer: str,
    version: int = 1,
    question: str = "",
    contradicted: bool = False,
) -> dict:
    return {
        "section_id": section_id,
        "answer": answer,
        "question": question,
        "version": version,
        "contradiction_flagged": contradicted,
    }


def _state(qa_store: dict | None = None, messages: list | None = None) -> dict:
    return {
        "confirmed_qa_store": qa_store or {},
        "messages": messages or [],
        "prd_sections": {},
        "section_index": 0,
        "iteration": 0,
    }


# ── T1: Problem Statement prefers latest Background reframe ───────────────────

def test_problem_statement_prefers_latest_background_reframe():
    """When the user corrects Background (version 2 shadows version 1),
    Problem Statement inference must use the reframed answer, not the
    original stale operator-pain framing.

    Scenario:
      v1: 'manual CSV cleanup takes 45 minutes per batch'
      v2: 'systemic intelligence gap — pipeline forgets corrections session to session'
    """
    qa_store = {
        "bg_v1": _qa_entry(
            "background",
            "We do manual CSV cleanup that takes 45 minutes per batch run.",
            version=1,
        ),
        "bg_v2": _qa_entry(
            "background",
            "The real issue is a systemic intelligence gap — "
            "the pipeline forgets all corrections between sessions so the same mismatches recur.",
            version=2,
        ),
    }
    state = _state(qa_store=qa_store)
    result = infer_section_candidates("problem_statement", state)

    # Escalation path must fire (background is answered)
    assert result["inference_available"], "Inference must be available when background is answered"

    # The escalation candidate must reference the v2 (corrected) framing
    candidates = result.get("candidate_items", [])
    assert len(candidates) >= 1, "Must yield at least one escalation candidate"

    combined = " ".join(str(c.get("text", "")) for c in candidates).lower()
    # v2 reframe keywords must appear; v1 stale framing must not dominate
    assert any(
        word in combined
        for word in ("intelligence gap", "forgets", "recur", "corrections", "session")
    ), (
        f"Escalation candidate must reference corrected Background framing (v2).\n"
        f"Got: {combined[:300]}"
    )


# ── T2: Background and Problem Statement must not share identical prompt basis ──

def test_background_and_problem_statement_do_not_use_identical_prompt_basis():
    """When background has been answered, the seed_context_hint for
    Problem Statement must be semantically distinct from any Background candidate.

    Background hints: 'current workflow / current state'
    Problem Statement hints: 'why it fails / underlying failure / deeper problem'
    """
    qa_store = {
        "bg1": _qa_entry(
            "background",
            "Current workflow: operators manually align supplier CSVs, trigger sync after sign-off.",
            version=1,
        ),
    }
    state = _state(qa_store=qa_store)

    bg_result = infer_section_candidates("background", state)
    ps_result = infer_section_candidates("problem_statement", state)

    bg_hint = (bg_result.get("seed_context_hint") or "").lower()
    ps_hint = (ps_result.get("seed_context_hint") or "").lower()

    # Background hint should be about current state / workflow
    # Problem Statement hint must NOT simply repeat the Background hint verbatim
    if bg_hint and ps_hint:
        overlap_ratio = sum(1 for w in ps_hint.split() if w in bg_hint.split()) / max(len(ps_hint.split()), 1)
        assert overlap_ratio < 0.8, (
            f"Problem Statement seed hint is >80% verbatim match to Background hint.\n"
            f"bg_hint={bg_hint!r}\n"
            f"ps_hint={ps_hint!r}\n"
            f"overlap_ratio={overlap_ratio:.2f}"
        )

    # Problem Statement hint must be escalation-oriented
    escalation_words = ["fail", "why", "deep", "underlying", "core", "reason", "falls short"]
    assert any(w in ps_hint for w in escalation_words), (
        f"Problem Statement seed hint must be escalation-oriented.\n"
        f"Got: {ps_hint!r}"
    )


# ── T3: Corrected Background answer shadows older operator-pain snippets ───────

def test_corrected_background_answer_shadows_older_operator_pain_snippets():
    """Correction-precedence in _qa_texts_for_sections must ensure that when
    a user corrects their Background answer (higher version), the OLD entry
    is NOT included alongside the new one in signal extraction.

    Old framing (v1): 'manual grind, operator pain'
    New framing (v2): 'systemic intelligence gap, pipeline amnesia'

    The Problem Statement escalation candidate must reference ONLY v2.
    """
    qa_store = {
        "bg_stale": _qa_entry(
            "background",
            "Operators manually grind through supplier data every single day.",
            version=1,
        ),
        "bg_corrected": _qa_entry(
            "background",
            "The systemic issue is pipeline amnesia — it never retains cross-session corrections.",
            version=2,
        ),
    }
    state = _state(qa_store=qa_store)
    result = infer_section_candidates("problem_statement", state)

    candidates = result.get("candidate_items", [])
    combined = " ".join(str(c.get("text", "")) for c in candidates).lower()

    # v2 terms must appear
    assert any(w in combined for w in ("amnesia", "retains", "corrections", "systemic")), (
        f"Corrected background framing (v2) must appear in escalation candidate.\n"
        f"Got: {combined[:300]}"
    )


# ── T4: Problem Statement escalates from workflow to underlying failure ─────────

def test_problem_statement_escalates_from_workflow_to_underlying_failure():
    """The escalation candidate category must be 'escalation_from_background'
    and its text must frame the question as 'why does the workflow fail'
    rather than simply re-describing the workflow.
    """
    qa_store = {
        "bg": _qa_entry(
            "background",
            "Current state: manual supplier data alignment, triggered by scheduler, "
            "human sign-off required below 92% confidence.",
            version=1,
        ),
    }
    state = _state(qa_store=qa_store)
    result = infer_section_candidates("problem_statement", state)

    candidates = result.get("candidate_items", [])
    assert len(candidates) >= 1

    first = candidates[0]
    assert first.get("category") == "escalation_from_background", (
        f"Candidate category must be 'escalation_from_background' when background is answered.\n"
        f"Got: {first.get('category')!r}"
    )

    text = first.get("text", "").lower()
    escalation_signals = [
        "fail", "why", "core reason", "deeper", "underlying", "recur",
        "never learn", "keeps failing", "falls short",
    ]
    assert any(s in text for s in escalation_signals), (
        f"Escalation candidate must frame the question around underlying failure.\n"
        f"Got: {text[:300]}"
    )


# ── T5: No verbatim repeat between Background and Problem Statement ────────────

def test_no_verbatim_repeat_between_background_and_problem_statement():
    """The 'source_sections' on Background candidates must NOT include
    'problem_statement', and vice versa.

    More importantly, when Background is answered, the Problem Statement
    candidates must not share the exact same text as any Background candidate.
    """
    qa_store = {
        "bg": _qa_entry(
            "background",
            "Workflow: raw CSV → header reconciliation → sync trigger → human review queue.",
            version=1,
        ),
    }
    state = _state(qa_store=qa_store)

    bg_result  = infer_section_candidates("background", state)
    ps_result  = infer_section_candidates("problem_statement", state)

    bg_texts = {c.get("text", "")[:80].lower() for c in bg_result.get("candidate_items", [])}
    ps_texts = {c.get("text", "")[:80].lower() for c in ps_result.get("candidate_items", [])}

    verbatim_overlap = bg_texts & ps_texts
    assert not verbatim_overlap, (
        f"Background and Problem Statement must not share verbatim candidate text.\n"
        f"Shared (first 80 chars): {verbatim_overlap}"
    )
