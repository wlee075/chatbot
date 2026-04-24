import pytest
from unittest.mock import patch
from graph.state import PRDState
from graph.nodes import (
    await_first_message_node, 
    await_answer_node, 
    await_discovery_answer_node
)

@pytest.fixture(autouse=True)
def mock_interrupt():
    with patch('graph.nodes.interrupt') as m:
        yield m

def test_await_answer_preserves_uploaded_files_in_returned_state(mock_interrupt):
    payload = {"event_type": "ANSWER", "uploaded_files": [{"filename": "img.png"}]}
    mock_interrupt.return_value = payload
    
    state = PRDState({
        "pending_event": {"event_type": "ANSWER"}
    })
    
    result = await_answer_node(state)
    assert "uploaded_files" in result
    assert result["uploaded_files"] == [{"filename": "img.png"}]

def test_await_discovery_answer_handles_active_and_attached_context_consistently(mock_interrupt):
    payload = {"event_type": "ANSWER", "uploaded_files": [{"filename": "design.pdf"}]}
    mock_interrupt.return_value = payload
    
    state = PRDState({
        "pending_event": {"event_type": "ANSWER"}
    })
    
    result = await_discovery_answer_node(state)
    assert "uploaded_files" in result
    assert result["uploaded_files"] == [{"filename": "design.pdf"}]

def test_wait_nodes_share_consistent_image_context_flag_logic(mock_interrupt):
    payload = {"event_type": "ANSWER", "uploaded_files": [{"filename": "wireframe.jpg"}]}
    mock_interrupt.return_value = payload
    
    state = PRDState({
        "pending_event": {"event_type": "ANSWER"}
    })
    
    r1 = await_first_message_node(state)
    r2 = await_answer_node(state)
    r3 = await_discovery_answer_node(state)
    
    assert r1.get("uploaded_files") == r2.get("uploaded_files") == r3.get("uploaded_files")

def test_first_turn_image_upload_does_not_require_section_index_to_exist(mock_interrupt):
    payload = {"event_type": "ANSWER", "uploaded_files": [{"filename": "img.jpg"}]}
    mock_interrupt.return_value = payload
    
    state = PRDState({
        "pending_event": {"event_type": "ANSWER"},
    })
    
    r1 = await_first_message_node(state)
    assert "uploaded_files" in r1
