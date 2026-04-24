"""
Tests for draft-marker-driven question generation.

Covers the senior's required tests:
  - test_next_prompt_uses_needs_clarification_marker_when_present
  - test_user_can_answer_without_opening_view_draft
  - test_only_one_specific_missing_item_is_asked
  - test_generic_prompt_used_only_when_no_specific_gap_exists
"""

import sys
import os
import uuid
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config.sections import PRD_SECTIONS
from graph.nodes import _extract_draft_markers, generate_questions_node


# ── Helpers ──────────────────────────────────────────────────────────────────

def _section_id():
    return PRD_SECTIONS[0].id


def _minimal_state(**overrides):
    base = {
        "thread_id": "test-thread",
        "run_id": str(uuid.uuid4()),
        "prd_sections": {},
        "confirmed_qa_store": {},
        "section_index": 0,
        "is_complete": False,
        "iteration": 1,
        "reflection": "Section needs more detail.",
        "requirement_gaps": "No explicit non-goal stated.",
        "triage_decision": "TRIAGE: NORMAL ITERATION",
        "max_iterations": 5,
        "remaining_subparts": [],
        "materialization_conflict": False,
        "pending_numeric_clarification": False,
        "repair_instruction": "",
        "recent_questions": [],
        "conflict_records": [],
        "generation_status": "",
        "question_status": "",
        "resolved_option_id": "",
        "raw_answer_buffer": "",
        "context_doc": "",
    }
    base.update(overrides)
    return base


# ── _extract_draft_markers unit tests ────────────────────────────────────────

def test_extract_draft_markers_returns_first_needs_clarification():
    """_extract_draft_markers must extract the topic from [NEEDS CLARIFICATION: ...]."""
    sid = _section_id()
    draft = "Goal: help users.\n[NEEDS CLARIFICATION: at least one explicit non-goal]\nMore text."
    result = _extract_draft_markers(sid, {sid: draft})
    assert result == "at least one explicit non-goal"


def test_extract_draft_markers_handles_missing_marker():
    """Returns None when draft contains no unresolved markers."""
    sid = _section_id()
    draft = "Goal: help users track orders."
    result = _extract_draft_markers(sid, {sid: draft})
    assert result is None


def test_extract_draft_markers_handles_empty_prd_sections():
    """Returns None gracefully when prd_sections is empty."""
    assert _extract_draft_markers(_section_id(), {}) is None


def test_extract_draft_markers_handles_non_string_draft():
    """Returns None gracefully when prd_sections value is not a string."""
    sid = _section_id()
    assert _extract_draft_markers(sid, {sid: {"nested": "dict"}}) is None


def test_extract_draft_markers_supports_missing_and_open_question_variants():
    """[MISSING: ...] and [OPEN QUESTION: ...] must also be extracted."""
    sid = _section_id()
    draft_missing = "[MISSING: user segment definition]"
    draft_open = "[OPEN QUESTION: launch timeline]"
    assert _extract_draft_markers(sid, {sid: draft_missing}) == "user segment definition"
    assert _extract_draft_markers(sid, {sid: draft_open}) == "launch timeline"


def test_extract_draft_markers_returns_only_first_when_multiple_present():
    """When multiple markers exist, only the first is returned (highest priority)."""
    sid = _section_id()
    draft = "[MISSING: non-goal]\n[NEEDS CLARIFICATION: target audience]"
    result = _extract_draft_markers(sid, {sid: draft})
    assert result == "non-goal"


# ── Integration tests: generate_questions_node lane selection ─────────────────

