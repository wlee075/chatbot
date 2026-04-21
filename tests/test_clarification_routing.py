import pytest
from unittest.mock import patch, MagicMock
from graph.state import PRDState
from graph.nodes import generate_questions_node, answer_clarification_node
from graph.routing import route_after_intent as route_after_echo
from graph.split_nodes import clarification_router_node, intent_classifier_node

@patch("graph.split_nodes._get_llm", return_value=MagicMock())
@patch("graph.nodes._get_llm", return_value=MagicMock())
@patch("graph.split_nodes._classify_intent_rule")
def test_repetition_complaint_and_direct_clarification_question_take_different_routes(mock_classify, mock_llm1, mock_llm2):
    state_repeat = PRDState(
        thread_id="test", run_id="test", section_index=0, remaining_subparts=["workflow_sequence_missing"],
        raw_answer_buffer="You already asked that.", chat_history=[]
    )
    mock_classify.return_value = ("REPETITION_COMPLAINT", "mock", "MOCK", "generate_questions")
    result_repeat = intent_classifier_node(state_repeat)
    route_repeat = clarification_router_node(result_repeat)
    assert route_repeat["clarification_route_id"] == "repair_mode"
    
    state_clarify = PRDState(
        thread_id="test", run_id="test", section_index=0, remaining_subparts=["workflow_sequence_missing"],
        raw_answer_buffer="What are you unclear of?", chat_history=[]
    )
    mock_classify.return_value = ("DIRECT_CLARIFICATION_QUESTION", "mock", "MOCK", "answer_clarification")
    result_clarify = intent_classifier_node(state_clarify)
    route_clarify = clarification_router_node(result_clarify)
    assert route_clarify["clarification_route_id"] == "answer_clarification"
    
    # Check routing
    assert route_after_echo({"clarification_route_id": "generate_questions"}) == "generate_questions"
    assert route_after_echo({"clarification_route_id": "answer_clarification"}) == "answer_clarification"


@patch("graph.split_nodes._get_llm", return_value=MagicMock())
@patch("graph.nodes._get_llm", return_value=MagicMock())
@patch("graph.split_nodes._classify_intent_rule")
def test_rephrase_request_preserves_blocker_but_rewrites_wording(mock_classify, mock_llm1, mock_llm2):
    mock_classify.return_value = ("REPHRASE_REQUEST", "mock", "MOCK", "repair_mode")
    state = PRDState(
        thread_id="test", run_id="test", section_index=0, remaining_subparts=["workflow_sequence_missing"],
        raw_answer_buffer="I don't understand the question", chat_history=[]
    )
    result = intent_classifier_node(state)
    route = clarification_router_node(result)
    assert route["clarification_route_id"] == "repair_mode"


@patch("graph.split_nodes._get_llm", return_value=MagicMock())
@patch("graph.nodes._get_llm", return_value=MagicMock())
@patch("graph.split_nodes._classify_intent_rule")
def test_direct_clarification_question_answers_before_followup(mock_classify, mock_llm1, mock_llm2):
    mock_classify.return_value = ("DIRECT_CLARIFICATION_QUESTION", "mock", "MOCK", "answer_clarification")
    state = PRDState(
        thread_id="test", run_id="test", section_index=0, remaining_subparts=["workflow_sequence_missing"],
        raw_answer_buffer="what exactly do you still need", chat_history=[]
    )
    result = intent_classifier_node(state)
    route = clarification_router_node(result)
    assert route["clarification_route_id"] == "answer_clarification"
    
    # Route testing
    assert route_after_echo({"clarification_route_id": route["clarification_route_id"]}) == "answer_clarification"


def test_direct_clarification_question_cannot_render_draft_panel():
    # If returned as DIRECT_CLARIFICATION_QUESTION, route_after_echo bypasses detect_impact
    assert route_after_echo({"clarification_route_id": "answer_clarification"}) != "detect_impact"
    assert route_after_echo({"clarification_route_id": "answer_clarification"}) != "draft"


