import pytest
from unittest.mock import patch, MagicMock
from graph.state import PRDState
from utils.adjudicator import invoke_llm_adjudicator, AdjudicatorDecision
from graph.nodes import interpret_and_echo_node, generate_questions_node

# --- Unit Tests for the Adjudicator Itself ---

def test_llm_fallback_returns_structured_decision():
    mock_llm = MagicMock()
    mock_response = AdjudicatorDecision(
        task_type="blocker_clearing",
        decision_result=True,
        confidence_score=0.9,
        reason="Clear workflow logic provided",
        recommended_next_blocker_if_any="mapping_logic_missing"
    )
    with patch("utils.adjudicator.llm_invoke", return_value=mock_response):
        decision = invoke_llm_adjudicator(
            task_type="blocker_clearing",
            context_data={"current_blocker": "workflow_sequence", "user_answer": "I do X then Y"},
            llm=mock_llm
        )
        assert decision is not None
        assert decision.decision_result is True
        assert decision.confidence_score == 0.9

def test_low_confidence_adjudicator_result_falls_back_to_safe_clarification():
    mock_llm = MagicMock()
    mock_response = AdjudicatorDecision(
        task_type="blocker_clearing",
        decision_result=True,
        confidence_score=0.5,
        reason="It might be cleared, but I'm unsure."
    )
    with patch("utils.adjudicator.llm_invoke", return_value=mock_response):
        decision = invoke_llm_adjudicator("blocker_clearing", {}, llm=mock_llm)
        assert decision is None

def test_malformed_adjudicator_output_does_not_break_question_flow():
    mock_llm = MagicMock()
    with patch("utils.adjudicator.llm_invoke", return_value="Malformed String"):
        decision = invoke_llm_adjudicator("blocker_clearing", {}, llm=mock_llm)
        assert decision is None

def test_blocker_clear_and_repeat_checks_use_separate_decision_types():
    with patch("utils.adjudicator.llm_invoke") as mock_invoke:
        invoke_llm_adjudicator("semantic_repeat", {"previous_question": "A", "candidate_next_question": "B"}, llm=MagicMock())
        assert "adjudicator_semantic_repeat" in mock_invoke.call_args[1].get("purpose", "")

# --- Integration / Node Level Tests ---
@pytest.fixture(autouse=True)
def mock_integration_services():
    with patch("graph.nodes._get_llm") as mock_llm, patch("graph.nodes._get_nlp") as mock_nlp, patch("graph.nodes._classify_intent_rule") as mock_intent:
        mock_llm.return_value = MagicMock()
        mock_nlp.return_value = MagicMock()
        mock_intent.return_value = ("DIRECT_ANSWER", "mock", "MOCK")
        yield mock_nlp

def test_rule_based_filter_runs_before_llm_fallback(mock_integration_services):
    mock_nlp = mock_integration_services
    mock_nlp.return_value.__iter__.return_value = filter(lambda t: t.pos_ in ("NOUN", "VERB"), [MagicMock(text="Click", pos_="VERB", lemma_="click")])
    
    state = PRDState(
        thread_id="test",
        run_id="test",
        section_index=0,
        remaining_subparts=["workflow_sequence_missing"],
        raw_answer_buffer="I send it.",
        chat_history=[{"role": "user", "content": "I send it.", "semantics": {"action_graph": [{"verb": "send"}]}}]
    )
    
    with patch("utils.adjudicator.invoke_llm_adjudicator") as mock_adj:
        interpret_and_echo_node(state)
        mock_adj.assert_not_called()

def test_llm_fallback_only_invoked_on_ambiguous_cases(mock_integration_services):
    mock_nlp = mock_integration_services
    mock_doc = MagicMock()
    mock_doc.__iter__.return_value = filter(lambda t: t.pos_ in ("NOUN", "VERB"), [])
    mock_nlp.return_value = mock_doc
    
    state = PRDState(
        thread_id="test",
        run_id="test",
        section_index=0,
        remaining_subparts=["workflow_sequence_missing"],
        raw_answer_buffer="well it really depends on the day and how the team feels but usually there is some manual stuff going on before it ends.",
        chat_history=[{"role": "user", "content": "well it really depends on the day and how the team feels but usually there is some manual stuff going on before it ends."}]
    )
    
    with patch("utils.adjudicator.invoke_llm_adjudicator") as mock_adj:
        mock_adj.return_value = AdjudicatorDecision(task_type="blocker_clearing", decision_result=False, confidence_score=0.9, reason="No exact sequence")
        res = interpret_and_echo_node(state)
        mock_adj.assert_called_once()
        assert "workflow_sequence_missing_specific_interaction" in res["remaining_subparts"]

def test_adjudicator_not_called_from_non_ambiguous_clear_case(mock_integration_services):
    state = PRDState(
        thread_id="test",
        run_id="test",
        section_index=0,
        remaining_subparts=["workflow_sequence_missing"],
        raw_answer_buffer="I export.",
        chat_history=[{"role": "user", "content": "I export.", "semantics": {"action_graph": [{"verb": "export"}]}}]
    )
    with patch("utils.adjudicator.invoke_llm_adjudicator") as mock_adj:
        interpret_and_echo_node(state)
        mock_adj.assert_not_called()
