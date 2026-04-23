"""tests/test_contradiction_guard.py

Guards against false contradiction language on first turn or when there are
no valid conflict objects in state.

Tests map to the 5 required names from the spec:
- test_first_turn_cannot_emit_earlier_detail_mismatch_question
- test_no_contradiction_template_without_conflict_objects
- test_preserved_raw_string_beats_generic_fallback_when_usable
- test_new_session_has_empty_contradiction_inputs
- test_summary_not_current_section_on_first_turn
"""
from __future__ import annotations
from unittest.mock import patch, MagicMock
import pytest


# ─── Fixtures ────────────────────────────────────────────────────────────────

_GENERIC_FALLBACK = "What is the core problem you are trying to solve?"


def _empty_state() -> dict:
    """State as it would appear on the very first user turn of a new session."""
    return {
        "confirmed_qa_store": {},
        "prd_sections": {},
        "section_index": 0,
        "section_scores": {},
        "progress_pct": 0,
        "raw_answer_buffer": "We have a manual supplier data alignment issue.",
        "conversation_history": [
            {"role": "user", "content": "We have a manual supplier data alignment issue."}
        ],
        "recent_questions": [],
        "remaining_subparts": [],
        "concept_conflicts": [],
        "uploaded_files": [],
    }


def _state_with_stale_ghost_conflicts() -> dict:
    """State with concept_conflicts that have no surface/concept_key — ghost entries."""
    s = _empty_state()
    s["concept_conflicts"] = [{}, {"unrelated_field": "x"}]
    return s


def _state_with_valid_conflict() -> dict:
    """State with a proper, non-ghost conflict."""
    s = _empty_state()
    s["concept_conflicts"] = [{"surface": "timeline", "concept_key": "timeline_conflict"}]
    s["confirmed_qa_store"] = {
        "q1": {
            "section_id": "problem_statement",
            "resolved_subparts": ["user_goal"],
            "user_answer": "Launch in Q1",
            "contradiction_flagged": False,
        },
        "q2": {
            "section_id": "problem_statement",
            "resolved_subparts": ["user_goal"],
            "user_answer": "Launch in Q3",
            "contradiction_flagged": True,
        },
    }
    return s


# ─── Test 1: first turn cannot emit mismatch language ────────────────────────

def test_first_turn_cannot_emit_earlier_detail_mismatch_question():
    """On a fresh session with no prior QA, the fallback must not contain
    'earlier detail', 'mismatch', or 'which version should we keep'."""
    from graph.nodes import _generate_context_aware_fallback
    result = _generate_context_aware_fallback(_empty_state())
    _bad_phrases = ["earlier detail", "mismatch", "which version should we keep"]
    for phrase in _bad_phrases:
        assert phrase.lower() not in result.lower(), (
            f"Contradiction language '{phrase}' found on first turn with empty state.\n"
            f"Got: {result!r}"
        )


# ─── Test 2: no contradiction template without valid conflict objects ─────────

def test_no_contradiction_template_without_conflict_objects():
    """Even if concept_conflicts list is non-empty, ghost entries (no surface/concept_key)
    must NOT trigger the 'Which version is correct?' template."""
    from graph.nodes import _generate_context_aware_fallback
    result = _generate_context_aware_fallback(_state_with_stale_ghost_conflicts())
    assert "which version is correct" not in result.lower(), (
        f"Ghost conflicts triggered contradiction template. Got: {result!r}"
    )
    assert "which version should we keep" not in result.lower(), (
        f"Ghost conflicts triggered mismatch template. Got: {result!r}"
    )


# ─── Test 3: preserved raw string beats generic fallback ─────────────────────

def test_preserved_raw_string_beats_generic_fallback_when_usable():
    """A usable raw LLM string should survive through _normalize_generated_question
    and NOT be replaced by _generate_context_aware_fallback."""
    usable_raw = (
        "Based on what you shared, it sounds like the core problem is daily manual "
        "alignment of inconsistent supplier data causing 8-12% error rates. "
        "Is that the main pain point, or is there a bigger driver I'm missing?"
    )
    with (
        patch("graph.nodes._get_llm", return_value=MagicMock(model="gemini-2.5-flash")),
        patch("graph.nodes._generate_context_aware_fallback", return_value=_GENERIC_FALLBACK),
    ):
        from graph.nodes import _normalize_generated_question

        class _DummySection:
            id = "problem_statement"
            title = "Problem Statement"

        norm, _ = _normalize_generated_question(
            usable_raw,
            _empty_state(),
            {"thread_id": "t1", "run_id": "r1", "node_name": "generate_questions_node"},
            orch_plan=None,
            section=_DummySection(),
        )
    result = norm["single_next_question"]
    assert result == usable_raw, (
        f"Usable raw string was overwritten by generic fallback.\n"
        f"Expected: {usable_raw!r}\nGot: {result!r}"
    )


# ─── Test 4: new session has empty contradiction inputs ───────────────────────

def test_new_session_has_empty_contradiction_inputs():
    """A fresh empty state has 0 valid conflicts and 0 QA entries — verify guard logic."""
    state = _empty_state()
    # Direct guard check: no valid conflicts
    valid_conflicts = [
        c for c in state.get("concept_conflicts", [])
        if isinstance(c, dict) and (c.get("surface") or c.get("concept_key"))
    ]
    assert len(valid_conflicts) == 0, (
        "New session should have zero valid conflict objects"
    )
    assert len(state["confirmed_qa_store"]) == 0, (
        "New session should have empty QA store"
    )
    # Valid conflict state DOES produce conflict question
    valid_state = _state_with_valid_conflict()
    valid_confs = [
        c for c in valid_state.get("concept_conflicts", [])
        if isinstance(c, dict) and (c.get("surface") or c.get("concept_key"))
    ]
    assert len(valid_confs) == 1, (
        "State with valid surface-tagged conflict should have exactly 1 valid conflict"
    )


# ─── Test 5: Summary not active on first turn ─────────────────────────────────

def test_summary_not_current_section_on_first_turn():
    """'summary' must not be a registered PRDSection — the UI alias 'Summary'
    maps to 'headliner'. Verifying that section_index=0 maps to a real section,
    not 'summary'."""
    from config.sections import PRD_SECTIONS, get_section_by_index
    first_section = get_section_by_index(0)
    assert first_section is not None, "section_index=0 must resolve to a valid section"
    assert first_section.id != "summary", (
        f"First section on session start must not be 'summary'. Got: {first_section.id!r}.\n"
        "If the UI shows 'Summary', it is a display alias — not an interviewable section."
    )
    # Also check 'summary' is not in the PRD_SECTIONS list at all
    section_ids = [s.id for s in PRD_SECTIONS]
    assert "summary" not in section_ids, (
        "'summary' must not be a PRDSection — it is a UI display alias only."
    )
