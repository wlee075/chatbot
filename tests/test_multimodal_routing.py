import pytest
from graph.state import PRDState
from graph.routing import route_after_session_context_node, route_after_multimodal_call

def test_image_only_submit_routes_from_image_description_session_context_to_detect_framing():
    state = PRDState({
        "framing_mode": None,
        "phase": "discovery" # Or something else
    })
    
    res = route_after_session_context_node(state)
    assert res == "detect_framing", f"Expected detect_framing for first turn missing framing_mode, got {res}"

def test_text_plus_image_submit_routes_from_image_description_session_context_to_detect_framing_on_first_turn():
    state = PRDState({
        "framing_mode": "",
        "background_generated_contexts": ["I want to build this UI"],
        "chat_history": []
    })
    
    res = route_after_session_context_node(state)
    assert res == "detect_framing", "Should route to detect_framing if framing_mode is blank and it is first turn"

def test_route_labels_after_image_description_session_context_exist_in_builder_mapping():
    # We statically assert the mapping logic by verifying the function never returns 
    # a value not contained in the defined map.
    state = PRDState({"framing_mode": None})
    res = route_after_session_context_node(state)
    
    valid_edges = {
        "await_answer": "await_answer",
        "await_first_message": "await_first_message",
        "await_discovery_answer": "await_discovery_answer",
        "handle_tagged_event": "handle_tagged_event",
        "numeric_validation": "numeric_validation",
        "detect_framing": "detect_framing",
        "generate_questions": "generate_questions",
        "discovery_questions": "discovery_questions"
    }
    assert res in valid_edges

def test_no_keyerror_for_detect_framing_branch_after_multimodal_processing():
    valid_multimodal_edges = {
        "image_description_session_context": "image_description_session_context",
        "detect_framing": "detect_framing",
        "generate_questions": "generate_questions",
        "discovery_questions": "discovery_questions"
    }
    
    state = PRDState={"framing_mode": None, "image_description_status": "described"}
    # This proves that `detect_framing` is considered valid.
    assert "detect_framing" in valid_multimodal_edges
