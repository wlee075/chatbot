"""
Tests for the corrected timeline bar visual states.

Verifies the separation of COMPLETED PROGRESS from CURRENT POSITION:
  - Completed segments use solid fill (tl-done)
  - Current segment uses outline ring (tl-cur-pip) — NOT a solid fill
  - Future segments use muted style (tl-future)

Covers the senior's required tests:
  - test_zero_percent_has_no_filled_segments
  - test_current_step_uses_outline_not_fill
  - test_completed_steps_fill_correctly
  - test_current_index_renders_independent_of_percent
"""

import sys
import os

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


def _pill_classes_for(sv: dict) -> list[str]:
    """Return the CSS class token ('done'/'cur'/'future') per segment."""
    data = compute_progress_data(sv, PRD_SECTIONS)
    classes = []
    for seg in data["checklist"]:
        if seg["status"] == "complete":
            classes.append("done")
        elif seg["status"] == "current":
            classes.append("cur")
        else:
            classes.append("future")
    return classes


# ── test_zero_percent_has_no_filled_segments ──────────────────────────────────

def test_zero_percent_has_no_filled_segments():
    """At 0% complete, no segment must have 'done' (solid fill) status."""
    sv = _sv(0, [])
    classes = _pill_classes_for(sv)

    assert "done" not in classes, (
        "No segment should appear as 'done' when 0 sections are complete"
    )
    # Exactly one current marker must exist
    assert classes.count("cur") == 1
    # All other segments are future
    assert classes.count("future") == len(PRD_SECTIONS) - 1


def test_zero_percent_header_text_does_not_say_percent():
    """When completed==0, right_text must say '0 sections complete', not a percent."""
    data = compute_progress_data(_sv(0, []), PRD_SECTIONS)
    completed = data["completed"]
    pct = data["pct"]
    # The render function shows: '0 sections complete' when completed == 0
    # Verify the data driving that branch
    assert completed == 0
    assert pct == 0


# ── test_current_step_uses_outline_not_fill ───────────────────────────────────

def test_current_step_uses_outline_not_fill():
    """Current segment must be 'cur' status (outline), not 'done' (fill)."""
    sv = _sv(2, [PRD_SECTIONS[0].id, PRD_SECTIONS[1].id])
    classes = _pill_classes_for(sv)

    assert classes[2] == "cur", (
        f"Segment at index 2 should be current (outline), got {classes[2]!r}"
    )
    # Current must not be 'done'
    assert classes[2] != "done"


def test_current_step_index_maps_to_cur():
    """The segment at section_index must always be 'cur', regardless of completion count."""
    for idx in range(min(5, len(PRD_SECTIONS))):
        pass_ids = [PRD_SECTIONS[i].id for i in range(idx)]
        sv = _sv(idx, pass_ids)
        classes = _pill_classes_for(sv)
        assert classes[idx] == "cur", (
            f"section_index={idx}: expected 'cur' at pos {idx}, got {classes[idx]!r}"
        )


# ── test_completed_steps_fill_correctly ───────────────────────────────────────

def test_completed_steps_fill_correctly():
    """Completed segments must be 'done', and exactly N segments for N completions."""
    n = 3
    pass_ids = [PRD_SECTIONS[i].id for i in range(n)]
    sv = _sv(n, pass_ids)
    classes = _pill_classes_for(sv)

    done_count = classes.count("done")
    assert done_count == n, f"Expected {n} done segments, got {done_count}"
    # All done segments are before the current (by index)
    for i in range(n):
        assert classes[i] == "done", f"Segment {i} should be done"
    assert classes[n] == "cur"


def test_completed_steps_counter_matches_pill_count():
    """The 'completed' field in compute_progress_data must equal the done pill count."""
    n = 2
    pass_ids = [PRD_SECTIONS[i].id for i in range(n)]
    sv = _sv(n, pass_ids)
    data = compute_progress_data(sv, PRD_SECTIONS)
    classes = _pill_classes_for(sv)

    assert data["completed"] == classes.count("done") == n


# ── test_current_index_renders_independent_of_percent ────────────────────────

def test_current_index_renders_independent_of_percent():
    """The current marker position depends on section_index, not on PASS verdicts.

    With section_index=5 and no explicit PASS verdicts, sections 0-4 are
    counted as complete (they are behind the current pointer), so pct > 0.
    The key invariant is: checklist[5].status == 'cur' regardless of pct.
    """
    sv = _sv(5, [])  # no PASS verdicts, but section_index = 5
    classes = _pill_classes_for(sv)
    data    = compute_progress_data(sv, PRD_SECTIONS)

    # Position 5 must be 'cur'
    assert classes[5] == "cur"
    # Sections 0-4 are behind idx=5, so they count as complete
    assert data["completed"] == 5
    expected_pct = round(5 / len(PRD_SECTIONS) * 100)
    assert data["pct"] == expected_pct
    # Segments 0..4 must all be 'done' (completed via positional advancement)
    for i in range(5):
        assert classes[i] == "done", (
            f"Segment {i} should be 'done' (behind current pointer), got {classes[i]!r}"
        )


def test_current_marker_one_ahead_after_completion():
    """After N completions, the current marker must sit at N (the next section)."""
    for n in range(1, min(5, len(PRD_SECTIONS))):
        pass_ids = [PRD_SECTIONS[i].id for i in range(n)]
        sv = _sv(n, pass_ids)
        classes = _pill_classes_for(sv)
        assert classes[n] == "cur", (
            f"After {n} completions, current marker should be at {n}, not {classes[n]!r}"
        )
