"""
Tests for live-snapshot PDF export behavior.

Verifies that PDF generation always reflects the current session state:
  - test_download_pdf_mid_session_contains_latest_completed_sections
  - test_download_pdf_refreshes_timestamp_each_click
  - test_download_pdf_replaces_stale_cached_bytes
  - test_download_pdf_partial_session_shows_mix_of_filled_and_placeholder_sections
"""

import sys
import os
import uuid
import datetime

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config.sections import PRD_SECTIONS
from graph.nodes import _build_section_summaries, _build_executive_summary, _render_pdf


# ── Helpers ───────────────────────────────────────────────────────────────────

def _qa_entry(section_id, answer, version=1):
    return {
        "fact_id": str(uuid.uuid4()),
        "answer": answer,
        "section": section_id,
        "section_id": section_id,
        "contradiction_flagged": False,
        "version": version,
    }


def _render_snapshot(prd_sections: dict, qa_store: dict, ts: str = "2026-04-23T13:00Z") -> bytes:
    """Helper: builds summaries and renders PDF from given state dicts."""
    summaries = _build_section_summaries(prd_sections, qa_store)
    exec_summary = _build_executive_summary(summaries, qa_store)
    return _render_pdf("Requirements Report", ts, exec_summary, summaries)


# ── Test 1: mid-session — newly completed sections appear in PDF ──────────────

def test_download_pdf_mid_session_contains_latest_completed_sections():
    """PDF regenerated from updated session state must include newly completed section content."""
    s0 = PRD_SECTIONS[0]
    s1 = PRD_SECTIONS[1]

    # State before completing s1
    prd_early = {s0.id: "Problem: suppliers use inconsistent file naming."}
    pdf_early = _render_snapshot(prd_early, {})

    # State after completing s1
    prd_later = {
        s0.id: "Problem: suppliers use inconsistent file naming.",
        s1.id: "Solution: unified naming standard dashboard.",
    }
    pdf_later = _render_snapshot(prd_later, {})

    # They must be different (later state produced different bytes)
    assert pdf_early != pdf_later, "Fresh render must produce different bytes when state changes"
    # Both must be valid PDFs
    assert pdf_early.startswith(b"%PDF")
    assert pdf_later.startswith(b"%PDF")


# ── Test 2: timestamp refreshes on each render ────────────────────────────────

def test_download_pdf_refreshes_timestamp_each_click():
    """Each call to _render_pdf with a different timestamp must embed that timestamp.
    (Proxy: different ts args must produce different bytes if the PDF encodes the ts.)"""
    s0 = PRD_SECTIONS[0]
    prd = {s0.id: "Problem: suppliers use inconsistent naming."}

    ts1 = "2026-04-23T05:00Z"
    ts2 = "2026-04-23T13:00Z"

    pdf1 = _render_snapshot(prd, {}, ts=ts1)
    pdf2 = _render_snapshot(prd, {}, ts=ts2)

    # Different timestamps → different PDF bytes
    assert pdf1 != pdf2, "PDF bytes must differ when generated_at timestamp changes"
    assert pdf1.startswith(b"%PDF")
    assert pdf2.startswith(b"%PDF")


# ── Test 3: stale cached bytes are not re-served after state update ───────────

def test_download_pdf_replaces_stale_cached_bytes():
    """The live-render path must never re-use bytes from an earlier render when state has changed."""
    s0 = PRD_SECTIONS[0]

    # Simulate first render (no data)
    stale_bytes = _render_snapshot({}, {})

    # Simulate second render (section now complete)
    prd_updated = {s0.id: "Problem: invoices are processed manually with 3-day delays."}
    fresh_bytes = _render_snapshot(prd_updated, {})

    # Fresh bytes must be different (new data is present)
    assert fresh_bytes != stale_bytes, "Fresh render after state update must differ from stale bytes"
    # Both must be valid PDFs
    assert stale_bytes.startswith(b"%PDF")
    assert fresh_bytes.startswith(b"%PDF")


# ── Test 4: partial session — filled + placeholder mix ───────────────────────

def test_download_pdf_partial_session_shows_mix_of_filled_and_placeholder_sections():
    """A mid-session PDF must show filled content for completed sections and
    is_empty=True (placeholder) for sections not yet answered."""
    s0 = PRD_SECTIONS[0]  # completed
    # All other sections are uncompleted

    prd_partial = {s0.id: "Problem: order tracking is manual and error-prone."}
    summaries = _build_section_summaries(prd_partial, {})

    completed = [s for s in summaries if not s.get("is_empty", True)]
    incomplete = [s for s in summaries if s.get("is_empty", True)]

    assert len(completed) >= 1, "At least the one filled section must be non-empty"
    assert len(incomplete) >= 1, "At least one section must still be marked as placeholder"
    assert completed[0]["id"] == s0.id, f"Filled section must be s0, got {completed[0]['id']}"

    # PDF must render without error
    pdf = _render_snapshot(prd_partial, {})
    assert pdf.startswith(b"%PDF")
    assert len(pdf) > 0
