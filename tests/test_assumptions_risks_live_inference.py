"""Step 2 tests — Assumptions and Risks live inference promotion.

Guarantees:
1. assumptions and risks are listed in LIVE_PROMPT_SECTIONS.
2. When dependency/validation/risk signals exist in prior sections, inference
   returns candidates (not empty) — so the orchestrator will inject them into
   the prompt instead of falling to generic elicitation.
3. When candidates exist, orchestrator action is NOT DIRECT_ELICIT.
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from utils.section_inference import LIVE_PROMPT_SECTIONS, infer_section_candidates
from utils.prd_orchestrator import (
    inference_first_prd_orchestrator,
    ACTION_DIRECT_ELICIT,
    ACTION_PROPOSE_ONE,
    ACTION_PROPOSE_LIST,
    ACTION_SEED_QUESTION,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

class DummySection:
    def __init__(self, section_id: str):
        self.id = section_id
        self.title = section_id.replace("_", " ").title()
        self.expected_components: list = []


def _make_state(qa_pairs: list | None = None) -> dict:
    store: dict = {}
    for i, pair in enumerate(qa_pairs or []):
        store[f"qk_{i}"] = pair
    return {
        "confirmed_qa_store": store,
        "prd_sections": {},
        "section_scores": {},
        "section_index": 0,
        "iteration": 1,
        "recent_questions": [],
    }


def _answered(section_id: str, answer: str) -> dict:
    return {
        "section_id": section_id,
        "question": "Tell me about...",
        "answer": answer,
        "contradiction_flagged": False,
    }


# ── Test 1 — LIVE_PROMPT_SECTIONS contains assumptions and risks ──────────────

def test_assumptions_live_prompt_injection_enabled():
    """assumptions must appear in LIVE_PROMPT_SECTIONS after Phase 2 promotion."""
    assert "assumptions" in LIVE_PROMPT_SECTIONS, (
        "assumptions not in LIVE_PROMPT_SECTIONS — Phase 2 promotion may not have been applied"
    )


def test_risks_live_prompt_injection_enabled():
    """risks must appear in LIVE_PROMPT_SECTIONS after Phase 2 promotion."""
    assert "risks" in LIVE_PROMPT_SECTIONS, (
        "risks not in LIVE_PROMPT_SECTIONS — Phase 2 promotion may not have been applied"
    )


# ── Test 2 — No generic fallback when strong candidates exist ─────────────────

def test_assumptions_no_generic_fallback_when_candidates_exist():
    """When clear dependency/assumption signals exist in prior sections,
    the orchestrator must NOT return DIRECT_ELICIT for assumptions.

    DIRECT_ELICIT is the generic form-fallback. A sentence like
    'This depends on API stability being confirmed' is a textbook dependency
    assumption signal — the system should PROPOSE or SEED, never go blank.
    """
    state = _make_state(qa_pairs=[
        _answered(
            "goals",
            "Automate invoice matching. This depends on the supplier API being stable "
            "and data being available by Tuesday. We assume the infra is already in place.",
        ),
    ])
    result = infer_section_candidates("assumptions", state)

    # Signal-rich input must produce at least one candidate
    assert result["inference_available"] is True, (
        "Dependency/assumption signals present but inference_available=False"
    )
    assert len(result["candidate_items"]) >= 1, (
        "Expected ≥1 assumption candidates from dependency signals"
    )

    # Orchestrator must not return generic direct-elicit when candidates exist
    plan = inference_first_prd_orchestrator(state, DummySection("assumptions"))
    assert plan["recommended_action"] != ACTION_DIRECT_ELICIT, (
        f"Orchestrator returned DIRECT_ELICIT for assumptions despite strong candidates. "
        f"Action: {plan['recommended_action']}, confidence: {plan.get('confidence')}"
    )


def test_risks_no_generic_fallback_when_candidates_exist():
    """When clear risk/blocker/uncertainty signals exist in prior sections,
    the orchestrator must NOT return DIRECT_ELICIT for risks.

    A sentence like 'there's a risk of supplier non-compliance with the timeline'
    is a named risk — the system should propose or seed a confirm question,
    not ask 'What are your risks?'
    """
    state = _make_state(qa_pairs=[
        _answered(
            "goals",
            "Cut mapping errors by 80%. There's a risk of adoption resistance from "
            "the ops team and the timeline is aggressive. We're also uncertain whether "
            "the data quality will be sufficient.",
        ),
    ])
    result = infer_section_candidates("risks", state)

    # Signal-rich input must produce at least one candidate
    assert result["inference_available"] is True, (
        "Risk/uncertainty signals present but inference_available=False"
    )
    assert len(result["candidate_items"]) >= 1, (
        "Expected ≥1 risk candidates from blocker/uncertainty signals"
    )

    # Orchestrator must not return generic direct-elicit when candidates exist
    plan = inference_first_prd_orchestrator(state, DummySection("risks"))
    assert plan["recommended_action"] != ACTION_DIRECT_ELICIT, (
        f"Orchestrator returned DIRECT_ELICIT for risks despite strong candidates. "
        f"Action: {plan['recommended_action']}, confidence: {plan.get('confidence')}"
    )
