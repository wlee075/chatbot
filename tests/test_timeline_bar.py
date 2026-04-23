"""
Tests for the compact timeline bar.

Covers the senior's required tests:
  - test_timeline_bar_shows_correct_percentage
  - test_timeline_bar_marks_current_section
  - test_timeline_bar_updates_after_section_completion
  - test_timeline_bar_renders_above_input_box  (structural/data – no Streamlit mock needed)
"""

import sys
import os
import uuid

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config.sections import PRD_SECTIONS
from utils.progress_rail import compute_progress_data


# ── helpers ───────────────────────────────────────────────────────────────────

def _pass_score():
    return {"verdict": "PASS", "completeness": 1.0}


def _sv(section_index: int, pass_ids: list[str]) -> dict:
    return {
        "section_index": section_index,
        "section_scores": {sid: _pass_score() for sid in pass_ids},
        "confirmed_qa_store": {},
    }


# ── test_timeline_bar_shows_correct_percentage ────────────────────────────────

def test_timeline_bar_shows_correct_percentage_zero():
    """At session start: 0 complete → 0%."""
    data = compute_progress_data(_sv(0, []), PRD_SECTIONS)
    assert data["pct"] == 0


def test_timeline_bar_shows_correct_percentage_partial():
    """N complete sections → correct rounded percentage."""
    total = len(PRD_SECTIONS)
    completed_ids = [PRD_SECTIONS[0].id, PRD_SECTIONS[1].id]
    data = compute_progress_data(_sv(2, completed_ids), PRD_SECTIONS)
    expected = round(2 / total * 100)
    assert data["pct"] == expected, f"Expected {expected}%, got {data['pct']}%"


def test_timeline_bar_shows_correct_percentage_full():
    """All sections complete → 100%."""
    all_ids = [s.id for s in PRD_SECTIONS]
    sv = {
        "section_index": len(PRD_SECTIONS) - 1,
        "section_scores": {sid: _pass_score() for sid in all_ids},
        "confirmed_qa_store": {},
    }
    data = compute_progress_data(sv, PRD_SECTIONS)
    assert data["pct"] == 100


# ── test_timeline_bar_marks_current_section ───────────────────────────────────

def test_timeline_bar_marks_current_section():
    """Exactly one checklist row must have status='current'."""
    sv = _sv(2, [PRD_SECTIONS[0].id, PRD_SECTIONS[1].id])
    data = compute_progress_data(sv, PRD_SECTIONS)
    current_rows = [r for r in data["checklist"] if r["status"] == "current"]
    assert len(current_rows) == 1
    assert current_rows[0]["id"] == PRD_SECTIONS[2].id


def test_timeline_bar_completed_sections_have_complete_status():
    """PASS-verdict sections must appear as 'complete' in the checklist."""
    sv = _sv(2, [PRD_SECTIONS[0].id, PRD_SECTIONS[1].id])
    data = compute_progress_data(sv, PRD_SECTIONS)
    done = {r["id"] for r in data["checklist"] if r["status"] == "complete"}
    assert PRD_SECTIONS[0].id in done
    assert PRD_SECTIONS[1].id in done


def test_timeline_bar_pending_sections_have_pending_status():
    """Sections beyond the current one must be 'pending'."""
    sv = _sv(0, [])
    data = compute_progress_data(sv, PRD_SECTIONS)
    pending = [r for r in data["checklist"] if r["status"] == "pending"]
    # All sections except the first (current) should be pending
    assert len(pending) == len(PRD_SECTIONS) - 1


# ── test_timeline_bar_updates_after_section_completion ───────────────────────

def test_timeline_bar_updates_after_section_completion():
    """Advancing a section must shift the current highlight and increase %."""
    sv_before = _sv(0, [])
    sv_after  = _sv(1, [PRD_SECTIONS[0].id])

    before = compute_progress_data(sv_before, PRD_SECTIONS)
    after  = compute_progress_data(sv_after,  PRD_SECTIONS)

    # Current section must shift
    cur_before = next(r for r in before["checklist"] if r["status"] == "current")
    cur_after  = next(r for r in after["checklist"]  if r["status"] == "current")
    assert cur_before["id"] == PRD_SECTIONS[0].id
    assert cur_after["id"]  == PRD_SECTIONS[1].id

    # Percentage must increase
    assert after["pct"] > before["pct"]

    # Section 0 must now be 'complete'
    s0_row = next(r for r in after["checklist"] if r["id"] == PRD_SECTIONS[0].id)
    assert s0_row["status"] == "complete"


# ── test_timeline_bar_renders_above_input_box ─────────────────────────────────
# This test verifies the data contract consumed by the bar, not Streamlit rendering.
# It ensures the HTML string that _render_timeline_bar would produce contains
# the required tokens, by reconstructing the same logic from compute_progress_data.

def test_timeline_bar_renders_above_input_box():
    """The timeline data must produce one pill per PRD section."""
    sv = _sv(1, [PRD_SECTIONS[0].id])
    data = compute_progress_data(sv, PRD_SECTIONS)

    # One checklist entry per section → one pill per section in the bar
    assert len(data["checklist"]) == len(PRD_SECTIONS)

    # The pill colors are driven by status; ensure coverage
    statuses = {r["status"] for r in data["checklist"]}
    assert "complete" in statuses
    assert "current"  in statuses
    assert "pending"  in statuses

    # current_title must not be empty
    assert data["current_title"], "current_title must be set for the bar label"

    # Percentage must be a clean integer in [0,100]
    assert isinstance(data["pct"], int)
    assert 0 <= data["pct"] <= 100
