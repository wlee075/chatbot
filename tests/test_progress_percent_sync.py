"""
Tests for progress percentage / completion sync.

Root cause fixed:
  advance_section_node writes section_index += 1 but does NOT always write a
  PASS verdict.  The old heuristic required _is_current_section_incomplete to
  return False (i.e. PASS exists) even for the i < idx branch, so ITER_CAP
  advances never registered as complete.

  Fix: is_complete = (verdict == "PASS") or (i < idx).

Covers the senior's required tests:
  - test_progress_percent_updates_after_section_completion
  - test_one_completed_section_out_of_13_shows_8_percent
  - test_current_section_does_not_increment_completion_until_completed
  - test_progress_percent_uses_same_completion_source_as_advance_banner
"""

import sys
import os
import math

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config.sections import PRD_SECTIONS
from utils.progress_rail import compute_progress_data


TOTAL = len(PRD_SECTIONS)


def _sv_index_only(section_index: int) -> dict:
    """Simulate what advance_section_node writes: only section_index advances,
    no PASS verdict is guaranteed (ITER_CAP path)."""
    return {
        "section_index": section_index,
        "section_scores": {},        # deliberate: no PASS written
        "confirmed_qa_store": {},
    }


def _sv_pass(section_index: int, pass_ids: list) -> dict:
    """Simulate a PASS-based advance."""
    return {
        "section_index": section_index,
        "section_scores": {sid: {"verdict": "PASS", "completeness": 1.0} for sid in pass_ids},
        "confirmed_qa_store": {},
    }


# ── test_progress_percent_updates_after_section_completion ────────────────────

def test_progress_percent_updates_after_section_completion():
    """After advance_section_node runs (index += 1), pct must reflect completion.

    This is the exact scenario that was broken: ITER_CAP advance, no PASS written.
    """
    sv_before = _sv_index_only(0)   # on section 0, nothing done yet
    sv_after  = _sv_index_only(1)   # advance_section_node moved to section 1

    before = compute_progress_data(sv_before, PRD_SECTIONS)
    after  = compute_progress_data(sv_after,  PRD_SECTIONS)

    # Before: 0 sections completed
    assert before["pct"] == 0
    assert before["completed"] == 0

    # After: section 0 is behind us → must count as complete
    assert after["completed"] == 1
    assert after["pct"] > 0, (
        "After advancing section_index, pct must be > 0 even without a PASS verdict"
    )


# ── test_one_completed_section_out_of_13_shows_8_percent ─────────────────────

def test_one_completed_section_out_of_13_shows_8_percent():
    """1 completed section out of 13 must show 8% (round(1/13*100))."""
    sv = _sv_index_only(1)   # section 0 was advanced over, no PASS needed
    data = compute_progress_data(sv, PRD_SECTIONS)

    expected = round(1 / TOTAL * 100)
    assert data["completed"] == 1, f"Expected 1 completed, got {data['completed']}"
    assert data["pct"] == expected, (
        f"Expected {expected}%, got {data['pct']}%"
    )


def test_multiple_index_advances_accumulate_correctly():
    """N sections advanced over must each count as complete."""
    for n in range(1, min(6, TOTAL)):
        sv = _sv_index_only(n)
        data = compute_progress_data(sv, PRD_SECTIONS)
        expected_pct = round(n / TOTAL * 100)
        assert data["completed"] == n, f"n={n}: completed={data['completed']}"
        assert data["pct"] == expected_pct, f"n={n}: pct={data['pct']} != {expected_pct}"


# ── test_current_section_does_not_increment_completion_until_completed ────────

def test_current_section_does_not_increment_completion_until_completed():
    """The section at section_index (the active one) must NOT count as complete."""
    sv = _sv_index_only(3)   # sections 0,1,2 are behind us; 3 is active
    data = compute_progress_data(sv, PRD_SECTIONS)

    assert data["completed"] == 3, "Only sections 0,1,2 should be counted"
    # Section 3 is current, not complete
    assert data["checklist"][3]["status"] == "current"
    assert data["checklist"][3]["status"] != "complete"


def test_current_section_not_counted_with_pass_either():
    """Current section status is always 'current' in the checklist, even with PASS.

    A PASS on the current section DOES count in completed_count (the score was
    already awarded), but the checklist status remains 'current' (advance hasn't
    happened yet).  completed = sections_before + current_if_pass.
    """
    # section_index=2, section 2 has a PASS verdict (advance is imminent but not done yet)
    sv = _sv_pass(2, [PRD_SECTIONS[2].id])
    data = compute_progress_data(sv, PRD_SECTIONS)

    # section 2 is the active one → checklist status must be 'current'
    assert data["checklist"][2]["status"] == "current"
    # Sections 0 and 1 (before idx=2) plus section 2 itself (has PASS) = 3 complete
    assert data["completed"] == 3


# ── test_progress_percent_uses_same_completion_source_as_advance_banner ───────

def test_progress_percent_uses_same_completion_source_as_advance_banner():
    """The banner 'Elevator Pitch completed! Moving to Key Stakeholders...' fires
    when advance_section_node runs.  That node writes section_index = prev+1 and
    does NOT always write a PASS to section_scores.

    The percentage must therefore count sections by positional advancement
    (i < idx), NOT by PASS verdict alone.
    """
    # Simulate: banner fired, now at Key Stakeholders (idx=2, assuming idx starts at 0)
    # Elevator Pitch is idx=0, Key Stakeholders is idx=1 (or wherever they are).
    # We just need to verify: ANY positional advance → count as complete
    sv = _sv_index_only(2)   # 2 sections behind us, no PASS anywhere
    data = compute_progress_data(sv, PRD_SECTIONS)

    assert data["completed"] == 2, (
        "Sections advanced past must count as complete even without PASS verdicts."
    )
    assert data["pct"] == round(2 / TOTAL * 100), (
        f"pct should match 2/{TOTAL} advancement: got {data['pct']}%"
    )


def test_iter_cap_advance_counts_same_as_pass_advance():
    """ITER_CAP forced advance (no PASS) must produce the same pct as PASS advance."""
    n = 3
    pass_ids = [PRD_SECTIONS[i].id for i in range(n)]

    sv_iter_cap = _sv_index_only(n)           # no PASS verdicts
    sv_pass     = _sv_pass(n, pass_ids)       # explicit PASS verdicts

    d_iter = compute_progress_data(sv_iter_cap, PRD_SECTIONS)
    d_pass = compute_progress_data(sv_pass,     PRD_SECTIONS)

    assert d_iter["completed"] == d_pass["completed"] == n
    assert d_iter["pct"] == d_pass["pct"]