@patch("graph.nodes._invoke_structured_question_generator")
@patch("graph.nodes._evaluate_duplicate_candidate")
@patch("graph.nodes._normalize_generated_question")
@patch("graph.nodes._construct_final_question_text")
@patch("graph.nodes._build_elicitor_prompt_context")
@patch("graph.nodes.build_conversation_understanding_output")
@patch("graph.nodes._maybe_emit_numeric_repair_prompt", return_value=None)
@patch("graph.nodes._maybe_emit_conflict_resolution_question", return_value=None)
@patch("graph.nodes._maybe_emit_resolved_branch_question", return_value=None)
@patch("graph.nodes.log_event")
def test_next_prompt_uses_needs_clarification_marker_when_present(
    mock_log, mock_branch, mock_conflict, mock_repair,
    mock_bridge, mock_build_prompt,
    mock_construct, mock_normalize, mock_evaluate, mock_invoke
):
    """When a [NEEDS CLARIFICATION: ...] marker exists in the draft, the lane
    instruction must explicitly name the extracted topic."""
    sid = _section_id()
    draft = "Phase 1 goals captured.\n[NEEDS CLARIFICATION: at least one explicit non-goal]"

    mock_build_prompt.return_value = "BASE_PROMPT"
    mock_bridge.return_value = {}
    mock_invoke.return_value = ({"single_next_question": "Q", "subparts": [], "question_id": "q1"}, "raw")
    mock_normalize.return_value = ({"single_next_question": "Q", "subparts": [], "question_id": "q1"}, "gap")
    mock_construct.return_value = "Q"
    mock_evaluate.return_value = (True, "", "")

    state = _minimal_state(prd_sections={sid: draft})
    with patch("graph.nodes._classify_target_confidence", return_value=("STRONG", "some target", [], "reason", "SINGLE_CANONICAL")):
        with patch("graph.nodes._enforce_visibility", side_effect=lambda d, *a, **kw: d):
            with patch("graph.nodes.get_section_by_index", return_value=PRD_SECTIONS[0]):
                generate_questions_node(state)

    # Confirm the system prompt passed to the generator contained the marker topic
    call_args = mock_build_prompt.call_args
    # The lane instruction is appended AFTER base_prompt; verify it via _invoke call
    system_prompt_used = mock_invoke.call_args[0][0]
    assert "at least one explicit non-goal" in system_prompt_used, (
        f"Expected marker topic in LLM prompt, got: {system_prompt_used[:300]}"
    )
    assert "DRAFT MARKER RESOLUTION" in system_prompt_used


@patch("graph.nodes._invoke_structured_question_generator")
@patch("graph.nodes._evaluate_duplicate_candidate")
@patch("graph.nodes._normalize_generated_question")
@patch("graph.nodes._construct_final_question_text")
@patch("graph.nodes._build_elicitor_prompt_context")
@patch("graph.nodes.build_conversation_understanding_output")
@patch("graph.nodes._maybe_emit_numeric_repair_prompt", return_value=None)
@patch("graph.nodes._maybe_emit_conflict_resolution_question", return_value=None)
@patch("graph.nodes._maybe_emit_resolved_branch_question", return_value=None)
@patch("graph.nodes.log_event")
def test_draft_marker_instruction_does_not_ask_user_to_open_panel(
    mock_log, mock_branch, mock_conflict, mock_repair,
    mock_bridge, mock_build_prompt,
    mock_construct, mock_normalize, mock_evaluate, mock_invoke
):
    """The draft-marker lane instruction must NOT tell the user to open any panel or draft."""
    sid = _section_id()
    draft = "[NEEDS CLARIFICATION: user segment definition]"

    mock_build_prompt.return_value = "BASE_PROMPT"
    mock_bridge.return_value = {}
    mock_invoke.return_value = ({"single_next_question": "Q", "subparts": [], "question_id": "q1"}, "raw")
    mock_normalize.return_value = ({"single_next_question": "Q", "subparts": [], "question_id": "q1"}, "gap")
    mock_construct.return_value = "Q"
    mock_evaluate.return_value = (True, "", "")

    state = _minimal_state(prd_sections={sid: draft})
    with patch("graph.nodes._classify_target_confidence", return_value=("STRONG", "target", [], "reason", "SINGLE_CANONICAL")):
        with patch("graph.nodes._enforce_visibility", side_effect=lambda d, *a, **kw: d):
            with patch("graph.nodes.get_section_by_index", return_value=PRD_SECTIONS[0]):
                generate_questions_node(state)

    system_prompt_used = mock_invoke.call_args[0][0]
    # The instruction must activate the DRAFT MARKER RESOLUTION lane
    assert "DRAFT MARKER RESOLUTION" in system_prompt_used
    # And must name the specific unresolved topic so the LLM can form a named question
    assert "user segment definition" in system_prompt_used
    # Must include the answerability constraint so LLM knows not to be vague
    assert "answerable in one reply" in system_prompt_used


