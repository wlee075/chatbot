"""
Tests for PDF download gate logic (get_pdf_download_state).

Covers the required tests:
  - test_pdf_locked_below_80
  - test_pdf_enabled_as_draft_at_80
  - test_pdf_enabled_as_draft_at_99
  - test_pdf_enabled_as_final_at_100
  - test_button_label_changes_with_progress
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from utils.progress_rail import get_pdf_download_state


# ── test_pdf_locked_below_80 ─────────────────────────────────────────────────

def test_pdf_locked_at_0():
    state = get_pdf_download_state(0)
    assert state["enabled"] is False
    assert "80" in state["label"] or "lock" in state["label"].lower() or "🔒" in state["label"]


def test_pdf_locked_at_50():
    state = get_pdf_download_state(50)
    assert state["enabled"] is False


def test_pdf_locked_at_79():
    state = get_pdf_download_state(79)
    assert state["enabled"] is False


def test_pdf_locked_badge_is_empty():
    """Below 80 there is no badge."""
    state = get_pdf_download_state(40)
    assert state["badge"] == ""


def test_pdf_locked_hint_contains_pct():
    """Locked hint should mention the current percentage."""
    state = get_pdf_download_state(62)
    assert "62" in state["hint"]


# ── test_pdf_enabled_as_draft_at_80 ─────────────────────────────────────────

def test_pdf_enabled_at_80():
    state = get_pdf_download_state(80)
    assert state["enabled"] is True


def test_pdf_draft_label_at_80():
    state = get_pdf_download_state(80)
    assert "Draft" in state["label"]


def test_pdf_draft_badge_at_80():
    state = get_pdf_download_state(80)
    assert state["badge"] == "Draft"


def test_pdf_draft_hint_at_80():
    state = get_pdf_download_state(80)
    assert "80" in state["hint"]


def test_pdf_draft_type_is_secondary_at_80():
    """Draft button should be secondary style (not the same as Final)."""
    state = get_pdf_download_state(80)
    assert state["btn_type"] == "secondary"


# ── test_pdf_enabled_as_draft_at_99 ─────────────────────────────────────────

def test_pdf_enabled_at_99():
    state = get_pdf_download_state(99)
    assert state["enabled"] is True


def test_pdf_draft_label_at_99():
    state = get_pdf_download_state(99)
    assert "Draft" in state["label"]


def test_pdf_draft_badge_at_99():
    state = get_pdf_download_state(99)
    assert state["badge"] == "Draft"


def test_pdf_draft_type_is_secondary_at_99():
    state = get_pdf_download_state(99)
    assert state["btn_type"] == "secondary"


# ── test_pdf_enabled_as_final_at_100 ────────────────────────────────────────

def test_pdf_enabled_at_100():
    state = get_pdf_download_state(100)
    assert state["enabled"] is True


def test_pdf_final_label_at_100():
    state = get_pdf_download_state(100)
    assert "Final" in state["label"]
    assert "Draft" not in state["label"]


def test_pdf_final_badge_at_100():
    state = get_pdf_download_state(100)
    assert state["badge"] == "Complete"


def test_pdf_final_type_is_primary():
    """Final button should be primary style."""
    state = get_pdf_download_state(100)
    assert state["btn_type"] == "primary"


def test_pdf_final_hint_at_100():
    state = get_pdf_download_state(100)
    assert "100" in state["hint"] or "final" in state["hint"].lower()


# ── test_button_label_changes_with_progress ──────────────────────────────────

def test_label_changes_across_thresholds():
    """Button label must differ at each threshold zone."""
    locked = get_pdf_download_state(40)["label"]
    draft  = get_pdf_download_state(85)["label"]
    final  = get_pdf_download_state(100)["label"]

    assert locked != draft
    assert draft  != final
    assert locked != final


def test_enabled_changes_at_80_boundary():
    """Exact boundary: 79 → locked, 80 → enabled."""
    assert get_pdf_download_state(79)["enabled"] is False
    assert get_pdf_download_state(80)["enabled"] is True


def test_badge_changes_at_100_boundary():
    """Exact boundary: 99 → Draft badge, 100 → Complete badge."""
    assert get_pdf_download_state(99)["badge"]  == "Draft"
    assert get_pdf_download_state(100)["badge"] == "Complete"


def test_all_states_return_required_keys():
    """All states must return all required keys."""
    required = {"enabled", "label", "btn_type", "badge", "hint"}
    for pct in [0, 50, 79, 80, 90, 99, 100]:
        state = get_pdf_download_state(pct)
        missing = required - state.keys()
        assert not missing, f"pct={pct} missing keys: {missing}"
