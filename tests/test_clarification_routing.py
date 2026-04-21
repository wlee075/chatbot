import pytest
from unittest.mock import patch, MagicMock
from graph.state import PRDState
from graph.nodes import interpret_and_echo_node, generate_questions_node, answer_clarification_node
from graph.routing import route_after_echo

@patch("graph.nodes._get_llm")
@patch("graph.nodes._classify_intent_rule")
def test_repetition_complaint_and_direct_clarification_question_take_different_routes(mock_classify, mock_llm):
    mock_llm.return_value = MagicMock()
    state_repeat = PRDState(
        thread_id="test", run_id="test", section_index=0, remaining_subparts=["workflow_sequence_missing"],
        raw_answer_buffer="You already asked that.", chat_history=[]
    )
    mock_classify.return_value = ("REPETITION_COMPLAINT", "mock", "MOCK")
    result_repeat = interpret_and_echo_node(state_repeat)
    assert result_repeat["repair_instruction"] == "REPETITION_COMPLAINT"
    
    state_clarify = PRDState(
        thread_id="test", run_id="test", section_index=0, remaining_subparts=["workflow_sequence_missing"],
        raw_answer_buffer="What are you unclear of?", chat_history=[]
    )
    mock_classify.return_value = ("DIRECT_CLARIFICATION_QUESTION", "mock", "MOCK")
    result_clarify = interpret_and_echo_node(state_clarify)
    assert result_clarify["reply_intent"] == "DIRECT_CLARIFICATION_QUESTION"
    
    # Check routing
    assert route_after_echo({"reply_intent": "REPETITION_COMPLAINT"}) == "generate_questions"
    assert route_after_echo({"reply_intent": "DIRECT_CLARIFICATION_QUESTION"}) == "answer_clarification"


@patch("graph.nodes._get_llm")
@patch("graph.nodes._classify_intent_rule")
def test_rephrase_request_preserves_blocker_but_rewrites_wording(mock_classify, mock_llm):
    mock_llm.return_value = MagicMock()
    mock_classify.return_value = ("REPHRASE_REQUEST", "mock", "MOCK")
    state = PRDState(
        thread_id="test", run_id="test", section_index=0, remaining_subparts=["workflow_sequence_missing"],
        raw_answer_buffer="I don't understand the question", chat_history=[]
    )
    result = interpret_and_echo_node(state)
    assert result["repair_instruction"] == "REPHRASE_REQUEST"
    assert "workflow_sequence_missing" in result["remaining_subparts"]


@patch("graph.nodes._get_llm")
@patch("graph.nodes._classify_intent_rule")
def test_direct_clarification_question_answers_before_followup(mock_classify, mock_llm):
    mock_llm.return_value = MagicMock()
    mock_classify.return_value = ("DIRECT_CLARIFICATION_QUESTION", "mock", "MOCK")
    state = PRDState(
        thread_id="test", run_id="test", section_index=0, remaining_subparts=["workflow_sequence_missing"],
        raw_answer_buffer="what exactly do you still need", chat_history=[]
    )
    result = interpret_and_echo_node(state)
    assert result["reply_intent"] == "DIRECT_CLARIFICATION_QUESTION"
    
    # Route testing
    assert route_after_echo(result) == "answer_clarification"


def test_direct_clarification_question_cannot_render_draft_panel():
    # If returned as DIRECT_CLARIFICATION_QUESTION, route_after_echo bypasses detect_impact
    assert route_after_echo({"reply_intent": "DIRECT_CLARIFICATION_QUESTION"}) != "detect_impact"
    assert route_after_echo({"reply_intent": "DIRECT_CLARIFICATION_QUESTION"}) != "draft"


@patch("graph.nodes._get_llm")
@patch("graph.nodes._classify_intent_rule")
def test_stale_question_cannot_render_after_invalidation_even_if_old_state_exists(mock_classify, mock_llm):
    mock_llm.return_value = MagicMock()
    mock_classify.return_value = ("REPETITION_COMPLAINT", "mock", "MOCK")
    state = PRDState(
        thread_id="test", run_id="test", section_index=0, active_question_id="old_123", raw_answer_buffer="stop", chat_history=[], remaining_subparts=["workflow_sequence_missing"]
    )
    result = interpret_and_echo_node(state)
    assert result["active_question_id"] == ""


@patch("graph.nodes._get_llm")
def test_direct_clarification_answer_uses_remaining_blockers_not_generic_model_guess(mock_llm):
    mock_llm.return_value = MagicMock()
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


@patch("graph.nodes._get_llm")
def test_direct_clarification_answer_mentions_conflict_when_conflict_present(mock_llm):
    mock_llm.return_value = MagicMock()
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


@patch("graph.nodes._get_llm")
def test_repetition_repair_happens_before_question_generation(mock_llm):
    mock_llm.return_value = MagicMock()
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


@patch("graph.nodes._get_llm")
def test_direct_clarification_answer_has_deterministic_fallback(mock_llm):
    mock_llm.return_value = MagicMock()
    state = PRDState(
        thread_id="test", run_id="test", section_index=0, remaining_subparts=["mapping_logic_missing"],
        raw_answer_buffer="what?", current_questions="Where are the files?", chat_history=[]
    )
    with patch("graph.nodes.llm_invoke", side_effect=Exception("API Error")):
        result = answer_clarification_node(state)
        # Verify fallback triggers
        assert "Still missing details for" in result["chat_history"][0]["content"]

