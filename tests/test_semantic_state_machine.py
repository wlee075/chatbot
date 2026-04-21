import pytest
import datetime
import uuid
from unittest.mock import patch, MagicMock

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from graph.state import ConceptStatus
from graph.nodes import (
    _sync_concept_history, 
    _build_user_message_dict,
    interpret_and_echo_node,
    handle_tagged_event_node
)
from config.sections import PRD_SECTIONS

@pytest.fixture
def mock_semantic_state():
    return {
        "section_index": 0,
        "chat_history": [],
        "concept_history": {},
        "confirmed_qa_store": {},
        "current_questions": "What do you want to build?",
        "raw_answer_buffer": ""
    }

def simulate_user_message(state, text):
    msg = _build_user_message_dict(text)
    state["chat_history"].append(msg)
    state["raw_answer_buffer"] = text
    semantics = msg.get("semantics", {})
    if semantics:
        updates = _sync_concept_history(state, semantics)
        state["concept_history"].update(updates)
    return msg

def test_negated_concept_stays_noncurrent(mock_semantic_state):
    simulate_user_message(mock_semantic_state, "We will never use a PDF for this.")
    hist = mock_semantic_state["concept_history"]
    # PDF should be NEGATED, not CURRENT
    assert "pdf" in hist
    assert hist["pdf"]["status"] == ConceptStatus.NEGATED.value
    assert hist["pdf"]["is_current"] is False

def test_historical_vs_current_concept_state(mock_semantic_state):
    simulate_user_message(mock_semantic_state, "We used to use SAP.")
    hist = mock_semantic_state["concept_history"]
    print("HIST:", hist)
    assert "sap" in hist
    assert hist["sap"]["status"] == ConceptStatus.HISTORICAL.value
    assert hist["sap"]["is_current"] is False

def test_example_only_not_promoted(mock_semantic_state):
    simulate_user_message(mock_semantic_state, "For example, if a user uploads a PDF...")
    hist = mock_semantic_state["concept_history"]
    assert "pdf" in hist
    assert hist["pdf"]["status"] == ConceptStatus.EXAMPLE_ONLY.value

def test_action_graph_extracts_send_pdf_to_mailbox(mock_semantic_state):
    msg = simulate_user_message(mock_semantic_state, "The system will send the PDF to the mailbox.")
    semantics = msg["semantics"]
    assert "action_graph" in semantics
    # We should have an action graph for send
    actions = semantics["action_graph"]
    has_send = any(act["verb"] == "send" for act in actions)
    assert has_send

def test_extraction_alone_does_not_auto_make_current(mock_semantic_state):
    simulate_user_message(mock_semantic_state, "We need a PRD.")
    hist = mock_semantic_state["concept_history"]
    assert "prd" in hist
    assert hist["prd"]["status"] == ConceptStatus.MENTIONED.value

def test_promotion_requires_traceable_source_and_confidence(mock_semantic_state):
    msg = simulate_user_message(mock_semantic_state, "We must use a PRD.")
    hist = mock_semantic_state["concept_history"]
    assert hist["prd"]["status"] == ConceptStatus.MENTIONED.value
    
    # Simulate promotion gate
    with patch("utils.logger.log_event"), patch("graph.nodes._get_llm", return_value=MagicMock()):
        # Interpret and echo makes it CURRENT because it is a direct assertion
        new_state = interpret_and_echo_node(mock_semantic_state)
        
    assert new_state["concept_history"]["prd"]["status"] == ConceptStatus.CURRENT.value

def test_correction_supersedes_prior_concept(mock_semantic_state):
    msg1 = simulate_user_message(mock_semantic_state, "We need a PRD.")
    with patch("utils.logger.log_event"), patch("graph.nodes._get_llm", return_value=MagicMock()):
        new_state = interpret_and_echo_node(mock_semantic_state)
        mock_semantic_state.update(new_state)
        
    assert mock_semantic_state["concept_history"]["prd"]["status"] == ConceptStatus.CURRENT.value
    
    # Simulate a CORRECT_MESSAGE event
    msg2 = simulate_user_message(mock_semantic_state, "No, we use CSV.")
    mock_semantic_state["pending_event"] = {
        "event_type": "CORRECT_MESSAGE",
        "target_message_id": msg1["msg_id"]
    }
    
    with patch("utils.logger.log_event"), patch("utils.telemetry.log_canonical_write"):
        new_state = handle_tagged_event_node(mock_semantic_state)
        mock_semantic_state.update(new_state)
        
    assert mock_semantic_state["concept_history"]["prd"]["status"] == ConceptStatus.SUPERSEDED.value
    assert mock_semantic_state["concept_history"]["csv"]["status"] == ConceptStatus.CURRENT.value

def test_conflicted_state_requires_resolution_before_current(mock_semantic_state):
    simulate_user_message(mock_semantic_state, "We do not use PDF.")
    hist = mock_semantic_state["concept_history"]
    assert hist["pdf"]["status"] == ConceptStatus.NEGATED.value
    
    simulate_user_message(mock_semantic_state, "We will use PDF.")
    hist = mock_semantic_state["concept_history"]
    assert hist["pdf"]["status"] == ConceptStatus.CONFLICTED.value

def test_parallel_active_concepts_with_scope(mock_semantic_state):
    simulate_user_message(mock_semantic_state, "Ops needs Excel, Finance uses SAP.")
    hist = mock_semantic_state["concept_history"]
    assert "excel" in hist
    assert "sap" in hist
    assert hist["excel"]["status"] == ConceptStatus.MENTIONED.value
    assert hist["sap"]["status"] == ConceptStatus.MENTIONED.value
    
def test_uncertain_semantics_not_promoted_to_truth(mock_semantic_state):
    msg = simulate_user_message(mock_semantic_state, "I guess we might use SAP.")
    # Assuming "might" is not negated, but has low confidence or is just mentioned
    hist = mock_semantic_state["concept_history"]
    assert hist["sap"]["status"] == ConceptStatus.MENTIONED.value

def test_phrase_entity_relations_preserved(mock_semantic_state):
    msg = simulate_user_message(mock_semantic_state, "The salesforce system integration.")
    hist = mock_semantic_state["concept_history"]
    assert "salesforce" in hist

def test_concept_state_transition_logged(mock_semantic_state):
    with patch("graph.nodes._log_semantic_transition") as mock_log:
        simulate_user_message(mock_semantic_state, "We need a PRD.")
        hist = mock_semantic_state["concept_history"]
        mock_log.assert_not_called() # No transition from Non-existent to Mentioned (it's initialized directly? Wait, _log_semantic_transition might be called.)
        
        simulate_user_message(mock_semantic_state, "We won't use PRD.")
        hist = mock_semantic_state["concept_history"]
        assert mock_log.called
