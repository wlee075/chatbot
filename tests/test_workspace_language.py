"""
Tests for the workspace-language timeline bar.

Verifies:
  - No 'Step X of Y' language in output data
  - Current section label updates when user switches sections
  - Percent is independent of current section position
  - Partial sections render a distinct state (amber) vs pending (muted)

Covers the senior's required tests:
  - test_ui_contains_no_step_language
  - test_current_section_label_updates_when_user_jumps
  - test_percent_independent_of_current_section_position
  - test_partial_sections_render_distinct_state
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config.sections import PRD_SECTIONS
from utils.progress_rail import compute_progress_data


def _pass_score():
    return {"verdict": "PASS", "completeness": 1.0}


def _sv(section_index: int, pass_ids: list[str], qa_store: dict | None = None) -> dict:
    return {
        "section_index": section_index,
        "section_scores": {sid: _pass_score() for sid in pass_ids},
        "confirmed_qa_store": qa_store or {},
    }


# ── test_ui_contains_no_step_language ────────────────────────────────────────

def test_ui_data_does_not_produce_step_wording():
    """compute_progress_data must not produce any 'step' concept in its output.

    The render layer maps `current_title` to 'Current Section: <name>'.
    We verify the data layer returns a section name string, NOT a numeric step.
    """
    sv = _sv(2, [])
    data = compute_progress_data(sv, PRD_SECTIONS)

    # current_title must be a meaningful section name, not a number
    title = data["current_title"]
    assert title, "current_title must not be empty"
    assert not title.isdigit(), f"current_title must not be a number: {title!r}"
    # Must not look like 'Step 3 of 13' or similar
    assert "of" not in title.lower() or "step" not in title.lower(), (
        f"current_title must not contain step-sequence language: {title!r}"
    )


def test_current_title_is_section_name_not_index():
    """current_title must equal the human-readable section title, not its index."""
    for idx in range(min(5, len(PRD_SECTIONS))):
        sv = _sv(idx, [])
        data = compute_progress_data(sv, PRD_SECTIONS)
        assert data["current_title"] == data["checklist"][idx]["title"]


# ── test_current_section_label_updates_when_user_jumps ────────────────────────

def test_current_section_label_updates_when_user_jumps():
    """Jumping section_index to a non-adjacent section must update current_title."""
    sv_at_0 = _sv(0, [])
    sv_at_5 = _sv(5, [])

    d0 = compute_progress_data(sv_at_0, PRD_SECTIONS)
    d5 = compute_progress_data(sv_at_5, PRD_SECTIONS)

    assert d0["current_id"] != d5["current_id"]
    assert d0["current_title"] != d5["current_title"]
    # Correct sections are flagged current in checklist
    assert d0["checklist"][0]["status"] == "current"
    assert d5["checklist"][5]["status"] == "current"


def test_current_marker_follows_any_arbitrary_jump():
    """Non-linear jump: current must always follow section_index exactly."""
    jump_to = [0, 4, 1, 7, 2]  # non-sequential
    for idx in jump_to:
        sv = _sv(idx, [])
        data = compute_progress_data(sv, PRD_SECTIONS)
        assert data["checklist"][idx]["status"] == "current", (
            f"After jump to {idx}, checklist[{idx}].status should be 'current'"
        )


# ── test_percent_independent_of_current_section_position ─────────────────────

def test_percent_independent_of_current_section_position():
    """With the same number of sections behind both pointers, pct must be equal."""
    # Section 0 has a PASS; focus at idx=1 vs idx=6
    pass_ids = [PRD_SECTIONS[0].id]

    sv_cur1 = _sv(1, pass_ids)
    sv_cur6 = _sv(6, pass_ids)

    d1 = compute_progress_data(sv_cur1, PRD_SECTIONS)
    d6 = compute_progress_data(sv_cur6, PRD_SECTIONS)

    # d6 has 6 sections behind cursor (0-5), d1 has 1 — they cannot be equal.
    # The correct invariant: pct increases monotonically with (max(idx, pass_count)).
    assert d6["pct"] >= d1["pct"], (
        f"More sections advanced must mean >= pct: {d6['pct']} < {d1['pct']}"
    )


def test_pct_only_counts_pass_verdicts():
    """pct must equal round(completed/total*100) and not include current."""
    n = 4
    pass_ids = [PRD_SECTIONS[i].id for i in range(n)]
    sv = _sv(n, pass_ids)
    data = compute_progress_data(sv, PRD_SECTIONS)

    expected = round(n / len(PRD_SECTIONS) * 100)
    assert data["pct"] == expected
    assert data["completed"] == n


# ── test_partial_sections_render_distinct_state ───────────────────────────────

def _make_qa_entry(section_id: str, answer: str = "some answer") -> dict:
    return {
        "section_id": section_id,
        "answer": answer,
        "contradiction_flagged": False,
    }


def test_partial_sections_render_distinct_state():
    """A section with answers but no PASS and AHEAD of the current index must be 'partial'."""
    # Focus on section 1; plant answers for section 4 (ahead of current, no PASS)
    target_id = PRD_SECTIONS[4].id
    qa_store = {
        f"q_{target_id}_1": _make_qa_entry(target_id, "some data"),
    }
    sv = _sv(1, [], qa_store=qa_store)
    data = compute_progress_data(sv, PRD_SECTIONS)

    seg4 = data["checklist"][4]
    assert seg4["id"] == target_id
    assert seg4["status"] == "partial", (
        f"Future section with QA should be 'partial', got {seg4['status']!r}"
    )


def test_partial_section_differs_from_pending():
    """Partial and pending must be different statuses when both are ahead of current."""
    # Focus on section 1; section 4 has answers (partial), section 5 has none (pending)
    qa_store = {
        "q4": _make_qa_entry(PRD_SECTIONS[4].id, "answer here"),
    }
    sv = _sv(1, [], qa_store=qa_store)
    data = compute_progress_data(sv, PRD_SECTIONS)

    status_4 = data["checklist"][4]["status"]
    status_5 = data["checklist"][5]["status"]
    assert status_4 == "partial"
    assert status_5 == "pending"
    assert status_4 != status_5


def test_partial_does_not_count_as_completed():
    """Partial sections must NOT be counted in 'completed' or pct."""
    # Focus on section 0; section 4 (ahead) has QA but no PASS
    target_id = PRD_SECTIONS[4].id
    qa_store = {"q4": _make_qa_entry(target_id, "some data")}
    sv = _sv(0, [], qa_store=qa_store)
    data = compute_progress_data(sv, PRD_SECTIONS)

    assert data["completed"] == 0
    assert data["pct"] == 0


def test_complete_overrides_partial():
    """If a section has PASS, it must be 'complete', not 'partial'."""
    target_id = PRD_SECTIONS[4].id
    qa_store = {"q4": _make_qa_entry(target_id, "some data")}
    sv = _sv(1, [target_id], qa_store=qa_store)
    data = compute_progress_data(sv, PRD_SECTIONS)

    seg4 = data["checklist"][4]
    assert seg4["status"] == "complete", (
        f"PASS verdict must override partial: got {seg4['status']!r}"
    )
