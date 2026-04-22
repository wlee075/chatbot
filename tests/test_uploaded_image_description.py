import pytest
from graph.state import PRDState
from graph.split_nodes import uploaded_image_description_node
from graph.routing import route_after_multimodal_call
from unittest.mock import patch, MagicMock

@pytest.fixture(autouse=True)
def mock_multimodal_api():
    class DummyObs:
        high_level_description = "A sketch."
        distinct_visible_elements = ["button", "text"]
        unreadable_or_uncertain_areas = ["maybe desk"]

    with patch('graph.split_nodes._get_llm') as mock_get_llm:
        mock_response = DummyObs()
        mock_get_llm.return_value.with_structured_output.return_value.invoke.return_value = mock_response
        yield mock_get_llm

def test_only_jpg_and_png_are_described():
    state = PRDState(accepted_files=[
        {"file_id": "1", "filename": "test.jpg", "file_type": "jpg"},
        {"file_id": "2", "filename": "test.png", "file_type": "png"}
    ])
    res = uploaded_image_description_node(state)
    assert res["image_description_status"] == "described"
    assert not res["needs_followup"]
    assert len(res["described_images"]) == 2
    assert res["described_images"][0]["file_id"] == "1"
    assert res["described_images"][1]["file_id"] == "2"

def test_accepted_pdfs_are_ignored():
    state = PRDState(accepted_files=[
        {"file_id": "1", "filename": "test.pdf", "file_type": "pdf"}
    ])
    res = uploaded_image_description_node(state)
    assert res["image_description_status"] == "no_accepted_images"
    assert res["needs_followup"] is True
    assert len(res["described_images"]) == 0

def test_rejected_files_are_ignored():
    state = PRDState(accepted_files=[], rejected_files=[
        {"filename": "test.jpg", "reason": "malformed_file_payload"}
    ])
    res = uploaded_image_description_node(state)
    assert res["image_description_status"] == "no_accepted_images"

def test_upload_order_is_preserved():
    state = PRDState(accepted_files=[
        {"file_id": "A", "filename": "a.png", "file_type": "png"},
        {"file_id": "B", "filename": "b.jpg", "file_type": "jpg"},
        {"file_id": "C", "filename": "c.jpg", "file_type": "jpg"}
    ])
    res = uploaded_image_description_node(state)
    assert [img["file_id"] for img in res["described_images"]] == ["A", "B", "C"]

def test_high_level_description_is_provided():
    state = PRDState(accepted_files=[{"file_id": "1", "filename": "test.jpg", "file_type": "jpg"}])
    res = uploaded_image_description_node(state)
    img = res["described_images"][0]
    assert "high_level_description" in img
    assert img["high_level_description"]

def test_visible_elements_are_listed():
    state = PRDState(accepted_files=[{"file_id": "1", "filename": "test.jpg", "file_type": "jpg"}])
    res = uploaded_image_description_node(state)
    img = res["described_images"][0]
    assert "visible_elements" in img
    assert isinstance(img["visible_elements"], list)
    assert len(img["visible_elements"]) > 0

def test_uncertainties_are_explicitly_surfaced():
    state = PRDState(accepted_files=[{"file_id": "1", "filename": "test.jpg", "file_type": "jpg"}])
    res = uploaded_image_description_node(state)
    img = res["described_images"][0]
    assert "uncertainties" in img
    assert isinstance(img["uncertainties"], list)
    assert len(img["uncertainties"]) > 0

def test_no_ocr_claims_are_made():
    # As per our implementation mock:
    state = PRDState(accepted_files=[{"file_id": "1", "filename": "test.jpg", "file_type": "jpg"}])
    res = uploaded_image_description_node(state)
    img = res["described_images"][0]
    assert "claim of transcription" not in img["high_level_description"].lower()

def test_no_business_meaning_is_inferred():
    state = PRDState(accepted_files=[{"file_id": "1", "filename": "test.jpg", "file_type": "jpg"}])
    res = uploaded_image_description_node(state)
    img = res["described_images"][0]
    assert "business" not in img["high_level_description"].lower()

def test_no_followup_is_added_unless_needed():
    state = PRDState(accepted_files=[{"file_id": "1", "filename": "test.jpg", "file_type": "jpg"}])
    res = uploaded_image_description_node(state)
    assert res["needs_followup"] is False

def test_no_accepted_images_returns_no_accepted_images_status():
    state = PRDState(accepted_files=[])
    res = uploaded_image_description_node(state)
    assert res["image_description_status"] == "no_accepted_images"

def test_downstream_receives_only_described_images():
    state = PRDState(accepted_files=[
        {"file_id": "1", "filename": "test.jpg", "file_type": "jpg"},
        {"file_id": "2", "filename": "test.pdf", "file_type": "pdf"}
    ])
    res = uploaded_image_description_node(state)
    assert len(res["described_images"]) == 1
    assert res["described_images"][0]["file_id"] == "1"

def test_downstream_does_not_see_rejected_files():
    state = PRDState(accepted_files=[{"file_id": "1", "filename": "t.jpg", "file_type": "jpg"}], 
                     rejected_files=[{"filename": "t2.jpg", "reason": "empty"}])
    res = uploaded_image_description_node(state)
    assert len(res["described_images"]) == 1

def test_downstream_does_not_see_pdfs():
    state = PRDState(accepted_files=[{"file_id": "1", "filename": "t.pdf", "file_type": "pdf"}])
    res = uploaded_image_description_node(state)
    assert "described_images" in res
    assert len(res["described_images"]) == 0

def test_route_intercept_resume_generates():
    state = PRDState(phase="elicitation")
    assert route_after_multimodal_call(state) == "detect_framing"
    state2 = PRDState(phase="elicitation", framing_mode="chat", pending_event={"event_type": "TAG_MESSAGE_AS_TRUTH"})
    assert route_after_multimodal_call(state2) == "handle_tagged_event"
    