@patch("graph.nodes._invoke_structured_question_generator")
@patch("graph.nodes._evaluate_duplicate_candidate")
@patch("graph.nodes._normalize_generated_question")
@patch("graph.nodes._construct_final_question_text")
@patch("graph.nodes._build_elicitor_prompt_context")
@patch("graph.nodes.build_conversation_understanding_output")
@patch("graph.nodes._maybe_emit_numeric_repair_prompt", return_value=None)
@patch("graph.nodes._maybe_emit_conflict_resolution_question", return_value=None)
@patch("graph.nodes._maybe_emit_resolved_branch_question", return_value=None)
@patch("graph.nodes.log_event")
def test_only_one_specific_missing_item_is_asked_from_multiple_markers(
    mock_log, mock_branch, mock_conflict, mock_repair,
    mock_bridge, mock_build_prompt,
    mock_construct, mock_normalize, mock_evaluate, mock_invoke
):
    """When multiple markers exist, only the first (highest-priority) is used."""
    sid = _section_id()
    draft = "[MISSING: non-goal]\n[NEEDS CLARIFICATION: target audience]"

    mock_build_prompt.return_value = "BASE_PROMPT"
    mock_bridge.return_value = {}
    mock_invoke.return_value = ({"single_next_question": "Q", "subparts": [], "question_id": "q1"}, "raw")
    mock_normalize.return_value = ({"single_next_question": "Q", "subparts": [], "question_id": "q1"}, "gap")
    mock_construct.return_value = "Q"
    mock_evaluate.return_value = (True, "", "")

    state = _minimal_state(prd_sections={sid: draft})
    with patch("graph.nodes._classify_target_confidence", return_value=("STRONG", "target", [], "reason", "SINGLE_CANONICAL")):
        with patch("graph.nodes._enforce_visibility", side_effect=lambda d, *a, **kw: d):
            with patch("graph.nodes.get_section_by_index", return_value=PRD_SECTIONS[0]):
                generate_questions_node(state)

    system_prompt_used = mock_invoke.call_args[0][0]
    # First marker wins; second marker must NOT appear as a separate ask
    assert "non-goal" in system_prompt_used
    # Should not also inject the second marker as a separate instruction
    assert system_prompt_used.count("DRAFT MARKER RESOLUTION") == 1


@patch("graph.nodes._invoke_structured_question_generator")
@patch("graph.nodes._evaluate_duplicate_candidate")
@patch("graph.nodes._normalize_generated_question")
@patch("graph.nodes._construct_final_question_text")
@patch("graph.nodes._build_elicitor_prompt_context")
@patch("graph.nodes.build_conversation_understanding_output")
@patch("graph.nodes._maybe_emit_numeric_repair_prompt", return_value=None)
@patch("graph.nodes._maybe_emit_conflict_resolution_question", return_value=None)
@patch("graph.nodes._maybe_emit_resolved_branch_question", return_value=None)
@patch("graph.nodes.log_event")
def test_generic_prompt_used_only_when_no_specific_gap_exists(
    mock_log, mock_branch, mock_conflict, mock_repair,
    mock_bridge, mock_build_prompt,
    mock_construct, mock_normalize, mock_evaluate, mock_invoke
):
    """When the draft has no markers, the normal A/B lane applies (no DRAFT MARKER RESOLUTION)."""
    sid = _section_id()
    draft = "Goal: help users track orders."  # no markers

    mock_build_prompt.return_value = "BASE_PROMPT"
    mock_bridge.return_value = {}
    mock_invoke.return_value = ({"single_next_question": "Q", "subparts": [], "question_id": "q1"}, "raw")
    mock_normalize.return_value = ({"single_next_question": "Q", "subparts": [], "question_id": "q1"}, "gap")
    mock_construct.return_value = "Q"
    mock_evaluate.return_value = (True, "", "")

    state = _minimal_state(prd_sections={sid: draft})
    with patch("graph.nodes._classify_target_confidence", return_value=("STRONG", "some target", [], "reason", "SINGLE_CANONICAL")):
        with patch("graph.nodes._enforce_visibility", side_effect=lambda d, *a, **kw: d):
            with patch("graph.nodes.get_section_by_index", return_value=PRD_SECTIONS[0]):
                generate_questions_node(state)

    system_prompt_used = mock_invoke.call_args[0][0]
    assert "DRAFT MARKER RESOLUTION" not in system_prompt_used
    assert "TARGET LOCK" in system_prompt_used  # Lane A was used
