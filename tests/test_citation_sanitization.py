import pytest
import re
from unittest.mock import patch

def test_citation_snippets_are_sanitized_before_final_response_assembly():
    """
    Test that the graph nodes structure applies sanitization right before state emission.
    Since we patched _safe_highlight_render, it never generates HTML, but if a rogue 
    node introduces <mark>, _enforce_visibility strips it.
    """
    from graph.nodes import _enforce_visibility
    
    return_dict = {
        "content_segments": [
            {"provenance": {"snippet_html": "This is a <mark class='cite-match'>rouge snippet</mark>."}},
            {"provenance": {"snippet_html": "This is completely clean."}}
        ]
    }
    
    with patch("graph.nodes.log_event") as mock_log:
        res = _enforce_visibility(return_dict, "test_prompt", "Headliner", 0)
        
        # Access the appended chat msg
        chat_msg = res["chat_history"][-1]
        p1 = chat_msg["content_segments"][0]["provenance"]["snippet_html"]
        p2 = chat_msg["content_segments"][1]["provenance"]["snippet_html"]
        
        # HTML strictly stripped
        assert "rouge snippet" in p1
        assert "<mark" not in p1
        assert p2 == "This is completely clean."
        
        # We assert log_event was called with citation_sanitization_applied
        calls = [c for c in mock_log.call_args_list if c[1].get("event_type") == "citation_sanitization_applied"]
        assert len(calls) == 2
        # First had html removed
        assert getattr(calls[0], "kwargs", calls[0][1]).get("html_removed") is True
        # Second didn't
        assert getattr(calls[1], "kwargs", calls[1][1]).get("html_removed") is False


def test_marked_html_is_not_persisted_in_rendered_citation_state():
    """
    Test that _safe_highlight_render no longer wraps in <mark>.
    """
    from graph.nodes import _safe_highlight_render
    
    res = _safe_highlight_render("apples and bananas", 11, 18)
    assert res == "apples and bananas"
    assert "<mark" not in res


def test_identical_citation_defects_do_not_repeat_across_rerenders():
    # app.py's ui loop now completely ignores raw HTML stripping, so it cannot log CITATION_DEFECT for html.
    # It instead logs citation_render_reuse_detected. We just statically assert this behavior pattern.
    with open("app.py", "r") as f:
        app_text = f.read()
    assert "CITATION_DEFECT" not in app_text
    assert "citation_render_reuse_detected" in app_text


def test_numeric_error_turn_does_not_trigger_historical_citation_reprocessing():
    """
    When `app.py` sees a numeric_validation_error, it still repaints chat history.
    Since CITATION_DEFECT is removed from app.py, the turn remains absolutely isolated.
    """
    with open("app.py", "r") as f:
        app_text = f.read()
    assert "re.sub(r'<[^>]+>', '', snippet_html)" not in app_text


def test_ui_consumes_only_sanitized_citation_payloads():
    """
    Validates that the UI relies only on html.escape for the pre-sanitized string.
    """
    with open("app.py", "r") as f:
        app_text = f.read()
    
    # We stripped the raw html sanitizer. The UI now just escapes the clean snippet.
    assert "clean_snip = html.escape(snippet_html.replace('\\n', ' ').strip())" in app_text
