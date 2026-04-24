"""
Tests for question_target_section_consistency_guard.

Acceptance criteria:
  AC1. If the bot asks a stakeholder question, the next answer must write to key_stakeholders.
  AC2. If UI says Elevator Pitch but question target is Key Stakeholders, the system blocks
       before await_answer or before truth_commit (emits mismatch clarification).
  AC3. no_question_available cannot silently advance when state alignment is inconsistent.
  AC4. ITER_CAP cannot override section-target mismatch.
  AC5. cross_section_target bypasses the guard (legitimate cross-section flow).
"""
import pytest
from unittest.mock import MagicMock, patch


# ── Module-level mocks to avoid heavy imports ─────────────────────────────────
import graph.nodes
graph.nodes.log_event = MagicMock()
graph.nodes.log_suppression_decision = MagicMock()
graph.nodes._get_llm = MagicMock()

import graph.routing
graph.routing.log_event = MagicMock()

from graph.routing import route_after_generate_questions
from graph.state import PRDState


# ── Helper ────────────────────────────────────────────────────────────────────

class _Sec:
    """Minimal section stub."""
    def __init__(self, sid, title):
        self.id = sid
        self.title = title
        self.expected_components = []
        self.description = ""


# ── AC3: no_question_available must NOT silently advance on target mismatch ───

def test_no_question_available_does_not_block_at_router_when_target_matches():
    """Normal path: target == active section → route to draft (no mismatch)."""
    state = PRDState({
        "generation_status": "no_question_available",
        "last_question_target_section_id": "elevator_pitch",
        "section_index": 1,  # elevator_pitch is index 1
    })
    route = route_after_generate_questions(state)
    assert route == "draft", "Should proceed to draft when target matches active section"


def test_section_target_mismatch_observed_is_logged_by_router():
    """
    AC3: When no_question_available reaches the router with a mismatched stamp,
    the router logs section_target_mismatch_observed (observability-only).
    The clarification was already emitted by generate_questions_node.
    """
    graph.routing.log_event.reset_mock()
    state = PRDState({
        "generation_status": "no_question_available",
        "last_question_target_section_id": "key_stakeholders",  # orch target
        "section_index": 1,   # elevator_pitch (index 1) ≠ key_stakeholders (index 2)
        "cross_section_target": None,
    })
    # Router still routes to draft (mismatch was already caught by the node)
    route = route_after_generate_questions(state)
    assert route == "draft"
    # Confirm the defensive log was emitted
    observed_calls = [
        call for call in graph.routing.log_event.call_args_list
        if call.kwargs.get("event_type") == "section_target_mismatch_observed"
    ]
    assert len(observed_calls) == 1, "Router must log section_target_mismatch_observed"
    assert observed_calls[0].kwargs["last_question_target_section_id"] == "key_stakeholders"
    assert observed_calls[0].kwargs["active_section_id"] == "elevator_pitch"


def test_cross_section_target_bypasses_router_mismatch_log():
    """AC5: When cross_section_target is explicitly set, no mismatch warning logged."""
    graph.routing.log_event.reset_mock()
    state = PRDState({
        "generation_status": "no_question_available",
        "last_question_target_section_id": "key_stakeholders",
        "section_index": 1,
        "cross_section_target": "key_stakeholders",  # explicit bypass
    })
    route = route_after_generate_questions(state)
    assert route == "draft"
    mismatch_calls = [
        c for c in graph.routing.log_event.call_args_list
        if c.kwargs.get("event_type") == "section_target_mismatch_observed"
    ]
    assert len(mismatch_calls) == 0, "cross_section_target must suppress mismatch log"


# ── AC4: ITER_CAP cannot override section-target mismatch ─────────────────────

@patch("graph.nodes.log_event")
@patch("graph.nodes.log_canonical_write", MagicMock())
@patch("graph.nodes.get_section_by_index")
def test_iter_cap_does_not_advance_after_question_target_mismatch(
    mock_get_section, mock_log
):
    """
    AC4: advance_section_node with ITER_CAP must return REWORK when
    last_question_target_section_id != section.id and no cross_section_target.
    """
    from graph.nodes import advance_section_node

    active_section = _Sec("elevator_pitch", "Elevator Pitch")
    mock_get_section.return_value = active_section

    state = PRDState({
        "section_index": 1,
        "iteration": 5,          # past iteration cap
        "verdict": "REWORK",     # not PASS → ITER_CAP path
        "recovery_mode_consecutive_count": 0,
        "overall_score": 0.4,
        "max_iterations": 3,
        "last_question_target_section_id": "key_stakeholders",  # mismatch!
        "cross_section_target": None,
        "section_gap_state": {},
        "current_draft": "draft text",
        "confirmed_qa_store": {},
    })

    result = advance_section_node(state)

    assert result.get("verdict") == "REWORK", (
        "advance_section_node must return REWORK on ITER_CAP with target mismatch"
    )
    assert result.get("section_index") is None or result.get("section_index") == 1, (
        "section_index must not be incremented"
    )
    # Confirm the guard event was logged
    guard_calls = [
        c for c in mock_log.call_args_list
        if c.kwargs.get("event_type") == "advance_blocked_by_section_target_mismatch"
    ]
    assert len(guard_calls) == 1, "advance_blocked_by_section_target_mismatch must be logged"


