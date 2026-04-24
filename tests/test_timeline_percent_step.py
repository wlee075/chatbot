"""
Tests for the percentage + step-position header logic.

Covers the senior's required tests:
  - test_percent_uses_only_completed_sections
  - test_step_position_independent_of_percent
  - test_zero_percent_displays_cleanly
  - test_completed_percent_updates_after_section_pass
"""

import sys
import os
import math

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config.sections import PRD_SECTIONS
from utils.progress_rail import compute_progress_data


def _pass_score():
    return {"verdict": "PASS", "completeness": 1.0}


def _sv(section_index: int, pass_ids: list[str]) -> dict:
    return {
        "section_index": section_index,
        "section_scores": {sid: _pass_score() for sid in pass_ids},
        "confirmed_qa_store": {},
    }


# ── test_percent_uses_only_completed_sections ─────────────────────────────────

def test_percent_uses_only_completed_sections():
    """% must be floor(completed/total*100), current section excluded."""
    total = len(PRD_SECTIONS)
    n_done = 3
    pass_ids = [PRD_SECTIONS[i].id for i in range(n_done)]
    sv = _sv(n_done, pass_ids)
    data = compute_progress_data(sv, PRD_SECTIONS)

    expected = round(n_done / total * 100)
    assert data["pct"] == expected
    assert data["completed"] == n_done

    # current section (index n_done) must NOT be counted
    assert data["completed"] < n_done + 1


def test_current_section_never_counted_as_complete():
    """Being 'on' a section must never inflate the percentage."""
    # Section 0 is current, 0 passed
    sv0 = _sv(0, [])
    d0  = compute_progress_data(sv0, PRD_SECTIONS)
    assert d0["pct"] == 0
    assert d0["completed"] == 0


# ── test_step_position_independent_of_percent ─────────────────────────────────

def test_step_position_independent_of_percent():
    """Step position (section_index+1) must be independent of how many are completed."""
    # On section 5, but 0 passed
    sv = _sv(5, [])
    data = compute_progress_data(sv, PRD_SECTIONS)
    # section_index=5 → Step 6 of N
    cur_step = next(
        (i + 1 for i, seg in enumerate(data["checklist"]) if seg["status"] == "current"),
        None,
    )
    assert cur_step == 6, f"Expected step 6, got {cur_step}"
    assert data["pct"] == 0  # no completions


def test_step_advances_independently_of_verdicts():
    """Advancing section_index moves the current marker without changing pct."""
    sv_3 = _sv(3, [])
    sv_7 = _sv(7, [])

    d3 = compute_progress_data(sv_3, PRD_SECTIONS)
    d7 = compute_progress_data(sv_7, PRD_SECTIONS)

    cur3 = next(i + 1 for i, s in enumerate(d3["checklist"]) if s["status"] == "current")
    cur7 = next(i + 1 for i, s in enumerate(d7["checklist"]) if s["status"] == "current")

    assert cur7 > cur3
    assert d3["pct"] == 0
    assert d7["pct"] == 0


# ── test_zero_percent_displays_cleanly ────────────────────────────────────────

def test_zero_percent_displays_cleanly():
    """At 0%, pct must be 0 (not a negative or None), 'completed' must be 0."""
    sv = _sv(0, [])
    data = compute_progress_data(sv, PRD_SECTIONS)
    assert data["pct"] == 0
    assert data["completed"] == 0
    assert isinstance(data["pct"], int)


def test_zero_percent_still_has_current_title():
    """At 0%, current_title must be a non-empty string (first section or override)."""
    sv = _sv(0, [])
    data = compute_progress_data(sv, PRD_SECTIONS)
    assert data["current_title"], "current_title must not be empty at 0%"


# ── test_completed_percent_updates_after_section_pass ────────────────────────

def test_completed_percent_updates_after_section_pass():
    """After a section earns a PASS, pct must increase relative to before."""
    sv_before = _sv(0, [])
    sv_after  = _sv(1, [PRD_SECTIONS[0].id])

    before = compute_progress_data(sv_before, PRD_SECTIONS)
    after  = compute_progress_data(sv_after,  PRD_SECTIONS)

    assert after["pct"] > before["pct"]
    assert after["completed"] == before["completed"] + 1


def test_pct_increases_monotonically_with_completions():
    """Each additional PASS verdict must raise pct monotonically."""
    total = len(PRD_SECTIONS)
    prev_pct = -1
    for n in range(total + 1):
        pass_ids = [PRD_SECTIONS[i].id for i in range(n)]
        idx = min(n, total - 1)
        sv = _sv(idx, pass_ids)
        data = compute_progress_data(sv, PRD_SECTIONS)
        assert data["pct"] >= prev_pct, (
            f"pct should not decrease: n={n}, prev={prev_pct}, got={data['pct']}"
        )
        prev_pct = data["pct"]
