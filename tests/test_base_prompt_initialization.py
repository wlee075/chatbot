"""
Tests: base_prompt always initialized before any orchestrator injection helper
is called in generate_questions_node.

Regression guard for: UnboundLocalError — base_prompt referenced before
assignment when orch_plan routes through SEED, PROPOSE_CONSTRAINED, or
safe_clarify paths before _build_elicitor_prompt_context was called.
"""
import contextlib
import pytest
from unittest.mock import MagicMock, patch

from graph.nodes import generate_questions_node
from config.sections import get_section_by_index, PRD_SECTIONS


SM_IDX = next(i for i, s in enumerate(PRD_SECTIONS) if s.id == "success_metrics")
SUMMARY_IDX = next((i for i, s in enumerate(PRD_SECTIONS) if s.id == "summary"), 0)


def _make_state(section_index=None, **overrides):
    idx = SM_IDX if section_index is None else section_index
    s = {
        "thread_id": "t-bp", "run_id": "r-bp",
        "section_index": idx, "iteration": 1,
        "remaining_subparts": [], "concept_conflicts": [],
        "recent_questions": [], "confirmed_qa_store": {},
        "section_gap_state": {},
        "prd_sections": {"success_metrics": "", "summary": ""},
        "overall_score": -1.0, "verdict": "REWORK",
        "recovery_mode_consecutive_count": 0, "phase": "elicitation",
        "active_question_id": "", "user_input": "We want to improve latency",
        "guardrail_intent": "PROVIDE_ANSWER",
    }
    s.update(overrides)
    return s


_FAKE_NORM = {"single_next_question": "What is the baseline?",
              "question_id": "q1", "subparts": [], "gap_reason": ""}
_FAKE_PKG = {
    "current_questions": "What is the baseline?",
    "generation_status": "question_generated",
    "selected_candidate_id": "q1", "duplicate_details": {},
    "content_segments": [], "current_question_object": {},
    "remaining_subparts": [], "active_question_id": "q1",
    "active_question_type": "open", "active_question_options": [],
    "question_status": "active", "resolved_option_id": "",
    "answered_at": "", "recent_questions": [], "repair_instruction": "",
}


def _fresh_ctx(*a, **kw):
    return {"thread_id": "t1", "run_id": "r1",
            "node_name": "generate_questions_node", "_t0": 0}


def _base_orch_plan(**overrides):
    plan = {
        "recommended_action": "DIRECT_ELICIT",
        "is_live_prompt_eligible": True,
        "confidence": "MEDIUM",
        "target_section_id": "success_metrics",
        "candidate_items": [],
        "seed_context_hint": "",
        "reasoning_summary": "",
        "section_jumped": False,
        "feature_framing_detected": False,
        "is_inference_first": False,
    }
    plan.update(overrides)
    return plan


def _run(orch_plan, section_index=None, extra_patches=None, **state_kw):
    """Run generate_questions_node with standard mocks + a given orchestrator plan."""
    idx = section_index if section_index is not None else SM_IDX
    patches = [
        patch("graph.nodes.get_section_by_index",
              return_value=get_section_by_index(idx)),
        patch("graph.nodes._log_ctx", side_effect=_fresh_ctx),
        patch("graph.nodes.log_event"),
        patch("graph.nodes.build_conversation_understanding_output", return_value={}),
        patch("graph.nodes._maybe_emit_numeric_repair_prompt", return_value=None),
        patch("graph.nodes._maybe_emit_conflict_resolution_question", return_value=None),
        patch("graph.nodes._maybe_emit_resolved_branch_question", return_value=None),
        patch("graph.nodes._build_elicitor_prompt_context",
              return_value="MOCK_BASE_PROMPT"),
        patch("graph.nodes._invoke_structured_question_generator",
              return_value=(_FAKE_NORM, "")),
        patch("graph.nodes._normalize_generated_question",
              return_value=(_FAKE_NORM, "")),
        patch("graph.nodes._construct_final_question_text",
              return_value="What is the baseline?"),
        patch("graph.nodes._evaluate_duplicate_candidate",
              return_value=(True, "", None)),
        patch("graph.nodes._package_generated_question_result",
              return_value=_FAKE_PKG),
        patch("graph.nodes._package_no_question_result", return_value=_FAKE_PKG),
        patch("graph.nodes._extract_draft_markers", return_value=None),
        # The real orchestrator call in generate_questions_node:
        patch("graph.nodes.inference_first_prd_orchestrator",
              return_value=orch_plan),
    ]
    if extra_patches:
        patches.extend(extra_patches)
    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        return generate_questions_node(
            _make_state(section_index=idx, **state_kw)
        )


