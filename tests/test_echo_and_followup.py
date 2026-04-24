import os
os.environ["GOOGLE_API_KEY"] = "fake_key_for_testing"

import pytest
from graph.nodes import generate_questions_node
from graph.split_nodes import echo_generation_node
from graph.state import PRDState

def test_weak_echo_is_suppressed(mocker):
    # No need to mock intent classifier, we provide state explicitly
    # Simulate a raw answer that is too long / describes a workflow
    raw_answer = "First we extract data from the Outlook email -> then map it in Excel -> then trigger the PDF retrieval system."
    state = PRDState(
        section_index=0,
        raw_answer_buffer=raw_answer,
        current_questions="How does the workflow work?",
        thread_id="test",
        reply_intent="DIRECT_ANSWER"
    )
    result = echo_generation_node(state)
    chat_history = result.get("chat_history", [])
    
    # Assert echo is completely dropped (not returned)
    assert len(chat_history) == 0

def test_echo_when_present_is_clean_and_non_repetitive(mocker):
    # Provide state explicitly
    # Simulate a clean, short answer
    raw_answer = "we use SAP for this."
    state = PRDState(
        section_index=0,
        raw_answer_buffer=raw_answer,
        thread_id="test",
        reply_intent="DIRECT_ANSWER"
    )
    result = echo_generation_node(state)
    chat_history = result.get("chat_history", [])
    
    # Assert echo exists and is clean
    assert len(chat_history) == 1
    echo = chat_history[0]["content"]
    assert echo == "Got it. We use SAP for this."
    assert "Got it — " not in echo

def test_workflow_answer_triggers_specific_followup_not_elaborate(mocker):
    mocker.patch("graph.nodes._get_llm", return_value=mocker.Mock())
    # Mock LLM to return a generic 'Could you elaborate on that?'
    mock_llm_response = {
        "question_id": "test_id",
        "single_next_question": "Could you elaborate on that?",
        "subparts": ["clarification"],
        "user_facing_gap_reason": "",
        "explicit_missing_detail": "",
        "acknowledged_context": ""
    }
    mocker.patch("graph.nodes.llm_invoke", return_value=mock_llm_response)
    mocker.patch("graph.nodes.build_conversation_understanding_output", return_value={
        "draft_readiness": {"is_ready": False, "hard_blockers": []},
        "current_concepts": []
    })
    
    raw_answer = "Outlook email -> raw data extraction -> Excel mapping"
    state = PRDState(
        section_index=0,
        raw_answer_buffer=raw_answer,
        thread_id="test",
        run_id="test"
    )
    result = generate_questions_node(state)
    next_question = result["current_questions"]
    
    assert "elaborate" not in next_question.lower()
    assert "matched" in next_question.lower() or "manual" in next_question.lower()

def test_followup_targets_highest_value_missing_workflow_detail(mocker):
    mocker.patch("graph.nodes._get_llm", return_value=mocker.Mock())
    # Mock generic LLM fallback
    mock_llm_response = {
        "question_id": "test_id",
        "single_next_question": "Could you provide a few more details on this?",
        "subparts": ["clarification"],
        "user_facing_gap_reason": "",
        "explicit_missing_detail": "",
        "acknowledged_context": ""
    }
    mocker.patch("graph.nodes.llm_invoke", return_value=mock_llm_response)
    mocker.patch("graph.nodes.build_conversation_understanding_output", return_value={
        "draft_readiness": {"is_ready": False, "hard_blockers": []},
        "current_concepts": []
    })
    
    # Specific workflow focusing on mapping should trigger the exact field question
    raw_answer = "first we map the vendor invoice using Excel"
    state = PRDState(
        section_index=0,
        raw_answer_buffer=raw_answer,
        thread_id="test",
        run_id="test"
    )
    result = generate_questions_node(state)
    next_question = result["current_questions"]
    
    assert "what exactly is being matched" in next_question.lower()
