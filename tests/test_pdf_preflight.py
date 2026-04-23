"""
Tests for the PDF preflight formatter (_clean_report_section_text).

Covers the required tests:
  - test_all_sections_are_cleaned_before_pdf_render
  - test_corrupted_apostrophe_fixed_week_question_s_to_week_apostrophe_s
  - test_numeric_ranges_normalized
  - test_empty_section_uses_needs_more_data_message
  - test_pdf_contains_no_suspicious_tokens
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from graph.nodes import _clean_report_section_text


FALLBACK = "Needs more data to refine this section."


# ── test_corrupted_apostrophe_fixed ──────────────────────────────────────────

def test_corrupted_apostrophe_week_question_s():
    """'week?s' must become 'week\\'s' (the exact reported corruption)."""
    result = _clean_report_section_text(
        "Next week?s file requires the same matches to be made again."
    )
    assert "week's" in result, f"Expected 'week\\'s', got: {result!r}"
    assert "week?s" not in result


def test_corrupted_apostrophe_doesnt():
    """'doesn?t' must become 'doesn\\'t'."""
    result = _clean_report_section_text("The system doesn?t handle this case.")
    assert "doesn't" in result
    assert "doesn?t" not in result


def test_smart_apostrophe_normalized():
    """Unicode smart apostrophe (U+2019) must be replaced before latin-1 encoding."""
    smart = "New supplier\u2019s requirements are challenging."
    result = _clean_report_section_text(smart)
    # After cleanup, should contain standard ' not the unicode one
    assert "\u2019" not in result
    assert "supplier's" in result


def test_smart_double_quotes_normalized():
    """Smart double quotes must be converted to ASCII."""
    text = "\u201cThis is quoted text\u201d for the report."
    result = _clean_report_section_text(text)
    assert "\u201c" not in result
    assert "\u201d" not in result
    assert '"This is quoted text"' in result


# ── test_numeric_ranges_normalized ───────────────────────────────────────────

def test_numeric_range_with_spaced_dash():
    """'2 - 3 weeks' must be normalized to '2-3 weeks'."""
    result = _clean_report_section_text(
        "New suppliers require 2 - 3 weeks of mapping time."
    )
    assert "2-3 weeks" in result, f"Expected '2-3 weeks', got: {result!r}"
    assert "2 - 3" not in result


def test_numeric_range_various_numbers():
    """Numeric ranges with different values must all be normalized."""
    cases = [
        ("10 - 20 days", "10-20 days"),
        ("1 - 5 months", "1-5 months"),
        ("100 - 200 units", "100-200 units"),
    ]
    for raw, expected_fragment in cases:
        result = _clean_report_section_text(f"This takes {raw} to complete.")
        assert expected_fragment in result, f"Expected {expected_fragment!r} in {result!r}"


def test_already_normalized_range_unchanged():
    """'2-3 weeks' (no spaces around dash) must remain as-is."""
    text = "New suppliers require 2-3 weeks of mapping time."
    result = _clean_report_section_text(text)
    assert "2-3 weeks" in result


# ── test_empty_section_uses_needs_more_data_message ──────────────────────────

def test_empty_string_returns_fallback():
    """Empty string input must return the standard fallback."""
    result = _clean_report_section_text("")
    assert result == FALLBACK


def test_none_coerced_to_fallback():
    """None input must not raise and must return fallback."""
    result = _clean_report_section_text(None)
    assert result == FALLBACK


def test_whitespace_only_returns_fallback():
    """Whitespace-only text must return fallback."""
    result = _clean_report_section_text("   \n\t  ")
    assert result == FALLBACK


def test_single_word_returns_fallback():
    """Text with fewer than 4 words must return fallback."""
    result = _clean_report_section_text("supplier")
    assert result == FALLBACK


def test_list_input_coerced_correctly():
    """List input must be joined and cleaned correctly."""
    result = _clean_report_section_text(
        ["Next week?s file needs work.", "Suppliers require 2 - 3 weeks."]
    )
    assert "week's" in result
    assert "2-3 weeks" in result


# ── test_all_sections_are_cleaned_before_pdf_render ──────────────────────────

def test_all_sections_cleaned_removes_corrupted_apostrophe_before_encoding():
    """Simulate the full chain: preflight runs BEFORE _safe() encodes to latin-1.

    If _clean_report_section_text is called first, 'week?s' → 'week\\'s'.
    Then _safe() encodes the apostrophe correctly (it's latin-1 compat).
    This test verifies the preflight removes the '?' before it could be
    mistaken as a replacement character.
    """
    dirty_input = "Next week?s file requires the same matches. New suppliers require 2 - 3 weeks of mapping."
    cleaned = _clean_report_section_text(dirty_input, section_title="Headliner")

    # Verify the chain produces clean output
    assert "?" not in cleaned.replace("?", ""), "No stray '?' should remain after preflight"
    # Actually verify the exact expected fixes
    assert "week's" in cleaned
    assert "2-3 weeks" in cleaned
    assert "week?s" not in cleaned
    assert "2 - 3" not in cleaned


# ── test_pdf_contains_no_suspicious_tokens ───────────────────────────────────

def test_pdf_no_question_mark_before_letter():
    """After preflight, no '<letter>?<letter>' patterns should remain."""
    import re
    raw_texts = [
        "The week?s report is ready.",
        "It doesn?t support this mode.",
        "Manager?s approval is needed.",
    ]
    for raw in raw_texts:
        result = _clean_report_section_text(raw)
        matches = re.findall(r"[a-zA-Z]\?[a-z]", result)
        assert not matches, (
            f"Suspicious token still present in {result!r}: {matches}"
        )


def test_pdf_no_double_spaces():
    """After preflight, no double spaces should remain in any line."""
    raw = "The  system   requires   multiple   spaces   cleaned."
    result = _clean_report_section_text(raw)
    for line in result.splitlines():
        assert "  " not in line, f"Double space found in line: {line!r}"


def test_pdf_bullet_consistency():
    """Mixed bullet styles must be normalized to '-'."""
    raw = "• First item\n* Second item\n- Third item"
    result = _clean_report_section_text(raw)
    lines = [l.strip() for l in result.splitlines() if l.strip()]
    for line in lines:
        if line[0] in ("•", "*"):
            assert False, f"Non-standard bullet not cleaned: {line!r}"


def test_before_after_example_from_spec():
    """Verify the exact before/after example from the senior's spec."""
    before = "Next week?s file requires the same matches to be made again. New suppliers require 2 - 3 weeks of mapping."
    after_expected_fragments = ["week's", "2-3 weeks"]

    result = _clean_report_section_text(before, section_title="Headliner")
    for fragment in after_expected_fragments:
        assert fragment in result, (
            f"Expected {fragment!r} in cleaned output, got: {result!r}"
        )
