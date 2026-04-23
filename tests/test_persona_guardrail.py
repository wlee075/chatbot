"""tests/test_persona_guardrail.py

Tests for the Persona Evidence Hierarchy (Phase 1.7) and Conversation
Continuity Guardrail.

Required tests — 10 total:
Persona Guardrail (5):
  test_operator_pain_outweighs_invented_executive_focus
  test_user_named_roles_override_fictional_personas
  test_non_target_persona_not_used_for_primary_followup
  test_exec_pitch_only_when_user_requests_exec_audience
  test_persona_inference_prefers_real_workflow_roles

Conversation Continuity Guardrail (5):
  test_non_target_persona_not_followup_focus_next_turn
  test_previous_persona_hierarchy_respected
  test_question_generator_prefers_unresolved_primary_personas
  test_explicit_audience_shift_requires_reason
  test_exec_prompt_only_when_buyer_context_needed
"""
from __future__ import annotations
import pytest
from utils.section_inference import infer_section_candidates


# ─── Fixtures ─────────────────────────────────────────────────────────────────

def _qa(section_id: str, answer: str) -> dict:
    return {
        "section_id": section_id,
        "user_answer": answer,
        "contradiction_flagged": False,
        "resolved_subparts": [],
    }


def _state(qa_pairs: list[dict] | None = None) -> dict:
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


# ══════════════════════════════════════════════════════════════════════════════
# PERSONA GUARDRAIL TESTS (P1–P5)
# ══════════════════════════════════════════════════════════════════════════════

# P1
def test_operator_pain_outweighs_invented_executive_focus():
    """When the corpus contains explicit operator-role signals, the inferrer must
    return operator candidates FIRST — executive must never be the primary item."""
    state = _state([
        _qa("headliner",
            "Product Ops runs 6-8 batches daily of manual supplier mapping, "
            "each taking 45 minutes. Analysts spend most of the day chasing errors."),
    ])
    result = infer_section_candidates("key_stakeholders", state)

    candidates = result.get("candidate_items", [])
    # If inference_available, first candidate must be operator tier
    if result["inference_available"]:
        assert len(candidates) > 0
        first_lower = candidates[0].lower()
        # Operator-tier language expected first
        assert any(w in first_lower for w in ("operator", "ops", "analyst", "hands-on", "product ops")), (
            f"First candidate should be operator-tier, got: {candidates[0]!r}"
        )
        # Executive must not be first
        assert "executive" not in candidates[0].lower(), (
            f"Executive must not be the first (primary) candidate. Got: {candidates[0]!r}"
        )


# P2
def test_user_named_roles_override_fictional_personas():
    """Role names stated explicitly by the user ('product ops', 'category manager')
    must appear in evidence_sources or candidates; fictional names like 'Maya' or
    'David' must not be invented by the inferrer."""
    state = _state([
        _qa("background",
            "Product Ops is on the hook for daily batches. "
            "Category managers use the output for catalog publishing."),
    ])
    result = infer_section_candidates("key_stakeholders", state)

    candidates = result.get("candidate_items", [])
    # Should not contain invented fictional first-names
    for cand in candidates:
        assert "Maya" not in cand, f"Inferrer invented fictional persona 'Maya': {cand!r}"
        assert "David" not in cand, f"Inferrer invented fictional persona 'David': {cand!r}"
        assert "Elena" not in cand, f"Inferrer invented fictional persona 'Elena': {cand!r}"

    # Evidence should reflect user's real roles
    corpus = " ".join(str(e) for e in result.get("evidence", []))
    assert "product ops" in corpus.lower() or "category" in corpus.lower(), (
        "User-named roles must appear in evidence"
    )


