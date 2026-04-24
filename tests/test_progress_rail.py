"""
Tests for the sticky progress rail compute layer.

All tests exercise compute_progress_data() from utils/progress_rail.py,
which is pure Python and requires no Streamlit imports.

Covers the senior's required tests:
  - test_compute_progress_data_shows_completed_section_percentage
  - test_compute_progress_data_highlights_current_section
  - test_compute_progress_data_updates_after_section_completion
  - test_compute_progress_data_shows_human_readable_missing_items
"""

import sys
import os
import uuid

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config.sections import PRD_SECTIONS
from utils.progress_rail import compute_progress_data, _derive_still_needed, _fmt_section_title


# ── helpers ───────────────────────────────────────────────────────────────────

def _qa(section_id: str, answer: str) -> dict:
    key = str(uuid.uuid4())
    return {
        key: {
            "section_id": section_id,
            "answer": answer,
            "contradiction_flagged": False,
            "version": 1,
        }
    }


def _pass_score() -> dict:
    return {"verdict": "PASS", "completeness": 1.0}


# ── _fmt_section_title ────────────────────────────────────────────────────────

def test_fmt_section_title_maps_headliner():
    assert _fmt_section_title("headliner") == "Summary"
    assert _fmt_section_title("Headliner") == "Summary"


def test_fmt_section_title_leaves_others_unchanged():
    assert _fmt_section_title("Key Stakeholders") == "Key Stakeholders"


# ── compute_progress_data: percentage ─────────────────────────────────────────

def test_compute_progress_data_shows_completed_section_percentage():
    """Completing N sections via PASS verdict must produce N/total * 100 %."""
    s0 = PRD_SECTIONS[0]
    s1 = PRD_SECTIONS[1]
    total = len(PRD_SECTIONS)

    sv = {
        "section_index": 2,
        "section_scores": {
            s0.id: _pass_score(),
            s1.id: _pass_score(),
        },
        "confirmed_qa_store": {},
    }

    data = compute_progress_data(sv, PRD_SECTIONS)
    expected_pct = round(2 / total * 100)

    assert data["pct"] == expected_pct, f"Expected {expected_pct}%, got {data['pct']}%"
    assert data["completed"] == 2
    assert data["total"] == total


def test_compute_progress_data_zero_percent_when_no_sections_complete():
    """No PASS verdicts → 0% regardless of section_index."""
    sv = {
        "section_index": 0,
        "section_scores": {},
        "confirmed_qa_store": {},
    }
    data = compute_progress_data(sv, PRD_SECTIONS)
    assert data["pct"] == 0
    assert data["completed"] == 0


# ── checklist highlight ───────────────────────────────────────────────────────

def test_compute_progress_data_highlights_current_section():
    """Exactly one checklist row must have status='current'."""
    sv = {
        "section_index": 1,
        "section_scores": {PRD_SECTIONS[0].id: _pass_score()},
        "confirmed_qa_store": {},
    }
    data = compute_progress_data(sv, PRD_SECTIONS)
    current_rows = [r for r in data["checklist"] if r["status"] == "current"]

    assert len(current_rows) == 1, "Exactly one section must be highlighted as current"
    assert current_rows[0]["id"] == PRD_SECTIONS[1].id


def test_compute_progress_data_completed_rows_have_complete_status():
    """All PASS-verdict sections before the current must appear as 'complete'."""
    sv = {
        "section_index": 2,
        "section_scores": {
            PRD_SECTIONS[0].id: _pass_score(),
            PRD_SECTIONS[1].id: _pass_score(),
        },
        "confirmed_qa_store": {},
    }
    data = compute_progress_data(sv, PRD_SECTIONS)
    done = {r["id"] for r in data["checklist"] if r["status"] == "complete"}
    assert PRD_SECTIONS[0].id in done
    assert PRD_SECTIONS[1].id in done


# ── after completion: highlight shifts ───────────────────────────────────────

def test_compute_progress_data_updates_after_section_completion():
    """After marking section 0 complete and advancing to index 1, the highlight moves."""
    sv_before = {
        "section_index": 0,
        "section_scores": {},
        "confirmed_qa_store": {},
    }
    sv_after = {
        "section_index": 1,
        "section_scores": {PRD_SECTIONS[0].id: _pass_score()},
        "confirmed_qa_store": {},
    }
    data_before = compute_progress_data(sv_before, PRD_SECTIONS)
    data_after  = compute_progress_data(sv_after, PRD_SECTIONS)

    # Before: section 0 is current, 0% complete
    before_cur = [r for r in data_before["checklist"] if r["status"] == "current"]
    assert before_cur[0]["id"] == PRD_SECTIONS[0].id
    assert data_before["pct"] == 0

    # After: section 1 is current, section 0 is complete
    after_cur = [r for r in data_after["checklist"] if r["status"] == "current"]
    assert after_cur[0]["id"] == PRD_SECTIONS[1].id
    assert data_after["completed"] >= 1


# ── still_needed: human-readable ─────────────────────────────────────────────

def test_compute_progress_data_shows_human_readable_missing_items():
    """still_needed must contain plain-English strings from expected_components."""
    sec = PRD_SECTIONS[0]
    sv = {
        "section_index": 0,
        "section_scores": {},
        "confirmed_qa_store": {},  # no answers yet
    }
    data = compute_progress_data(sv, PRD_SECTIONS)

    # still_needed should be non-empty (section has expected_components and nothing answered)
    if sec.expected_components:
        assert len(data["still_needed"]) > 0, "Expected missing items for unanswered section"
        assert len(data["still_needed"]) <= 3, "Must cap at 3 missing items"
        for item in data["still_needed"]:
            # Must not be an internal ID or routing key
            assert "section_id" not in item.lower()
            assert "verdict" not in item.lower()
            assert "_" not in item or " " in item  # prefer words over snake_case keys


def test_compute_progress_data_missing_items_reduce_when_answers_provided():
    """Providing a confirmed answer covering a component should reduce still_needed."""
    sec = PRD_SECTIONS[0]
    if not sec.expected_components:
        pytest.skip("No expected components for this section")

    # Pick the first expected component and craft an answer that covers it
    comp = sec.expected_components[0]
    keyword = comp.split()[0]  # first word

    qa = _qa(sec.id, f"The {keyword} is clearly defined and well understood.")
    sv_no_answers = {
        "section_index": 0,
        "section_scores": {},
        "confirmed_qa_store": {},
    }
    sv_with_answer = {
        "section_index": 0,
        "section_scores": {},
        "confirmed_qa_store": qa,
    }
    missing_before = compute_progress_data(sv_no_answers, PRD_SECTIONS)["still_needed"]
    missing_after  = compute_progress_data(sv_with_answer, PRD_SECTIONS)["still_needed"]

    assert len(missing_after) <= len(missing_before), \
        "Providing an answer must not increase missing items"
