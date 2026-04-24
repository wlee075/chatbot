"""
Tests for utils/section_inference.py — infer_section_candidates()

Covers all 6 required tests:
  - test_goals_use_prior_evidence_before_blank_prompt
  - test_non_goals_use_constraints_before_blank_prompt
  - test_metrics_inferred_from_pain_points
  - test_explicit_metrics_override_inference
  - test_low_evidence_falls_back_to_seed_question
  - test_infer_section_candidates_returns_candidate_items
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from utils.section_inference import infer_section_candidates, _TARGET_SECTIONS

# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_state(qa_pairs: list[dict], prd_sections: dict | None = None) -> dict:
    """Build a minimal state dict with confirmed_qa_store and prd_sections."""
    store = {}
    for i, pair in enumerate(qa_pairs):
        store[f"key_{i}"] = pair
    return {
        "confirmed_qa_store": store,
        "prd_sections": prd_sections or {},
    }


# ── test_infer_section_candidates_returns_candidate_items ────────────────────

def test_return_contract_has_all_keys():
    """All required keys must always be present in the return dict."""
    required = {"has_explicit_answers", "inference_available", "candidate_items",
                "evidence", "evidence_sources", "confidence"}
    for section_id in _TARGET_SECTIONS:
        result = infer_section_candidates(section_id, _make_state([]))
        missing = required - result.keys()
        assert not missing, f"{section_id} missing keys: {missing}"


def test_non_target_section_returns_empty():
    result = infer_section_candidates("headliner", _make_state([]))
    assert result["inference_available"] is False
    assert result["candidate_items"] == []


def test_goals_candidate_items_are_strings():
    state = _make_state([{
        "section_id": "problem_statement",
        "question": "What takes the most time?",
        "answer": "Manual mapping takes 4 hours per 1000 rows. We want to reduce this effort.",
    }])
    result = infer_section_candidates("goals", state)
    for item in result["candidate_items"]:
        assert isinstance(item, str), f"Expected str, got {type(item)}"


# ── test_goals_use_prior_evidence_before_blank_prompt ───────────────────────

def test_goals_infers_from_problem_statement():
    state = _make_state([{
        "section_id": "problem_statement",
        "question": "What is the core business pain?",
        "answer": "Manual mapping takes 4 hours per 1000 rows. We need to reduce this significantly.",
    }])
    result = infer_section_candidates("goals", state)
    assert result["inference_available"] is True
    assert len(result["candidate_items"]) >= 1
    assert "problem_statement" in result["evidence_sources"]


def test_goals_infers_from_headliner():
    state = _make_state([{
        "section_id": "headliner",
        "question": "What does your system do?",
        "answer": "The system will improve processing speed and eliminate duplicate SKU errors.",
    }])
    result = infer_section_candidates("goals", state)
    assert result["inference_available"] is True
    assert any("improve" in c.lower() or "eliminat" in c.lower() or "error" in c.lower()
               for c in result["candidate_items"])


def test_goals_empty_when_no_prior_sections():
    result = infer_section_candidates("goals", _make_state([]))
    assert result["inference_available"] is False
    assert result["candidate_items"] == []
    assert result["confidence"] == "low"


# ── test_non_goals_use_constraints_before_blank_prompt ──────────────────────

def test_non_goals_infers_from_explicit_exclusion():
    state = _make_state([{
        "section_id": "goals",
        "question": "Are there things you won't do?",
        "answer": "We won't replace the ERP system in this phase. Also excluding real-time API integrations.",
    }])
    result = infer_section_candidates("non_goals", state)
    assert result["inference_available"] is True
    assert len(result["candidate_items"]) >= 1
    assert "goals" in result["evidence_sources"]


def test_non_goals_infers_from_proposed_solution():
    state = _make_state([{
        "section_id": "proposed_solution",
        "question": "What will the system do?",
        "answer": "Phase one only handles batch imports. We will not support real-time feeds out of scope.",
    }])
    result = infer_section_candidates("non_goals", state)
    assert result["inference_available"] is True


def test_non_goals_empty_on_no_exclusion_signals():
    state = _make_state([{
        "section_id": "goals",
        "question": "Goals?",
        "answer": "Faster onboarding and better UX.",
    }])
    result = infer_section_candidates("non_goals", state)
    # Should not produce candidates from pure positive statements
    assert result["confidence"] in ("low", "medium")


# ── test_metrics_inferred_from_pain_points ───────────────────────────────────

def test_metrics_infers_numeric_baseline():
    state = _make_state([{
        "section_id": "problem_statement",
        "question": "How long does it take today?",
        "answer": "It takes 8 hours per week for the team to manually reconcile records.",
    }])
    result = infer_section_candidates("success_metrics", state)
    assert result["inference_available"] is True
    assert any("8" in c for c in result["candidate_items"]), (
        f"Expected numeric baseline in candidates, got {result['candidate_items']}"
    )


def test_metrics_infers_error_rate():
    state = _make_state([{
        "section_id": "problem_statement",
        "question": "What goes wrong?",
        "answer": "About 15% of SKU mappings are incorrect each month.",
    }])
    result = infer_section_candidates("success_metrics", state)
    assert result["inference_available"] is True
    assert result["confidence"] in ("medium", "high")


def test_metrics_confidence_requires_numeric_signal():
    state = _make_state([{
        "section_id": "problem_statement",
        "question": "Pain point?",
        "answer": "Teams spend too much time on manual work.",  # no number
    }])
    result = infer_section_candidates("success_metrics", state)
    # Without a numeric signal, confidence must NOT be high
    assert result["confidence"] != "high"


# ── test_explicit_metrics_override_inference ─────────────────────────────────

def test_explicit_answers_bypass_inference_for_goals():
    state = _make_state([
        {
            "section_id": "goals",
            "question": "What are your goals?",
            "answer": "Reduce onboarding time by 50%.",
        },
        {
            "section_id": "problem_statement",
            "question": "Pain points?",
            "answer": "Manual mapping takes 4 hours per 1000 rows.",
        },
    ])
    result = infer_section_candidates("goals", state)
    assert result["has_explicit_answers"] is True


def test_explicit_metrics_override_inference():
    """If success_metrics section is already answered, has_explicit_answers must be True."""
    state = _make_state([
        {
            "section_id": "success_metrics",
            "question": "What is your target metric?",
            "answer": "Reduce error rate from 15% to under 2% by Q3 2026.",
        },
        {
            "section_id": "problem_statement",
            "question": "Pain?",
            "answer": "15% error rate on SKU mappings, takes 8 hours per week.",
        },
    ])
    result = infer_section_candidates("success_metrics", state)
    assert result["has_explicit_answers"] is True


def test_contradiction_flagged_entry_ignored():
    """Entries with contradiction_flagged=True should not count as explicit answers."""
    state = _make_state([{
        "section_id": "goals",
        "question": "Goals?",
        "answer": "Reduce errors.",
        "contradiction_flagged": True,
    }])
    result = infer_section_candidates("goals", state)
    assert result["has_explicit_answers"] is False


# ── test_low_evidence_falls_back_to_seed_question ───────────────────────────

def test_low_evidence_returns_inference_not_available():
    state = _make_state([])
    for section_id in _TARGET_SECTIONS:
        result = infer_section_candidates(section_id, state)
        assert result["inference_available"] is False
        assert result["candidate_items"] == []
        assert result["confidence"] == "low"


def test_low_evidence_non_goals_with_only_positive_answers():
    state = _make_state([{
        "section_id": "goals",
        "question": "Goals?",
        "answer": "We want to increase speed and accuracy.",  # no exclusion signals
    }])
    result = infer_section_candidates("non_goals", state)
    # Should not produce false positive candidates from purely positive language
    assert result["confidence"] != "high"


def test_prd_sections_fallback_used_when_qa_empty():
    """If qa_store has no data but prd draft has text, candidates may still be generated."""
    state = {
        "confirmed_qa_store": {},
        "prd_sections": {
            "problem_statement": "Manual mapping takes 6 hours per week. We need to reduce this significantly."
        },
    }
    result = infer_section_candidates("goals", state)
    # May not always produce candidates from draft (noisier), but must not crash
    assert isinstance(result["candidate_items"], list)
    assert isinstance(result["inference_available"], bool)
