import pytest
from graph.state import PRDState
from graph.split_nodes import file_upload_intake_node, file_upload_rejection_node
from graph.routing import route_after_answer, route_after_file_intake, route_after_framing, route_after_discovery

def test_no_files_uploaded_rejects():
    state = PRDState(uploaded_files=[])
    res = file_upload_intake_node(state)
    assert res["upload_status"] == "rejected"
    assert not res["downstream_analysis_allowed"]
    assert len(res["rejected_files"]) == 1
    assert res["rejected_files"][0]["reason"] == "no_files_uploaded"

def test_payload_rejection_for_missing_metadata():
    state = PRDState(uploaded_files=[{"filename": "test.jpg"}])  # Missing file_id, etc
    res = file_upload_intake_node(state)
    assert res["upload_status"] == "rejected"
    assert res["rejected_files"][0]["reason"] == "malformed_file_payload"

def test_type_rejection_for_unsupported_files():
    state = PRDState(uploaded_files=[
        {"file_id": "1", "filename": "test.docx", "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "size_bytes": 100}
    ])
    res = file_upload_intake_node(state)
    assert res["upload_status"] == "rejected"
    assert res["rejected_files"][0]["reason"] == "unsupported_file_type"

def test_valid_image_and_pdf_mapping():
    state = PRDState(uploaded_files=[
        {"file_id": "1", "filename": "test1.jpg", "mime_type": "image/jpeg", "size_bytes": 100},
        {"file_id": "2", "filename": "test2.png", "mime_type": "image/png", "size_bytes": 100},
        {"file_id": "3", "filename": "test3.pdf", "mime_type": "application/pdf", "size_bytes": 100}
    ])
    res = file_upload_intake_node(state)
    assert res["upload_status"] == "accepted"
    assert len(res["accepted_files"]) == 3
    assert res["accepted_files"][0]["file_type"] == "jpg"
    assert res["accepted_files"][1]["file_type"] == "png"
    assert res["accepted_files"][2]["file_type"] == "pdf"

def test_upload_order_is_preserved_in_accepted_files():
    state = PRDState(uploaded_files=[
        {"file_id": "A", "filename": "a.png", "mime_type": "image/png", "size_bytes": 1},
        {"file_id": "B", "filename": "b.jpg", "mime_type": "image/jpeg", "size_bytes": 2},
        {"file_id": "C", "filename": "c.pdf", "mime_type": "application/pdf", "size_bytes": 3}
    ])
    res = file_upload_intake_node(state)
    assert [f["file_id"] for f in res["accepted_files"]] == ["A", "B", "C"]

def test_mime_type_is_primary_validation_signal():
    # If extension is weird but mime is correct, it should accept based on MIME maps
    state = PRDState(uploaded_files=[
        {"file_id": "1", "filename": "weird_name_no_ext", "mime_type": "image/jpeg", "size_bytes": 100}
    ])
    res = file_upload_intake_node(state)
    assert res["upload_status"] == "accepted"
    assert res["accepted_files"][0]["file_type"] == "jpg"

def test_accepted_partial_emits_clear_downstream_continuation_decision():
    state = PRDState(uploaded_files=[
        {"file_id": "1", "filename": "good.jpg", "mime_type": "image/jpeg", "size_bytes": 100},
        {"file_id": "2", "filename": "bad.docx", "mime_type": "application/vnd", "size_bytes": 100}
    ])
    res = file_upload_intake_node(state)
    assert res["upload_status"] == "accepted_partial"
    assert res["downstream_analysis_allowed"] is True
    assert len(res["accepted_files"]) == 1
    assert len(res["rejected_files"]) == 1

def test_uploaded_files_block_downstream_analysis_until_intake_completes():
    # Verify post-wait routers intercept the payload unconditionally
    state = PRDState(uploaded_files=[{"file_id": "1", "filename": "test.jpg", "mime_type": "image/jpeg", "size_bytes": 100}])
    assert route_after_answer(state) == "file_upload_intake"
    assert route_after_discovery(state) == "file_upload_intake"
    assert route_after_framing(state) == "file_upload_intake"

def test_rejected_upload_routes_to_file_upload_rejection_node_before_await_answer():
    # After intake runs, if downstream is blocked, ensure it hits rejection node
    state = PRDState(downstream_analysis_allowed=False)
    assert route_after_file_intake(state) == "file_upload_rejection"

def test_file_upload_rejection_node_emits_ui_message():
    state = PRDState(rejected_files=[{"filename": "test.txt", "reason": "unsupported_file_type"}])
    res = file_upload_rejection_node(state)
    assert "chat_history" in res
    assert res["chat_history"][0]["type"] == "file_upload_rejection_error"
    assert res["chat_history"][0]["rejected_files"] == state["rejected_files"]
