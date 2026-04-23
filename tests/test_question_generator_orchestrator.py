import pytest
from unittest.mock import MagicMock, patch

import graph.nodes
graph.nodes.log_event = MagicMock()
graph.nodes.log_suppression_decision = MagicMock()
graph.nodes._get_llm = MagicMock()

from graph.nodes import (
    _maybe_emit_numeric_repair_prompt,
    _maybe_emit_conflict_resolution_question,
    _maybe_emit_resolved_branch_question,
    _build_elicitor_prompt_context,
    _apply_repeat_guard,
    _suppress_resolved_subparts,
    _package_generated_question_result
)

class DummySection:
    def __init__(self, id, title, expected_components=None):
        self.id = id
        self.title = title
        self.description = "Dummy Description"
        self.expected_components = expected_components or []

@patch("graph.nodes._enforce_visibility")
def test_numeric_repair_short_circuit_bypasses_llm(mock_enforce):
    state = {"pending_numeric_clarification": True, "active_question_id": "q1", "section_index": 0}
    ctx = {}
    mock_enforce.return_value = {"current_questions": "numeric repair"}
    
    res = _maybe_emit_numeric_repair_prompt(state, DummySection("1", "Title"), ctx)
    assert res is not None
    assert res["current_questions"] == "numeric repair"
    mock_enforce.assert_called_once()

@patch("graph.nodes._enforce_visibility")
def test_conflict_resolution_short_circuit_bypasses_llm(mock_enforce):
    state = {"section_index": 0}
    bridge_output = {"conflicted_concepts": [{"concept_key": "auth", "surface": "authentication"}]}
    ctx = {}
    mock_enforce.return_value = {"current_questions": "conflict gating"}
    
    res = _maybe_emit_conflict_resolution_question(state, bridge_output, DummySection("1", "Title"), ctx)
    assert res is not None
    assert res["current_questions"] == "conflict gating"
    mock_enforce.assert_called_once()

@patch("graph.nodes._enforce_visibility")
def test_resolved_branch_narrowing_bypasses_llm(mock_enforce):
    state = {"question_status": "ANSWERED", "resolved_option_id": "User Auth", "remaining_subparts": ["metric_measurement"], "section_index": 0}
    ctx = {}
    mock_enforce.return_value = {"current_questions": "branch narrowed"}
    
    res = _maybe_emit_resolved_branch_question(state, DummySection("1", "Title"), ctx)
    assert res is not None
    assert res["current_questions"] == "branch narrowed"
    mock_enforce.assert_called_once()

@patch("graph.nodes._format_prd_so_far")
@patch("graph.nodes._build_visual_context_block")
def test_prompt_builder_outputs_expected_blocks(mock_visual, mock_prd):
    mock_prd.return_value = "PRD SO FAR"
    mock_visual.return_value = "VISUAL CONTEXT"
    
    state = {"iteration": 0, "remaining_subparts": ["workflow"]}
    bridge_output = {"status": "ok"}
    section = DummySection("1", "Title", ["Component 1"])
    
    prompt = _build_elicitor_prompt_context(state, section, bridge_output)
    assert "Title" in prompt
    assert "Component 1" in prompt
    assert "PRD SO FAR" in prompt
    assert "VISUAL CONTEXT" in prompt
    assert "workflow" in prompt

@patch("graph.nodes._get_nlp")
@patch("utils.adjudicator.invoke_llm_adjudicator")
def test_repeat_guard_retries_and_falls_back_correctly(mock_adjudicate, mock_nlp):
    mock_doc = MagicMock()
    mock_token = MagicMock()
    mock_token.lemma_ = "auth"
    mock_token.pos_ = "NOUN"
    mock_token.is_stop = False
    mock_doc.__iter__.return_value = [mock_token]
    mock_nlp.return_value = lambda text: mock_doc

    mock_decision = MagicMock()
    mock_decision.decision_result = True
    mock_decision.reason = "identical"
    mock_adjudicate.return_value = mock_decision
    
    recent = ["What about auth?"]
    response = {"single_next_question": "What about authentication?"}
    
    is_repeat, new_q, metrics = _apply_repeat_guard(response, recent, {}, {"_t0": 0})
    assert is_repeat is True
    assert metrics["reason"] == "identical"

def test_suppress_resolved_subparts_filters_question_targets():
    state = {"confirmed_qa_store": {"fact1": {"section_id": "1", "resolved_subparts": ["roles"]}}}
    response = {"single_next_question": "What happens?", "subparts": ["roles", "workflow"]}
    
    questions, filtered = _suppress_resolved_subparts(response, "", state, DummySection("1", "Title"), {})
    assert "roles" not in filtered
    assert "workflow" in filtered



@patch("graph.nodes._segment_text_with_provenance")
@patch("graph.nodes._enforce_visibility")
def test_package_generated_question_result_builds_expected_visibility_payload(mock_enforce, mock_segment):
    mock_segment.return_value = [{"text": "Hello"}]
    mock_enforce.return_value = {"packaged": True}
    
    response = {"question_id": "q2", "question_type": "OPEN_ENDED"}
    q_obj = {"question_text": "Hello", "subparts": []}
    state = {"section_index": 0}
    
    res = _package_generated_question_result(response, "Hello world", q_obj, state, DummySection("1", "Title"), {}, "System prompt text")
    assert res["packaged"] is True
    mock_enforce.assert_called_once()
