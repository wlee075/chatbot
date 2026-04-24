"""tests/test_background_synthesis.py

Tests for the Background section synthesis inferrer (Phase 1.6).

Required tests from spec:
- test_background_uses_prior_headliner_and_elevator_evidence
- test_background_question_is_summary_confirm_not_blank_prompt
- test_background_does_not_repeat_pain_point_field_label_language
- test_background_structured_success_still_produces_specific_question
- test_background_after_key_stakeholders_uses_existing_workflow_facts
"""
from __future__ import annotations
import pytest
from utils.section_inference import infer_section_candidates


# ─── Helpers ────────────────────────────────────────────────────────────────

def _qa(section_id: str, answer: str) -> dict:
    return {
        "section_id": section_id,
        "answer": answer,          # canonical field; matches confirmed_qa_store schema
        "contradiction_flagged": False,
        "resolved_subparts": [],
        "version": 1,
    }


def _make_state(qa_pairs: list[dict] | None = None) -> dict:
    store = {}
    for i, pair in enumerate(qa_pairs or []):
        store[f"q{i}"] = pair
    return {
        "confirmed_qa_store": store,
        "prd_sections": {},
        "section_index": 0,
        "conversation_history": [],
        "recent_questions": [],
        "remaining_subparts": [],
        "concept_conflicts": [],
    }


# ── T1 ────────────────────────────────────────────────────────────────────────

def test_background_uses_prior_headliner_and_elevator_evidence():
    """When headliner and elevator_pitch are answered, Background inferrer
    should pick up both as evidence and return inference_available=True."""
    state = _make_state([
        _qa("headliner",       "Product Ops runs 6-8 daily batches taking 45 min each."),
        _qa("elevator_pitch",  "Engineering patrols brittle regex for every new supplier."),
    ])
    result = infer_section_candidates("background", state)

    assert result["inference_available"] is True, (
        "Background should be inference_available when headliner + elevator_pitch answered"
    )
    assert len(result["evidence"]) >= 2, (
        f"Expected ≥2 evidence facts, got {len(result['evidence'])}"
    )
    assert "headliner" in result["evidence_sources"]
    assert "elevator_pitch" in result["evidence_sources"]


# ── T2 ────────────────────────────────────────────────────────────────────────

def test_background_question_is_summary_confirm_not_blank_prompt():
    """The Background candidate must be a confirm/correct synthesis, not a
    blank field-label prompt like 'Tell me the current state'."""
    state = _make_state([
        _qa("headliner",       "Product Ops runs 6-8 daily batches taking 45 min each."),
        _qa("elevator_pitch",  "Engineering patrols brittle regex for every new supplier."),
        _qa("key_stakeholders", "Supplier ops team, engineering, and catalog team."),
    ])
    result = infer_section_candidates("background", state)

    candidates = result.get("candidate_items", [])
    assert len(candidates) > 0, "Expected at least one candidate when inference_available=True"
    candidate = candidates[0]

    # Must be a confirm/correct synthesis — not a blank prompt
    _blank_phrases = [
        "tell me more",
        "could you share",
        "what is the current state",
        "description of the current state",
        "can you describe",
    ]
    for phrase in _blank_phrases:
        assert phrase.lower() not in candidate.lower(), (
            f"Candidate contains blank prompt phrase '{phrase}'. Got: {candidate!r}"
        )

    # Must be a confirm/correct form
    confirm_signals = [
        "is that an accurate picture",
        "what would you add or correct",
        "from what you've shared",
        "here's my read",
    ]
    assert any(sig.lower() in candidate.lower() for sig in confirm_signals), (
        f"Candidate is not a confirm/correct synthesis. Got: {candidate!r}"
    )


# ── T3 ────────────────────────────────────────────────────────────────────────

def test_background_does_not_repeat_pain_point_field_label_language():
    """Background question must not use field-label language like
    'description of the current state or pain point'."""
    state = _make_state([
        _qa("headliner",       "Manual alignment of inconsistent supplier data."),
        _qa("problem_statement", "8-12% of records error on ingestion."),
    ])
    result = infer_section_candidates("background", state)

    candidates = result.get("candidate_items", [])
    if not candidates:
        # sparse evidence → seed path; just check seed is not blank label
        seed = result.get("seed_context_hint", "")
        _bad = "description of the current state or pain point"
        assert _bad not in seed.lower(), (
            f"Seed contains field-label language. Got: {seed!r}"
        )
        return

    candidate = candidates[0]
    _bad_labels = [
        "description of the current state or pain point",
        "what is your current state",
        "provide a description",
    ]
    for label in _bad_labels:
        assert label.lower() not in candidate.lower(), (
            f"Field-label language found in candidate: {label!r}\nGot: {candidate!r}"
        )


# ── T4 ────────────────────────────────────────────────────────────────────────

def test_background_structured_success_still_produces_specific_question():
    """Even with structured extraction succeeding upstream, the Background inferrer
    should produce a specific synthesis candidate (not empty) when evidence exists."""
    state = _make_state([
        _qa("headliner",       "Daily supplier batch workflow takes 45 min per run."),
        _qa("elevator_pitch",  "Regex patching brittle, breaks on new suppliers."),
        _qa("key_stakeholders", "Product Ops, engineering, catalog team."),
    ])
    result = infer_section_candidates("background", state)

    # The structured path succeeded (high confidence), candidate should be non-empty
    assert result.get("confidence") in ("medium", "high"), (
        f"Expected medium or high confidence with 3 evidence facts. Got: {result.get('confidence')}"
    )
    candidates = result.get("candidate_items", [])
    assert len(candidates) > 0, "Background with 3 evidence facts must produce a synthesis candidate"
    assert len(candidates[0]) > 40, "Candidate must be a proper sentence, not a fragment"


# ── T5 ────────────────────────────────────────────────────────────────────────

def test_background_after_key_stakeholders_uses_existing_workflow_facts():
    """When key_stakeholders has been answered and describes a workflow,
    the Background inferrer must incorporate it into the synthesis candidate."""
    stakeholder_text = (
        "Product Ops runs supplier ingestion. Engineering patches regex rules. "
        "Catalog team is downstream consumer of corrected data."
    )
    state = _make_state([
        _qa("headliner",        "Semi-manual mapping workflow runs 6-8 times daily."),
        _qa("key_stakeholders", stakeholder_text),
    ])
    result = infer_section_candidates("background", state)

    assert result["inference_available"] is True, (
        "Background should be inference_available with headliner + key_stakeholders"
    )
    candidates = result.get("candidate_items", [])
    assert len(candidates) > 0

    # At least one upstream fact should appear in the candidate (truncated to 120 chars)
    combined_candidate = candidates[0]
    assert "key_stakeholders" in result["evidence_sources"], (
        "key_stakeholders evidence must be captured"
    )
    # Evidence text from key_stakeholders should be a substring of the candidate
    snippet = stakeholder_text[:50].lower()
    assert snippet in combined_candidate.lower() or "product ops" in combined_candidate.lower(), (
        f"key_stakeholders workflow fact not reflected in synthesis candidate.\nGot: {combined_candidate!r}"
    )