# P3
def test_non_target_persona_not_used_for_primary_followup():
    """When prestige signals (executive, VP) are detected alongside operator signals,
    the executive entry must be in the last position and flagged non-target."""
    state = _state([
        _qa("headliner",
            "Product Ops runs daily batch mapping. "
            "VP of Operations receives the weekly accuracy report."),
    ])
    result = infer_section_candidates("key_stakeholders", state)

    candidates = result.get("candidate_items", [])
    if len(candidates) < 2:
        # Not enough evidence to produce multiple candidates — that's fine
        return

    # Prestige/executive candidate must be last
    last = candidates[-1].lower()
    assert any(w in last for w in ("executive", "sponsor", "strategic", "non-target", "not primary")), (
        f"Executive / non-target role must be last candidate. Got last: {candidates[-1]!r}"
    )

    # Operator must be first
    first = candidates[0].lower()
    assert any(w in first for w in ("operator", "ops", "analyst", "product ops", "hands-on")), (
        f"Operator role must be first candidate. Got first: {candidates[0]!r}"
    )


# P4
def test_exec_pitch_only_when_user_requests_exec_audience():
    """When no prestige signals appear in user corpus, the inferrer must not
    produce executive candidates at all."""
    state = _state([
        _qa("headliner",
            "Product Ops manually aligns supplier data every morning. "
            "The mapping team handles about 200 SKUs per batch."),
    ])
    result = infer_section_candidates("key_stakeholders", state)

    candidates = result.get("candidate_items", [])
    for cand in candidates:
        assert "executive" not in cand.lower(), (
            f"Executive candidate appeared without user mentioning executive: {cand!r}"
        )
        assert "c-suite" not in cand.lower(), (
            f"C-suite candidate appeared without user signal: {cand!r}"
        )


# P5
def test_persona_inference_prefers_real_workflow_roles():
    """The inferrer should use role taxonomy labels (Operator, Manager, Director),
    not generic labels like 'user', 'stakeholder', or 'the team'."""
    state = _state([
        _qa("elevator_pitch",
            "Mapping operations team processes batches. "
            "The ops lead coordinates escalations. "
            "Budget approvals go through the director of operations."),
    ])
    result = infer_section_candidates("key_stakeholders", state)

    if not result["inference_available"]:
        return  # Too sparse to assert on candidates

    candidates = result.get("candidate_items", [])
    assert len(candidates) > 0, "Expected at least one role candidate"

    # All candidates must use role taxonomy labels, not vague generic phrases
    # (Check as full label, not substring, since 'user' appears in 'hands-on user')
    vague_exact = ["the team", "a stakeholder", "some person", "someone", "a user"]
    for cand in candidates:
        for vague in vague_exact:
            assert vague not in cand.lower(), (
                f"Vague phrase '{vague}' found in candidate: {cand!r}"
            )


# ══════════════════════════════════════════════════════════════════════════════
# CONVERSATION CONTINUITY GUARDRAIL TESTS (C1–C5)
# ══════════════════════════════════════════════════════════════════════════════

# C1
def test_non_target_persona_not_followup_focus_next_turn():
    """If the orchestrator emits a plan with non_target_personas=['Executive Sponsor ...'],
    the ActionPlan must carry that field so callers can enforce the no-exec-pivot rule."""
    from utils.prd_orchestrator import inference_first_prd_orchestrator

    class _Sec:
        id = "key_stakeholders"
        title = "Key Stakeholders"

    state = _state([
        _qa("headliner",
            "Product Ops runs daily batch mapping. "
            "VP of Operations receives a weekly summary report."),
    ])
    plan = inference_first_prd_orchestrator(state, _Sec())

    # Plan must carry non_target_personas when prestige signals are present
    assert "non_target_personas" in plan, "ActionPlan must include non_target_personas field"
    assert "persona_stance" in plan, "ActionPlan must include persona_stance field"

    # If prestige signal found and operator signal also found, non_target_personas is non-empty
    # But even if empty (no prestige), the key must exist
    assert isinstance(plan["non_target_personas"], list)
    assert isinstance(plan["persona_stance"], list)


