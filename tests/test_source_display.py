import pytest
import re
from app import _present_content

def test_raw_source_metadata_not_visible_in_draft():
    # Setup the exact failing payload from the user description
    raw_draft = "This is a drafted note. [SOURCE: concept_key=pm_doing_it, round=background_context]"
    
    # We pass it to the UI presenter to verify it parses and removes the raw token
    result = _present_content(content=raw_draft, source_lookup={}, answer_store={})
    
    # Assert the raw metadata is gone
    assert "[SOURCE:" not in result
    assert "concept_key=" not in result
    assert "round=" not in result
    assert "pm_doing_it" not in result
    assert "background_context" not in result

def test_source_display_is_human_readable():
    raw_draft = "Feature built for PMs. [SOURCE: concept_key=pm_doing_it, round=background_context]"
    
    lookup = {"pm_doing_it": "msg_0"}
    store = {"pm_doing_it": {"answer": "We are building this for the PM doing the manual work."}}
    
    result = _present_content(content=raw_draft, source_lookup=lookup, answer_store=store)
    
    # Assert the tooltip click handler and human readable snippet exist instead of raw data
    assert "We are building this" in result
    assert "pm_doing_it" not in result
    assert "background_context" not in result
    assert "cite-chip" in result

def test_backend_source_metadata_preserved_but_not_rendered():
    # The source reference term replacement should not execute if the SOURCE tag was successfully parsed.
    raw_draft = "Another test. [SOURCE: concept_key=some_key, round=1]"
    result = _present_content(content=raw_draft, source_lookup=None, answer_store=None)
    
    assert "[SOURCE" not in result
    assert "source reference" not in result # Should not leak into fallback string replacement
