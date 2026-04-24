import pytest

def build_submit_payload(user_input: str, stashed_upload: dict | None, active_ref: dict | None = None) -> dict | None:
    """
    Centralized payload builder enforcing validity explicitly.
    A turn is valid if content is non-empty OR uploaded_files is non-empty.
    """
    user_input = user_input.strip() if user_input else ""
    
    # Invariant: valid if content non-empty OR file attached
    is_valid = bool(user_input) or bool(stashed_upload)
    if not is_valid:
        return None
        
    if active_ref:
        target_content_str = active_ref.get("target_content", "")
        truncated_preview = target_content_str[:100] + ("..." if len(target_content_str) > 100 else "")
        # A tagged UI action uses its bound explicit event_type, otherwise fallback to ANSWER
        payload = {
            "event_type": active_ref.get("event_type", "ANSWER"),
            "content": user_input,
            "target_message_id": active_ref.get("target_message_id", ""),
            "target_content": truncated_preview,
            "source_message_role": active_ref.get("source_message_role", ""),
            "ui_action_label": active_ref.get("label", ""),
        }
    else:
        payload = {"event_type": "ANSWER", "content": user_input}
        
    if stashed_upload:
        payload["uploaded_files"] = [stashed_upload]
        
    return payload

def test_text_only_submit_works():
    payload = build_submit_payload("Hello", None)
    assert payload["event_type"] == "ANSWER"
    assert payload["content"] == "Hello"
    assert "uploaded_files" not in payload

def test_image_only_submit_works():
    mock_file = {"filename": "img.jpg"}
    payload = build_submit_payload("", mock_file)
    assert payload["event_type"] == "ANSWER"
    assert payload["content"] == ""
    assert payload["uploaded_files"] == [mock_file]

def test_text_plus_image_submit_works():
    mock_file = {"filename": "diagram.png"}
    payload = build_submit_payload("Draw this", mock_file)
    assert payload["event_type"] == "ANSWER"
    assert payload["content"] == "Draw this"
    assert payload["uploaded_files"] == [mock_file]

def test_empty_content_with_uploaded_files_is_valid():
    """Verify a turn is treated as valid if content is non-empty OR uploaded_files is non-empty."""
    mock_file = {"filename": "doc.pdf"}
    
    # 1. Invalid: Empty everything
    assert build_submit_payload("", None) is None
    assert build_submit_payload("   ", None) is None
    
    # 2. Valid: File only (content is empty)
    payload = build_submit_payload("", mock_file)
    assert payload is not None
    assert payload["content"] == ""
    assert payload["uploaded_files"] == [mock_file]

def test_image_only_reply_preserves_active_reference():
    """Verify that an image-only submission correctly preserves target message context."""
    mock_file = {"filename": "doc.pdf"}
    active_ref = {
        "event_type": "CORRECT_MESSAGE",
        "target_message_id": "msg_abc123",
        "target_content": "Please verify this.",
        "source_message_role": "assistant"
    }
    
    payload = build_submit_payload("", mock_file, active_ref=active_ref)
    
    assert payload is not None
    assert payload["event_type"] == "CORRECT_MESSAGE"
    assert payload["target_message_id"] == "msg_abc123"
    assert "Please verify this." in payload["target_content"]
    assert payload["uploaded_files"] == [mock_file]
