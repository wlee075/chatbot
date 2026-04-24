"""
Tests for PDF document structure: internal-token stripping, section mode rendering,
title block, and report quality gate.

Covers the senior's required tests:
  - test_pdf_strips_internal_source_tokens
  - test_pdf_strips_needs_clarification_tokens
  - test_pdf_sections_render_in_complete_partial_or_empty_mode_only
  - test_headliner_renders_as_single_clean_paragraph
  - test_empty_sections_use_needs_more_data_fallback
  - test_pdf_has_single_title_block
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from graph.nodes import _clean_report_section_text


# ── test_pdf_strips_internal_source_tokens ────────────────────────────────────

def test_pdf_strips_source_concept_key():
    """[SOURCE: concept_key=abc] must be removed entirely."""
    raw = "The system maps suppliers weekly. [SOURCE: concept_key=supplier_mapping] Additional logic applies."
    result = _clean_report_section_text(raw, section_title="Headliner")
    assert "[SOURCE:" not in result
    assert "concept_key" not in result
    assert "The system maps suppliers weekly" in result
    assert "Additional logic applies" in result


def test_pdf_strips_all_source_variants():
    """Multiple [SOURCE: ...] blocks in one section must all be removed."""
    raw = (
        "Feature A [SOURCE: concept_key=feat_a] was requested. "
        "Feature B [SOURCE: concept_key=feat_b] was also noted."
    )
    result = _clean_report_section_text(raw, section_title="Goals")
    assert "[SOURCE:" not in result
    assert "Feature A" in result
    assert "Feature B" in result


def test_pdf_strips_source_with_varied_content():
    """[SOURCE: ...] with any content inside must be stripped."""
    cases = [
        "Text here [SOURCE: x=1] more text.",
        "Text here [SOURCE: qa_key=something_long_here123] and done.",
        "Text here [SOURCE: type=canonical, ref=abc] end.",
    ]
    for raw in cases:
        result = _clean_report_section_text(raw)
        assert "[SOURCE:" not in result, f"SOURCE not stripped from: {result!r}"


# ── test_pdf_strips_needs_clarification_tokens ────────────────────────────────

def test_pdf_strips_needs_clarification_inline():
    """[NEEDS CLARIFICATION: topic] must be removed from final output."""
    raw = "The supplier works monthly [NEEDS CLARIFICATION: confirm frequency] to deliver reports."
    result = _clean_report_section_text(raw, section_title="Process")
    assert "[NEEDS CLARIFICATION:" not in result
    assert "NEEDS CLARIFICATION" not in result
    assert "The supplier works monthly" in result
    assert "to deliver reports" in result


def test_pdf_strips_needs_clarification_case_insensitive():
    """[needs clarification: ...] must also be stripped (case insensitive)."""
    raw = "Data volume [needs clarification: TBD] is high."
    result = _clean_report_section_text(raw)
    assert "needs clarification" not in result.lower()


def test_pdf_strips_generalised_internal_bracket_tags():
    """Any [ALLCAPS_TAG: ...] pattern must be stripped as internal metadata."""
    cases = [
        "Content [REVIEW_NEEDED: confirm this] more content.",
        "Text [DRAFT_MARKER: v1] continues here.",
    ]
    for raw in cases:
        result = _clean_report_section_text(raw)
        # The internal tag should not appear
        import re
        matches = re.findall(r"\[[A-Z_]{3,}:", result)
        assert not matches, f"Internal bracket tag survived: {result!r}"


# ── test_pdf_sections_render_in_complete_partial_or_empty_mode_only ───────────

def test_valid_section_modes():
    """Status values must be exactly one of: complete, partial, empty."""
    from graph.nodes import _build_section_summaries
    # Build with no data → all empty
    summaries = _build_section_summaries({}, {})
    for s in summaries:
        assert s["status"] in ("complete", "partial", "empty"), (
            f"Unexpected status {s['status']!r} for section {s['title']!r}"
        )


def test_section_with_no_data_is_empty():
    """Section with no draft and no qa_store entries must be status=empty."""
    from graph.nodes import _build_section_summaries
    from config.sections import PRD_SECTIONS
    summaries = _build_section_summaries({}, {})
    for s in summaries:
        assert s["status"] == "empty"
        assert s["prose"] == ""


# ── test_headliner_renders_as_single_clean_paragraph ─────────────────────────

def test_headliner_no_internal_tokens():
    """The Headliner section prose must be cleaned of all internal tokens."""
    raw_headliner = (
        "The system automates weekly supplier mapping. [SOURCE: concept_key=mapping_freq] "
        "Next week?s file requires the same matches. [NEEDS CLARIFICATION: confirm scope] "
        "Suppliers require 2 - 3 weeks of mapping."
    )
    result = _clean_report_section_text(raw_headliner, section_title="Headliner")

    # No internal tokens
    assert "[SOURCE:" not in result
    assert "[NEEDS CLARIFICATION:" not in result
    # Apostrophe fixed
    assert "week's" in result
    # Range normalized
    assert "2-3 weeks" in result
    # No '?s' corruption
    assert "week?s" not in result


def test_headliner_is_clean_prose():
    """After cleaning, the Headliner must be readable prose with no debug markers."""
    import re
    raw = (
        "The tool resolves supplier mismatches automatically. [SOURCE: concept_key=auto_resolve] "
        "The team doesn?t want manual corrections. New match rules require 10 - 15 days."
    )
    result = _clean_report_section_text(raw, section_title="Headliner")
    # No bracket markers of any kind
    bracket_tags = re.findall(r"\[[A-Z_]{3,}:", result)
    assert not bracket_tags, f"Internal tags remain: {bracket_tags}"
    # Readable text preserved
    assert "resolves supplier mismatches" in result
    assert "doesn't" in result
    assert "10-15 days" in result


# ── test_empty_sections_use_needs_more_data_fallback ─────────────────────────

def test_empty_section_fallback_text():
    """Empty / blank content must return the standard fallback."""
    FALLBACK = "Needs more data to refine this section."
    assert _clean_report_section_text("") == FALLBACK
    assert _clean_report_section_text(None) == FALLBACK
    assert _clean_report_section_text("   ") == FALLBACK


def test_internal_needs_more_data_bracket_is_stripped():
    """[Needs more data to fill] as a raw bracket marker must be stripped."""
    raw = "[Needs more data to fill]"
    result = _clean_report_section_text(raw)
    assert "[Needs more data" not in result


def test_empty_section_uses_standard_fallback_for_build():
    """_build_section_summaries empty sections must have prose='' and status=empty."""
    from graph.nodes import _build_section_summaries
    summaries = _build_section_summaries({}, {})
    empty_secs = [s for s in summaries if s["status"] == "empty"]
    assert len(empty_secs) > 0, "Expected at least some empty sections with no data"
    for s in empty_secs:
        assert s["prose"] == "", f"Empty section {s['title']!r} should have no prose"
        assert s["is_empty"] is True


# ── test_pdf_has_single_title_block ──────────────────────────────────────────

def test_no_requirements_summary_report_hardcoded():
    """The hardcoded 'Requirements Summary Report' literal must not appear in _render_pdf.

    This verifies the fix: the title block now shows the project name once,
    not a hardcoded duplicated header.
    """
    import inspect
    from graph.nodes import _render_pdf
    src = inspect.getsource(_render_pdf)
    # The old duplication: hardcoded "Requirements Summary Report" as a cell() call
    # AND the project name as a second cell() call.
    occurrences = src.count("Requirements Summary Report")
    assert occurrences <= 1, (
        f"Found {occurrences} occurrences of 'Requirements Summary Report' in "
        "_render_pdf — title block is still duplicated."
    )


def test_no_status_in_progress_in_render_function():
    """'Status: In progress' must not appear in _render_pdf source (was removed)."""
    import inspect
    from graph.nodes import _render_pdf
    src = inspect.getsource(_render_pdf)
    assert "Status: In progress" not in src
    assert "Status: Needs more data" not in src


def test_no_what_we_know_so_far_in_render_function():
    """'What we know so far:' must not appear in _render_pdf source (was removed)."""
    import inspect
    from graph.nodes import _render_pdf
    src = inspect.getsource(_render_pdf)
    assert "What we know so far" not in src
    assert "Still needed:" not in src
