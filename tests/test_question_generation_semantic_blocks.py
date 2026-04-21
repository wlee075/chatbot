import pytest
from unittest.mock import patch, MagicMock
from graph.state import PRDState, ConceptStatus
from graph.nodes import generate_questions_node
from graph.split_nodes import blocker_transition_node, semantic_assessor_node

@pytest.fixture(autouse=True)
def mock_llm_and_nlp():
    with patch("graph.nodes._get_llm") as mock_llm, patch("graph.nodes._get_nlp") as mock_nlp:
        mock_llm.return_value = MagicMock()
        mock_nlp.return_value = MagicMock()
        yield

@patch("graph.nodes._classify_intent_rule", return_value=("DIRECT_ANSWER", "mock answer", "FAST_REGEX", None))
def test_generic_workflow_blocker_cleared_after_workflow_answer(mock_intent):
    # Setup state with initial generic workflow blocker
    state = PRDState(
        thread_id="test",
        run_id="test",
        section_index=0,
        remaining_subparts=["workflow_sequence_missing"],
        chat_history=[
            {"role": "user", "msg_id": "u1", "content": "I export from SAP and send an email.", "semantics": {
                "action_graph": [{"verb": "send", "object": "email"}]
            }}
        ],
        recent_questions=["How does the process work?"],
    )
    
    # Process through blocker transition node directly
    state["reply_intent"] = "DIRECT_ANSWER"
    extraction = semantic_assessor_node(state)
    state.update(extraction)
    state["subpart_evidence_candidates"] = ["workflow_sequence_missing"]
    
    updated_state = blocker_transition_node(state)
    state.update(updated_state)
    
    # Workflow sequence should be cleared and replaced by mapping_logic_missing due to 'send'
    assert "workflow_sequence_missing" not in state["remaining_subparts"]
    assert "mapping_logic_missing" in state["remaining_subparts"]

@patch("graph.nodes._classify_intent_rule", return_value=("DIRECT_ANSWER", "mock answer", "FAST_REGEX", None))
def test_workflow_blocker_replaced_by_narrower_mapping_blocker(mock_intent):
    state = PRDState(
        thread_id="test2",
        run_id="test2",
        section_index=0,
        remaining_subparts=["mapping_logic_missing"],
        chat_history=[
            {"role": "user", "content": "we match by product code", "semantics": {
                "candidates": [{"surface": "product code"}]
            }}
        ]
    )
    
    state["reply_intent"] = "DIRECT_ANSWER"
    extraction = semantic_assessor_node(state)
    state.update(extraction)
    state["subpart_evidence_candidates"] = ["mapping_logic_missing"]
    updated_state = blocker_transition_node(state)
    state.update(updated_state)
    assert "mapping_logic_missing" not in state["remaining_subparts"]
    assert "destination_handling_missing" in state["remaining_subparts"]

@patch("graph.nodes._classify_intent_rule", return_value=("DIRECT_ANSWER", "mock answer", "FAST_REGEX", None))
def test_partially_answered_blocker_generates_narrower_same_domain_followup(mock_intent):
    state = PRDState(
        thread_id="test3",
        run_id="test3",
        section_index=0,
        remaining_subparts=["workflow_sequence_missing"],
        chat_history=[
            {"role": "user", "content": "It's complicated, I tell the team to handle it.", "semantics": {
                # no action_graph or mapping mentions
            }}
        ]
    )
    
    state["reply_intent"] = "DIRECT_ANSWER"
    extraction = semantic_assessor_node(state)
    state.update(extraction)
    state["subpart_evidence_candidates"] = ["workflow_sequence_missing"]
    updated_state = blocker_transition_node(state)
    state.update(updated_state)
    # The blocker gets a specific extension for narrower partial followup
    assert "workflow_sequence_missing" not in state["remaining_subparts"]
    assert "workflow_sequence_missing_specific_interaction" in state["remaining_subparts"]

def test_conflicted_concepts_force_conflict_question_before_other_blockers():
    state = PRDState(
        thread_id="test_c1",
        run_id="test_c",
        section_index=0,
        remaining_subparts=["mapping_logic_missing"],
        concept_history={
            "app_target": {"status": ConceptStatus.CONFLICTED, "concept_key": "app_target", "surface": "app target"}
        },
        chat_history=[]
    )
    
    res = generate_questions_node(state)
    # Should short circuit!
    assert "conflict_resolution" in res["remaining_subparts"]
    assert "mixed details about 'app_target'" in res["current_questions"]

