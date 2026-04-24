from app import _build_submit_payload

def test_payloads():
    stashed_upload = {"file_id": "1", "filename": "test.png", "mime_type": "image/png", "size_bytes": 100}
    active_ref = {"event_type": "ANSWER", "target_message_id": "msg_12345", "target_content": "Please clarify..."}
    
    print("\n--- Before Fix Equivalent (missing active_ref) ---")
    payload_before = _build_submit_payload(user_input="", stashed_upload=stashed_upload)
    print(payload_before)
    
    print("\n--- After Fix Equivalent (passing active_ref) ---")
    payload_after = _build_submit_payload(user_input="", stashed_upload=stashed_upload, active_ref=active_ref)
    print(payload_after)

if __name__ == "__main__":
    test_payloads()
