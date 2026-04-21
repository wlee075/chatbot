import pytest
import datetime
import uuid
import json
from unittest.mock import patch, MagicMock

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from graph.state import ConceptStatus
from graph.nodes import (
    _sync_concept_history, 
    _build_user_message_dict,
    interpret_and_echo_node,
    build_conversation_understanding_output
)
from config.sections import PRD_SECTIONS

@pytest.fixture
def state():
    return {
        "section_index": 0,
        "chat_history": [],
        "concept_history": {},
        "confirmed_qa_store": {},
        "current_questions": "What do you want to build?",
        "raw_answer_buffer": ""
    }

def simulate_user_message(state_obj, text):
    msg = _build_user_message_dict(text)
    state_obj["chat_history"].append(msg)
    state_obj["raw_answer_buffer"] = text
    semantics = msg.get("semantics", {})
    if semantics:
        updates = _sync_concept_history(state_obj, semantics)
        state_obj["concept_history"].update(updates)
    return msg

def test_future_planned_concept_not_treated_as_current(state):
    # Simulate extraction indicating future tense
    msg = _build_user_message_dict("We plan to add login next year.")
    state["chat_history"].append(msg)
    state["raw_answer_buffer"] = "We plan to add login next year."
    
    # Force a semantics entry indicating future scope
    state["concept_history"]["login"] = {
        "concept_key": "login",
        "surface_forms": ["login"],
        "status": ConceptStatus.MENTIONED.value,
        "is_current": True,  # Might initially be marked true by naive extraction
        "scope": {"type": "timeline", "value": "future_planned"}
    }
    
    bridge = build_conversation_understanding_output(state)
    
    # Should not be in current_concepts, should be in future_or_planned_concepts
    current_keys = [c["concept_key"] for c in bridge["current_concepts"]]
    future_keys = [c["concept_key"] for c in bridge["future_or_planned_concepts"]]
    
    assert "login" not in current_keys
    assert "login" in future_keys
    
def test_commit_truth_blocks_on_conflict(state):
    state["concept_history"]["pdf"] = {
        "concept_key": "pdf",
        "surface_forms": ["PDF"],
        "status": ConceptStatus.CONFLICTED.value,
        "is_current": False,
        "confidence": 0.95
    }
    
    with patch("utils.logger.log_event") as mock_log, patch("graph.nodes._get_llm") as mock_llm:
        mock_llm.return_value = MagicMock()
        result = interpret_and_echo_node(state)
        
    # The commit should return early and offer clarification echo, not pending_concept_updates (the qa store update)
    assert not result.get("pending_concept_updates")
    assert "conflict" in result.get("pending_echo", "").lower()
    
def test_readiness_gate_blocks_drafting_on_conflict(state):
    state["concept_history"]["csv"] = {
        "concept_key": "csv",
        "surface_forms": ["CSV"],
        "status": ConceptStatus.CONFLICTED.value,
        "is_current": False,
        "confidence": 0.9
    }
    bridge = build_conversation_understanding_output(state)
    assert not bridge["draft_readiness"]["is_ready"]
    assert "conflicted_concepts" in bridge["draft_readiness"]["hard_blockers"]

def test_bridge_schema_is_strictly_typed(state):
    # Just verify that the schema is respected and doesn't throw KeyErrors.
    bridge = build_conversation_understanding_output(state)
    assert "current_concepts" in bridge
    assert "conflicted_concepts" in bridge
    assert "future_or_planned_concepts" in bridge
    assert "draft_readiness" in bridge
    
def test_advisory_warning_does_not_block_draft(state):
    # Action graph extraction has missing destination, this is an advisory warning
    state["concept_history"]["send_email"] = {
        "concept_key": "send_email",
        "surface_forms": ["send"],
        "status": ConceptStatus.CURRENT.value,
        "is_current": True,
        "confidence": 0.9,
    }
    msg = _build_user_message_dict("We will send an email.")
    msg["semantics"]["action_graph"] = [{"verb": "send", "object": "email"}]
    state["chat_history"].append(msg)
    
    bridge = build_conversation_understanding_output(state)
    # Draft is ready despite the advisory warning about missing destination
    assert bridge["draft_readiness"]["is_ready"] is True
    assert "send email" in bridge["draft_readiness"]["advisory_warnings"]
