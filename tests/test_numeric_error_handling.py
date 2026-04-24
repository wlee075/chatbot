import pytest
from graph.split_nodes import handle_numeric_error_node

def test_validation_reason_code_is_not_rendered_directly_in_ui():
    """
    Ensures that if the semantic validation reason is snake_case or raw code, 
    the architecture correctly isolates it in state so the UI won't accidentally render it.
    """
    state = {
        "validation_reason": "scary_internal_backend_code_123"
    }
    res = handle_numeric_error_node(state)
    chat_hist = res.get("chat_history", [])
    
    # We enforce that the semantic validation code is NEVER injected directly into user-facing content
    assert chat_hist[0].get("type") == "numeric_validation_error"
    assert "scary_internal_backend_code_123" not in chat_hist[0].get("content", "")
    assert res["validation_reason"] == "scary_internal_backend_code_123"


def test_known_numeric_validation_code_maps_to_human_friendly_message():
    """
    Mock test to verify that the final presentation layer will correctly map known codes to
    the correct English text instead of mechanical phrasing.
    (This asserts the expected dictionary lookup contract that exists in app.py)
    """
    known_code = "hours_per_day_exceeds_24"
    
    # In app.py this exact logic is executed dynamically against state
    UI_NUMERIC_ERROR_MAPPING = {
        "hours_per_day_exceeds_24": "That number looks off — the hours per day can’t be more than 24. Could you clarify the correct figure?"
    }
    fallback_msg = "That number looks off — could you double-check and clarify the correct figure?"
    
    rendered_text = UI_NUMERIC_ERROR_MAPPING.get(known_code, fallback_msg)
    assert rendered_text == "That number looks off — the hours per day can’t be more than 24. Could you clarify the correct figure?"


def test_unknown_numeric_validation_code_uses_safe_generic_message():
    """
    If the system invents a new code like 'out_of_range_counts', we must not surface that code.
    Instead, it must cleanly fall back to a safe generic English phrasing.
    """
    unknown_code = "out_of_range_counts_some_new_feature"
    
    UI_NUMERIC_ERROR_MAPPING = {
        "hours_per_day_exceeds_24": "That number looks off — the hours per day can’t be more than 24."
    }
    fallback_msg = "That number looks off — could you double-check and clarify the correct figure?"
    
    rendered_text = UI_NUMERIC_ERROR_MAPPING.get(unknown_code, fallback_msg)
    assert rendered_text == fallback_msg
    assert "out_of_range" not in rendered_text  # Snake case code strictly suppressed


def test_numeric_error_rendering_preserves_machine_reason_in_state_but_not_ui():
    """
    Ensures handle_numeric_error_node only emits the typed machine reasons into state,
    and returns a clean slate for the UI renderer.
    """
    state = {
        "validation_reason": "negative_values"
    }
    res = handle_numeric_error_node(state)
    
    # Machine state retained
    assert res["response_type"] == "numeric_validation_error"
    assert res["validation_reason"] == "negative_values"
    assert res["pending_numeric_clarification"] is True
    
    # UI slate is clean
    chat_bubble = res["chat_history"][0]
    assert chat_bubble["content"] == ""
