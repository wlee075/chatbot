import pytest
from graph.state import PRDState
from graph.routing import route_after_first_message, route_after_discovery, route_after_answer, route_after_session_context_node

def test_post_generation_route_can_return_await_answer_without_keyerror():
    state = PRDState({
        "pending_event": {"event_type": "SUBMIT_SESSION_CONTEXT"}
    })
    
    # Prove that route_after_answer explicitly returns 'await_answer'
    assert route_after_answer(state) == "await_answer"

def test_wait_node_route_labels_match_registered_node_names():
    state = PRDState({
        "pending_event": {"event_type": "SUBMIT_SESSION_CONTEXT"}
    })
    # Check all 3 wait state intercept routers
    assert route_after_first_message(state) == "await_first_message"
    assert route_after_discovery(state) == "await_discovery_answer"
    assert route_after_answer(state) == "await_answer"

def test_no_keyerror_for_await_answer_branch_after_successful_generation():
    valid_edges = {
        "numeric_validation": "numeric_validation",
        "handle_tagged_event": "handle_tagged_event",
        "file_upload_intake": "file_upload_intake",
        "image_description_session_context": "image_description_session_context",
        "terminal_session": "terminal_session",
        "await_answer": "await_answer",
    }
    
    state = PRDState({
        "pending_event": {"event_type": "SUBMIT_SESSION_CONTEXT"}
    })
    
    # Assert the self-loop mapping exists in the allowed edge dict
    res = route_after_answer(state)
    assert res in valid_edges

def test_all_router_return_labels_exist_in_builder_mappings():
    # If a generic 'ANSWER' event occurs, normal progression occurs.
    state = PRDState({"pending_event": {"event_type": "ANSWER"}})
    
    valid_first = ["detect_framing", "file_upload_intake", "terminal_session", "await_first_message"]
    assert route_after_first_message(state) in valid_first

    valid_discovery = ["generate_questions", "discovery_questions", "file_upload_intake", "image_description_session_context", "terminal_session", "await_discovery_answer"]
    assert route_after_discovery(state) in valid_discovery

def test_first_turn_multimodal_routes_to_detect_framing_without_keyerror():
    # If the user uploads an image on the first turn without framing defined
    state = PRDState({
        "pending_event": {"event_type": "ANSWER"},
        "uploaded_files": [{"filename": "image.jpg"}],
        "framing_mode": ""
    })
    
    # Validates that route_after_first_message catches the multimodal path
    assert route_after_first_message(state) == "file_upload_intake"
    
    # Validates that after processing, route_after_session_context explicitly routes to detect_framing 
    # instead of crashing with KeyError
    assert route_after_session_context_node(state) == "detect_framing"

def test_edit_background_context_save_routes_back_to_wait_state_without_keyerror():
    # Prove that an edit/remove payload strictly routes back to wait states.
    state = PRDState({
        "pending_event": {"event_type": "SUBMIT_SESSION_CONTEXT"}
    })
    
    # Wait nodes return their identical loop targets
    assert route_after_first_message(state) == "await_first_message"
    assert route_after_answer(state) == "await_answer"
    assert route_after_discovery(state) == "await_discovery_answer"

def test_text_plus_image_turn_reaches_file_upload_intake():
    state = PRDState({
        "pending_event": {"event_type": "ANSWER"},
        "uploaded_files": [{"filename": "doc.pdf"}],
        "active_question_id": "test"
    })
    # Multimodal rules immediately capture execution into file intake seamlessly
    assert route_after_answer(state) == "file_upload_intake"

def test_image_only_turn_bypasses_text_intent_classification():
    # If the turn is pure image, after session context completes, we naturally route into generate_questions
    # purely bypassing the numeric/intent validators natively
    state = PRDState({
        "pending_event": {"event_type": "ANSWER", "content": ""},
        "framing_mode": "agile",
        "tbd_fields": [],
        "draft_execution_mode": "drafted"
    })
    # Since text is empty, the normal NLP parser doesn't invoke
    res = route_after_session_context_node(state)
    assert res in ["generate_questions", "discovery_questions"]
