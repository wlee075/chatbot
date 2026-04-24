import pytest
from graph.nodes import _generate_context_aware_fallback, generate_questions_node
from graph.state import PRDState

def test_generic_more_details_fallback_not_emitted_for_rich_workflow_input(mocker):
    mocker.patch("graph.nodes._get_llm", return_value=mocker.Mock())
    # Mock LLM to return exactly the forbidden generic string
    mock_llm_response = {
        "question_id": "test_id",
        "single_next_question": "Could you provide a few more details on this?",
        "subparts": ["clarification"],
        "user_facing_gap_reason": "",
    }
    mocker.patch("graph.nodes.llm_invoke", return_value=mock_llm_response)
    mocker.patch("graph.nodes.build_conversation_understanding_output", return_value={
        "draft_readiness": {"is_ready": False, "hard_blockers": []},
        "current_concepts": []
    })
    
    state = PRDState(section_index=0, raw_answer_buffer="We clicked the button", thread_id="test", run_id="test")
    result = generate_questions_node(state)
    next_question = result["current_questions"]
    
    # Assert that the generic string was intercepted and coerced to the generic goal string
    assert "Could you provide a few more details on this?" not in next_question
    assert "specific problem are you trying to solve here" in next_question


def test_context_aware_fallback_uses_specific_missing_blocker_for_product_mapping_case():
    state = PRDState(remaining_subparts=["audience_definition"])
    q = _generate_context_aware_fallback(state)
    assert "who this is for" in q
    
    state2 = PRDState(remaining_subparts=["login_workflow"])
    q2 = _generate_context_aware_fallback(state2)
    assert "expected workflow" in q2


def test_generate_questions_never_returns_generic_more_details_string():
    # Setup state with both subpart AND image
    state = PRDState(remaining_subparts=["audience"], pending_event={"uploaded_files": [{"type": "image/png"}]})
    q = _generate_context_aware_fallback(state)
    # The subpart takes priority over screen according to the priority order!
    assert "who this is for" in q
    
    # Setup state with image only
    state_image_only = PRDState(uploaded_files=[{"type": "image/png"}])
    q_screen = _generate_context_aware_fallback(state_image_only)
    assert "I can see the screen" in q_screen


def test_specific_followup_is_single_and_focused_for_high_detail_input():
    state = PRDState() # Empty state
    q = _generate_context_aware_fallback(state)
    assert "specific problem are you trying to solve" in q


def test_context_aware_fallback_is_deterministic():
    state = PRDState(remaining_subparts=["audience"])
    # It must return the exact same string repeatedly
    q1 = _generate_context_aware_fallback(state)
    q2 = _generate_context_aware_fallback(state)
    assert q1 == q2