@patch("graph.nodes.log_event")
@patch("graph.nodes.log_canonical_write", MagicMock())
@patch("graph.nodes.get_section_by_index")
def test_iter_cap_does_advance_when_target_matches(mock_get_section, mock_log):
    """
    Regression: when target stamp matches active section, ITER_CAP must NOT block.
    advance_section_node should proceed normally (we just verify no mismatch block event).
    """
    from graph.nodes import advance_section_node
    from config.sections import PRD_SECTIONS

    active_section = _Sec("elevator_pitch", "Elevator Pitch")
    mock_get_section.return_value = active_section

    state = PRDState({
        "section_index": 1,
        "iteration": 5,
        "verdict": "REWORK",
        "recovery_mode_consecutive_count": 0,
        "overall_score": 0.4,
        "max_iterations": 3,
        "last_question_target_section_id": "elevator_pitch",  # matches
        "cross_section_target": None,
        "section_gap_state": {},
        "current_draft": "draft text",
        "confirmed_qa_store": {},
    })

    result = advance_section_node(state)

    guard_block_calls = [
        c for c in mock_log.call_args_list
        if c.kwargs.get("event_type") == "advance_blocked_by_section_target_mismatch"
    ]
    assert len(guard_block_calls) == 0, (
        "No advance_blocked_by_section_target_mismatch when target matches active section"
    )


# ── AC2 / AC1: generate_questions_node emits clarification on SECTION_COMPLETE mismatch ──

@patch("graph.nodes._classify_target_confidence", return_value=("EMPTY", None, [], "all canonical resolved", "SECTION_COMPLETE"))
@patch("graph.nodes.inference_first_prd_orchestrator")
@patch("graph.nodes.build_conversation_understanding_output", return_value={
    "current_concepts": [], "historical_concepts": [], "negated_concepts": [],
    "example_only_concepts": [], "future_or_planned_concepts": [], "conflicted_concepts": [],
    "unresolved_blockers": [], "draft_readiness": {"is_ready": False, "hard_blockers": [], "advisory_warnings": []},
    "corrections_recently_applied": [], "action_candidates_if_any": [],
})
@patch("graph.nodes._build_elicitor_prompt_context", return_value="prompt")
@patch("graph.nodes._maybe_emit_numeric_repair_prompt", return_value=None)
@patch("graph.nodes._maybe_emit_conflict_resolution_question", return_value=None)
@patch("graph.nodes._maybe_emit_resolved_branch_question", return_value=None)
@patch("graph.nodes.get_section_by_index")
def test_section_complete_with_target_mismatch_emits_clarification(
    mock_get_sec, mock_branch, mock_conflict, mock_repair,
    mock_prompt, mock_infer_bridge, mock_orch, mock_classify,
):
    """
    AC2: When SECTION_COMPLETE fires but the orchestrator's target_section_id != section.id,
    the node must NOT return no_question_available — it emits a mismatch clarification instead.
    We intercept _enforce_visibility to capture the payload.
    """
    from graph.nodes import generate_questions_node

    active_section = _Sec("elevator_pitch", "Elevator Pitch")
    mock_get_sec.return_value = active_section

    # Orchestrator decided it was asking about key_stakeholders — not what UI shows
    mock_orch.return_value = {
        "target_section_id": "key_stakeholders",
        "recommended_action": "DIRECT_ELICIT",
        "is_live_prompt_eligible": True,
        "is_inference_first": False,
        "section_jumped": False,
        "feature_framing_detected": False,
        "confidence": 0.9,
        "candidate_items": [],
        "seed_context_hint": "",
        "reasoning_summary": "stakeholders already covered",
    }

    captured_payloads = []

    def _capture_enforce(d, *args, **kwargs):
        captured_payloads.append(d.copy())
        return d

    state = PRDState({
        "section_index": 1,        # elevator_pitch
        "iteration": 0,
        "materialization_conflict": False,
        "pending_numeric_clarification": False,
        "section_gap_state": {},
        "generation_status": "",
        "cross_section_target": None,
        "recent_questions": [],
    })

    with patch("graph.nodes._enforce_visibility", side_effect=_capture_enforce):
        result = generate_questions_node(state)

    # We expect the mismatch guard fired and returned a clarification payload.
    # The clarification payload has override_status='section_target_mismatch_clarify',
    # and generation_status is NOT 'no_question_available'.
    assert result.get("generation_status") != "no_question_available", (
        "guard must prevent no_question_available return when target mismatches active section"
    )
    # At least one payload passed through _enforce_visibility must carry the mismatch status
    override_statuses = [p.get("generation_status", "") for p in captured_payloads]
    assert "section_target_mismatch_clarify" in override_statuses, (
        f"Expected section_target_mismatch_clarify in payloads, got: {override_statuses}"
    )
    # The mismatch payload must include the correct last_question_target_section_id stamp
    mismatch_payloads = [p for p in captured_payloads if p.get("generation_status") == "section_target_mismatch_clarify"]
    assert mismatch_payloads[0].get("last_question_target_section_id") == "key_stakeholders", (
        "Mismatch clarification must stamp the orchestrator's target section, not section.id"
    )
