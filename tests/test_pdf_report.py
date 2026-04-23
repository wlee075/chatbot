"""
Tests for PDF report generation (finalize_node and helpers).

Covers:
  - test_advance_section_synthesizes_section_content_when_current_draft_empty
  - test_finalize_node_returns_non_empty_pdf_bytes_when_all_sections_complete
  - test_pdf_report_contains_title_executive_summary_and_next_steps
  - test_pdf_report_groups_content_by_section
  - test_pdf_report_deduplicates_repeated_section_content_across_multiple_turns
  - test_pdf_report_omits_internal_debug_tokens_and_state_keys
  - test_pdf_generation_failure_falls_back_to_markdown_without_crashing
"""

import sys
import os
import uuid
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config.sections import PRD_SECTIONS
from graph.nodes import (
    _build_section_summaries,
    _build_executive_summary,
    _build_markdown,
    _render_pdf,
    _synthesize_section_draft_from_qa_store,
    finalize_node,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _minimal_state(**overrides):
    """Return the minimal PRDState dict needed by finalize_node and helpers."""
    base = {
        "thread_id": "test-thread",
        "run_id": str(uuid.uuid4()),
        "prd_sections": {},
        "confirmed_qa_store": {},
        "section_index": len(PRD_SECTIONS),  # all sections done
        "is_complete": True,
        # Noise keys that must NOT appear in the PDF prose
        "_prd_sections_fmt_hash": "abc123",
        "_formatted_prd_so_far": "INTERNAL_FORMATTED_PRD",
        "generation_status": "question_generated",
        "clarification_route_id": "ROUTE_X",
    }
    base.update(overrides)
    return base


def _qa_entry(section_id, answer, version=1, contradiction_flagged=False):
    return {
        "fact_id": str(uuid.uuid4()),
        "answer": answer,
        "section": section_id,
        "section_id": section_id,
        "contradiction_flagged": contradiction_flagged,
        "version": version,
    }


# ── Test 1: advance_section synthesizes non-empty draft from qa_store ─────────

def test_advance_section_synthesizes_section_content_when_current_draft_empty():
    """_synthesize_section_draft_from_qa_store returns non-empty text when QA facts exist."""
    sid = PRD_SECTIONS[0].id
    qa_store = {
        "problem_statement": _qa_entry(sid, "Users cannot track their orders in real time."),
        "affected_users":    _qa_entry(sid, "All retail customers who place online orders."),
    }
    result = _synthesize_section_draft_from_qa_store(sid, qa_store)
    assert result.strip(), "Expected non-empty synthesized draft"
    assert "real time" in result.lower() or "online orders" in result.lower()


# ── Test 2: finalize_node returns non-empty PDF bytes ─────────────────────────

def test_finalize_node_returns_non_empty_pdf_bytes_when_all_sections_complete():
    """finalize_node must return non-empty prd_pdf_bytes when sections are populated."""
    sid = PRD_SECTIONS[0].id
    state = _minimal_state(
        prd_sections={sid: "The problem is users cannot track orders."},
    )
    result = finalize_node(state)
    assert "prd_pdf_bytes" in result, "finalize_node must return prd_pdf_bytes"
    assert isinstance(result["prd_pdf_bytes"], bytes)
    assert len(result["prd_pdf_bytes"]) > 0, "PDF bytes must not be empty when render succeeds"


# ── Test 3: PDF contains expected structural sections ─────────────────────────

def test_pdf_report_contains_title_executive_summary_and_next_steps():
    """Rendered PDF bytes must contain key report sections (verified via markdown companion)."""
    sid = PRD_SECTIONS[0].id
    state = _minimal_state(
        prd_sections={sid: "The core problem is order tracking latency."},
    )
    result = finalize_node(state)
    md = result["prd_markdown"]
    assert "Requirements Summary" in md, "Markdown must contain report title"
    assert "Executive Summary" in md, "Markdown must contain Executive Summary"
    assert "Next Steps" in md, "Markdown must contain Next Steps"
    assert result["prd_report_title"], "prd_report_title must be non-empty"
    assert result["prd_generated_at_utc"], "prd_generated_at_utc must be non-empty"


# ── Test 4: PDF groups content by section ─────────────────────────────────────

def test_pdf_report_groups_content_by_section():
    """Each completed section must appear as its own heading in the markdown companion."""
    s0 = PRD_SECTIONS[0]
    s1 = PRD_SECTIONS[1]
    state = _minimal_state(
        prd_sections={
            s0.id: "Problem: users cannot track orders.",
            s1.id: "Solution: real-time order tracking dashboard.",
        },
    )
    result = finalize_node(state)
    md = result["prd_markdown"]
    assert s0.title in md, f"Section '{s0.title}' must appear in markdown"
    assert s1.title in md, f"Section '{s1.title}' must appear in markdown"
    # Check order
    assert md.index(s0.title) < md.index(s1.title), "Sections must appear in canonical order"


# ── Test 5: deduplication across multiple turns ───────────────────────────────

def test_pdf_report_deduplicates_repeated_section_content_across_multiple_turns():
    """_build_section_summaries must deduplicate identical bullet lines from prd_sections and qa_store."""
    sid = PRD_SECTIONS[0].id
    repeated_answer = "Users cannot track their orders in real time."
    # Both prd_sections and qa_store contain the same answer
    prd_sections = {sid: f"- {repeated_answer}\n- {repeated_answer}"}
    qa_store = {
        "problem_v1": _qa_entry(sid, repeated_answer, version=1),
        "problem_v2": _qa_entry(sid, repeated_answer, version=2),
    }
    summaries = _build_section_summaries(prd_sections, qa_store)
    target = next(s for s in summaries if s["id"] == sid)
    prose = target["prose"]
    # Count occurrences of the repeated sentence
    occurrences = prose.lower().count("real time")
    assert occurrences == 1, f"Expected 1 occurrence after dedup, got {occurrences}"


# ── Test 6: no internal debug tokens in prose ─────────────────────────────────

def test_pdf_report_omits_internal_debug_tokens_and_state_keys():
    """The markdown output must not contain internal state keys or routing terms."""
    sid = PRD_SECTIONS[0].id
    state = _minimal_state(
        prd_sections={sid: "The problem is order tracking delays."},
    )
    result = finalize_node(state)
    md = result["prd_markdown"]
    forbidden = [
        "_prd_sections_fmt_hash",
        "_formatted_prd_so_far",
        "generation_status",
        "clarification_route_id",
        "ROUTE_X",
        "INTERNAL_FORMATTED_PRD",
        "question_generated",
    ]
    for token in forbidden:
        assert token not in md, f"Internal token '{token}' must not appear in final report"


# ── Test 7: PDF render failure falls back to markdown ─────────────────────────

def test_pdf_generation_failure_falls_back_to_markdown_without_crashing():
    """If _render_pdf raises, finalize_node must still return non-empty prd_markdown and b'' for bytes."""
    sid = PRD_SECTIONS[0].id
    state = _minimal_state(
        prd_sections={sid: "The problem is order tracking delays."},
    )
    with patch("graph.nodes._render_pdf", return_value=b""):
        result = finalize_node(state)

    assert result["prd_markdown"], "prd_markdown must still be non-empty when PDF render fails"
    assert result["prd_pdf_bytes"] == b"", "prd_pdf_bytes must be b'' when render fails"
    assert "prd_report_title" in result, "prd_report_title must still be set"
    assert "prd_generated_at_utc" in result, "prd_generated_at_utc must still be set"