# ── Test 1: SEED path ──────────────────────────────────────────────────────
def test_generate_questions_seed_hint_path_has_base_prompt():
    """SEED override must not raise UnboundLocalError."""
    result = _run(
        _base_orch_plan(recommended_action="SEED_QUESTION",
                        seed_context_hint="Latency matters"),
        extra_patches=[
            patch("graph.nodes._inject_seed_hint",
                  return_value="MOCK_BASE_PROMPT+seed"),
        ],
    )
    assert isinstance(result, dict), "Should return dict, not crash"


# ── Test 2: PROPOSE_CONSTRAINED ────────────────────────────────────────────
def test_generate_questions_candidate_path_has_base_prompt():
    """PROPOSE_CONSTRAINED must receive initialized base_prompt for injection."""
    result = _run(
        _base_orch_plan(
            recommended_action="PROPOSE_ONE",
            is_live_prompt_eligible=False,   # → PROPOSE_CONSTRAINED, not deterministic
            candidate_items=[{"name": "latency_p50", "evidence": "user said latency"}],
        ),
        extra_patches=[
            patch("graph.nodes._inject_orch_candidates",
                  return_value="MOCK_BASE_PROMPT+cands"),
        ],
    )
    assert isinstance(result, dict)


# ── Test 3: safe_clarify (all candidates rejected) ─────────────────────────
def test_generate_questions_safe_clarify_path_has_base_prompt():
    """When deterministic formatter returns None, safe_clarify must not crash."""
    result = _run(
        _base_orch_plan(
            recommended_action="PROPOSE_ONE",
            is_live_prompt_eligible=True,   # → tries deterministic path → None
            candidate_items=[{"name": "\x00corrupted\x00", "evidence": ""}],
        ),
        extra_patches=[
            patch("graph.nodes._format_deterministic_propose_question",
                  return_value=None),
            patch("graph.nodes._build_safe_clarification_question",
                  return_value="Could you share more about success metrics?"),
        ],
    )
    assert isinstance(result, dict)


# ── Test 4: orchestrator raises (orch_plan={} fallback) ───────────────────
def test_generate_questions_empty_orch_plan_has_base_prompt():
    """When orchestrator fails, default prompt path must not crash."""
    result = _run(
        {},   # empty plan — triggers the except branch which sets orch_plan={}
        extra_patches=[
            # Override the return_value patch with a side_effect to simulate failure
            patch("graph.nodes.inference_first_prd_orchestrator",
                  side_effect=RuntimeError("orchestrator unavailable")),
        ],
    )
    assert isinstance(result, dict)


# ── Test 5: fresh session, Summary section ────────────────────────────────
def test_first_turn_summary_section_does_not_crash():
    """First turn on Summary section must return a question dict without crashing."""
    result = _run(
        _base_orch_plan(
            recommended_action="DIRECT_ELICIT",
            target_section_id="summary",
        ),
        section_index=SUMMARY_IDX,
        iteration=0,
        confirmed_qa_store={},
        user_input=(
            "We are building a productivity tool for remote teams that tracks "
            "async standups and surfaces blockers automatically."
        ),
    )
    assert isinstance(result, dict), "First-turn Summary section must not crash"
    assert "current_questions" in result