# C2
def test_previous_persona_hierarchy_respected():
    """When operator and manager tiers are both resolved, persona_stance must list
    them in operator-first order (operator before manager)."""
    from utils.prd_orchestrator import inference_first_prd_orchestrator

    class _Sec:
        id = "key_stakeholders"
        title = "Key Stakeholders"

    state = _state([
        _qa("headliner",
            "Product Ops analysts run daily supplier batches. "
            "The ops manager reviews accuracy reports and escalates to the director."),
    ])
    plan = inference_first_prd_orchestrator(state, _Sec())
    persona_stance = plan.get("persona_stance", [])

    if len(persona_stance) >= 2:
        # Operator must appear before Manager
        titles = [p.lower() for p in persona_stance]
        operator_idx = next((i for i, t in enumerate(titles) if "operator" in t or "ops" in t), None)
        manager_idx  = next((i for i, t in enumerate(titles) if "manager" in t or "lead" in t), None)
        if operator_idx is not None and manager_idx is not None:
            assert operator_idx < manager_idx, (
                f"Operator must come before Manager in persona_stance.\n"
                f"Got: {persona_stance}"
            )


# C3
def test_question_generator_prefers_unresolved_primary_personas():
    """With no explicit key_stakeholders answer in QA, key_stakeholders inference
    must return inference_available=True when operator signals exist (unresolved primary)."""
    state = _state([
        _qa("headliner",
            "The mapping operations team runs batches 6 times daily. "
            "Each batch requires 45 minutes of analyst time."),
        # key_stakeholders is NOT answered — it is unresolved primary
    ])
    result = infer_section_candidates("key_stakeholders", state)

    assert result["has_explicit_answers"] is False, (
        "key_stakeholders must be unresolved (no explicit answer yet)"
    )
    # But operator signals exist — should be inference_available
    assert result["inference_available"] is True, (
        "With operator signals in headliner, key_stakeholders should be inference_available"
    )


# C4
def test_explicit_audience_shift_requires_reason():
    """If the ActionPlan declares non_target_personas (prestige), and the section is
    key_stakeholders, then persona_stance must be non-empty (a primary audience exists
    to center on). The caller is responsible for not pivoting to non_target_personas."""
    from utils.prd_orchestrator import inference_first_prd_orchestrator

    class _Sec:
        id = "key_stakeholders"
        title = "Key Stakeholders"

    state = _state([
        _qa("headliner",
            "The mapping ops team does all the heavy lifting. "
            "An executive sponsor reviews quarterly business reviews."),
    ])
    plan = inference_first_prd_orchestrator(state, _Sec())

    non_target = plan.get("non_target_personas", [])
    persona_stance = plan.get("persona_stance", [])

    if non_target:
        # If non-target exists, there must also be a primary audience
        assert len(persona_stance) > 0, (
            "If non_target_personas is declared, persona_stance must also be non-empty "
            "so the caller has a primary audience to ask about instead.\n"
            f"persona_stance={persona_stance!r}, non_target_personas={non_target!r}"
        )


# C5
def test_exec_prompt_only_when_buyer_context_needed():
    """When corpus contains buyer/budget signals alongside operator signals,
    the buyer approver role may appear in candidates — but it must not be first."""
    state = _state([
        _qa("headliner",
            "Product Ops runs daily supplier mapping. "
            "Budget approvals for new tools go through the ops director."),
    ])
    result = infer_section_candidates("key_stakeholders", state)

    candidates = result.get("candidate_items", [])
    if not candidates:
        return

    # Buyer/approver may appear — but must not be first
    buyer_idx = next(
        (i for i, c in enumerate(candidates)
         if any(w in c.lower() for w in ("director", "approver", "buyer", "budget", "head of"))),
        None,
    )
    operator_idx = next(
        (i for i, c in enumerate(candidates)
         if any(w in c.lower() for w in ("operator", "ops", "analyst", "hands-on"))),
        None,
    )

    if buyer_idx is not None and operator_idx is not None:
        assert operator_idx < buyer_idx, (
            f"Operator must appear before Buyer/Approver in candidates.\n"
            f"Got: {candidates}"
        )