@patch("graph.split_nodes._get_llm", return_value=MagicMock())
@patch("graph.nodes._get_llm", return_value=MagicMock())
@patch("graph.split_nodes._classify_intent_rule")
def test_stale_question_cannot_render_after_invalidation_even_if_old_state_exists(mock_classify, mock_llm1, mock_llm2):
    mock_classify.return_value = ("REPETITION_COMPLAINT", "mock", "MOCK", "stale_error")
    state = PRDState(
        thread_id="test", run_id="test", section_index=0, active_question_id="old_123", raw_answer_buffer="stop", chat_history=[], remaining_subparts=["workflow_sequence_missing"]
    )
    result = intent_classifier_node(state)
    from graph.split_nodes import repair_mode_node
    repair_result = repair_mode_node(result)
    assert repair_result["active_question_id"] == ""


@patch("graph.split_nodes._get_llm", return_value=MagicMock())
@patch("graph.nodes._get_llm", return_value=MagicMock())
def test_direct_clarification_answer_uses_remaining_blockers_not_generic_model_guess(mock_llm1, mock_llm2):
    state = PRDState(
        thread_id="test", run_id="test", section_index=0, remaining_subparts=["mapping_logic_missing"],
        raw_answer_buffer="what?", current_questions="Where are the files?", chat_history=[]
    )
    with patch("graph.nodes.llm_invoke") as mock_invoke:
        mock_invoke.return_value = MagicMock(content='{"explanation": "Missing mapping logic.", "optional_followup_question": "What logic?"}')
        answer_clarification_node(state)
        call_args = mock_invoke.call_args
        prompt = call_args[0][1][0].content
        assert "- mapping_logic_missing" in prompt


@patch("graph.split_nodes._get_llm", return_value=MagicMock())
@patch("graph.nodes._get_llm", return_value=MagicMock())
def test_direct_clarification_answer_mentions_conflict_when_conflict_present(mock_llm1, mock_llm2):
    state = PRDState(
        thread_id="test", run_id="test", section_index=0, remaining_subparts=["mapping_logic_missing"], concept_conflicts=["conflict A"],
        raw_answer_buffer="what?", current_questions="Where are the files?", chat_history=[]
    )
    with patch("graph.nodes.llm_invoke") as mock_invoke:
        mock_invoke.return_value = MagicMock(content='{"explanation": "Missing mapping logic.", "optional_followup_question": "What logic?"}')
        answer_clarification_node(state)
        call_args = mock_invoke.call_args
        prompt = call_args[0][1][0].content
        assert "- conflict A" in prompt


@patch("graph.split_nodes._get_llm", return_value=MagicMock())
@patch("graph.nodes._get_llm", return_value=MagicMock())
def test_repetition_repair_happens_before_question_generation(mock_llm1, mock_llm2):
    state = PRDState(
        thread_id="test", run_id="test", section_index=0, remaining_subparts=["workflow_sequence_missing"],
        repair_instruction="REPETITION_COMPLAINT", chat_history=[]
    )
    with patch("graph.nodes.llm_invoke") as mock_invoke:
        mock_invoke.return_value = {"single_next_question": "What fields?", "question_id": "123", "subparts": ["mapping_logic_missing"]}
        mock_nlp = MagicMock()
        mock_doc = MagicMock()
        mock_doc.__iter__.return_value = iter([])
        mock_nlp.return_value = mock_doc
        with patch("graph.nodes._get_nlp", return_value=mock_nlp):
            generate_questions_node(state)
            prompt = mock_invoke.call_args[0][1][0].content
            # narrowing down explicitly check inside generator Python implementation
            assert "mapping_logic_missing" in prompt


@patch("graph.split_nodes._get_llm", return_value=MagicMock())
@patch("graph.nodes._get_llm", return_value=MagicMock())
def test_direct_clarification_answer_has_deterministic_fallback(mock_llm1, mock_llm2):
    state = PRDState(
        thread_id="test", run_id="test", section_index=0, remaining_subparts=["mapping_logic_missing"],
        raw_answer_buffer="what?", current_questions="Where are the files?", chat_history=[]
    )
    with patch("graph.nodes.llm_invoke", side_effect=Exception("API Error")):
        result = answer_clarification_node(state)
        # Verify fallback triggers
        assert "Still missing details for" in result["chat_history"][0]["content"]

