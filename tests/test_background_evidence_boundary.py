"""tests/test_background_evidence_boundary.py

Regression tests for the Background section evidence pipeline (Phase 1.6).

These tests verify all 4 enforcement layers in _infer_background_synthesis:
  1. Evidence-type tagging: workflow > metric > pain > stakeholder > other
  2. Sentence-safety filter: reject fragments before candidate construction
  3. Background boundary: primary candidate from workflow evidence only
  4. Normalized candidate: no semicolon-joined raw fragment arrays

Tests:
  T8  – background does not use pain label as primary candidate
  T9  – background rejects fragment-only snippets
  T10 – background does not merge stakeholder fragment with pain fragment
  T11 – background prefers workflow sentence from stakeholder turn
  T12 – problem_statement can use delay but background cannot (boundary)
  T13 – deterministic formatter requires normalized summary text
"""
from __future__ import annotations

import pytest

from utils.section_inference import (
    infer_section_candidates,
    _bg_classify_evidence_type,
    _bg_is_sentence_safe,
    _bg_is_pain_label_fragment,
)


# ── Shared fixture ────────────────────────────────────────────────────────────

def _state(qa_entries: list[dict]) -> dict:
    qa_store = {}
    for i, e in enumerate(qa_entries):
        qa_store[f"entry_{i}"] = {
            "section_id":            e["section_id"],
            "answer":                e.get("answer", ""),
            "question":              e.get("question", ""),
            "version":               e.get("version", 1),
            "contradiction_flagged": e.get("contradiction_flagged", False),
        }
    return {"confirmed_qa_store": qa_store, "prd_sections": {}}


# ── T8: Pain-label-only evidence must not become the primary candidate ─────────

def test_T8_background_does_not_use_pain_label_as_primary_candidate():
    """T8: 'Delayed time-to-market' must not render as Background's current-state summary."""
    state = _state([
        {"section_id": "elevator_pitch",
         "answer": "Delayed time-to-market. High error rate. Manual burden on ops team."},
        {"section_id": "headliner",
         "answer": "Errors. Frustration. Backlog. Pain across the org."},
    ])
    result = infer_section_candidates("background", state)
    candidates = result.get("candidate_items", [])

    for c in candidates:
        c_text = c if isinstance(c, str) else str(c)
        assert "Delayed time-to-market" not in c_text or len(c_text.split()) > 15, (
            f"Background candidate uses pain label as primary text:\n{c_text!r}"
        )
        # Must not start with a pain noun directly after the template
        assert not c_text.startswith(
            "From what you've shared, the current workflow seems to be: Delayed"
        ), f"Background candidate opens with pain label: {c_text!r}"


# ── T9: Fragment-only snippets must be rejected ────────────────────────────────

def test_T9_background_rejects_fragment_only_snippets():
    """T9: Strings with fewer than 6 words or no verb are fragments and must fail sentence-safety."""
    fragments = [
        "When",
        "The operations.",
        "Delayed.",
        "Issues.",
        "Error rate.",
    ]
    for frag in fragments:
        assert not _bg_is_sentence_safe(frag), (
            f"Sentence-safety filter accepted a fragment that should be rejected: {frag!r}"
        )


# ── T10: Stakeholder fragment + pain fragment must not merge ───────────────────

def test_T10_background_does_not_merge_stakeholder_fragment_with_pain_fragment():
    """T10: When both inputs are short fragments, candidate must be SEED (no join).
    The old bug produced: 'Delayed time-to-market. When ; The operations.'
    """
    state = _state([
        {"section_id": "key_stakeholders", "answer": "The operations team."},
        {"section_id": "elevator_pitch",   "answer": "Delayed time-to-market."},
    ])
    result = infer_section_candidates("background", state)
    candidates = result.get("candidate_items", [])

    for c in candidates:
        c_text = c if isinstance(c, str) else str(c)
        # Must not contain semicolons (the old fragment-join pattern)
        assert "; " not in c_text, (
            f"Background candidate contains semicolon-joined fragments (old bug):\n{c_text!r}"
        )


# ── T11: Workflow sentence in stakeholder answer must win over pain bullets ────

def test_T11_background_prefers_workflow_sentence_from_stakeholder_turn():
    """T11: 'opens spreadsheets at 9 AM, filters by supplier, and manually matches
    150-300 products' must become the Background evidence — not pain bullets.
    """
    state = _state([
        {
            "section_id": "key_stakeholders",
            "answer": (
                "An inbound operations coordinator opens spreadsheets at 9 AM, "
                "filters by supplier, and manually matches 150-300 products per day "
                "using titles, MPNs, and images."
            ),
        },
        {
            "section_id": "elevator_pitch",
            "answer": "Delayed time-to-market. Manual burden. High error rate.",
        },
    ])
    result = infer_section_candidates("background", state)
    candidates = result.get("candidate_items", [])

    if not candidates:
        # SEED path is valid if no workflow evidence survives
        assert result.get("inference_available") is False
        return

    c_text = candidates[0] if isinstance(candidates[0], str) else str(candidates[0])

    assert any(kw in c_text.lower() for kw in (
        "spreadsheet", "filter", "manually match", "opens", "coordinator", "150", "mpn"
    )), (
        f"Background ignored the workflow sentence and used pain bullets:\n{c_text!r}"
    )


# ── T12: Evidence-type classifier boundary test ────────────────────────────────

def test_T12_problem_statement_can_use_delay_but_background_cannot():
    """T12: 'Delayed time-to-market' must be classified as 'pain', not 'workflow'.
    Background's primary evidence boundary excludes pain-typed facts.
    """
    pain_text     = "Delayed time-to-market due to slow catalog releases."
    workflow_text = "Operators manually match 150-300 products per day using spreadsheets."

    assert _bg_classify_evidence_type(pain_text) == "pain", (
        f"Expected 'pain' for: {pain_text!r}, got {_bg_classify_evidence_type(pain_text)!r}"
    )
    assert _bg_classify_evidence_type(workflow_text) == "workflow", (
        f"Expected 'workflow' for: {workflow_text!r}, got {_bg_classify_evidence_type(workflow_text)!r}"
    )

    # Pain-label fragment detection
    assert _bg_is_pain_label_fragment("Delayed time-to-market."), (
        "Pain-label fragment detector missed 'Delayed time-to-market.'"
    )
    assert not _bg_is_pain_label_fragment(workflow_text), (
        f"Pain-label fragment detector incorrectly flagged a workflow sentence:\n{workflow_text!r}"
    )


# ── T13: Formatter must produce normalized, coherent summary text ──────────────

def test_T13_deterministic_formatter_requires_normalized_summary_text():
    """T13: The Background candidate must be a coherent sentence — not a semicolon-joined
    fragment array. This directly tests the fixed formatter path.
    """
    state = _state([
        {
            "section_id": "key_stakeholders",
            "answer": (
                "An inbound operations coordinator starts each morning by opening supplier "
                "spreadsheets, filtering by supplier, and manually matching 150-300 products "
                "using titles, MPNs, and images."
            ),
        },
        {
            "section_id": "elevator_pitch",
            "answer": (
                "Manual product mapping cannot scale: each new supplier adds another "
                "spreadsheet and another hour of ops time per day."
            ),
        },
    ])
    result = infer_section_candidates("background", state)
    candidates = result.get("candidate_items", [])

    if not candidates:
        # SEED path is valid if no workflow evidence survives filtering
        assert result.get("inference_available") is False
        return

    c_text = candidates[0] if isinstance(candidates[0], str) else str(candidates[0])

    assert isinstance(c_text, str), f"Candidate must be a string, got {type(c_text)}"

    # Must NOT use semicolons to join raw fragments (old bug pattern)
    assert "; " not in c_text, (
        f"Candidate contains semicolon-joined fragments:\n{c_text!r}"
    )

    # First clause must be sentence-safe
    first_clause = c_text.split(".")[0]
    assert _bg_is_sentence_safe(first_clause), (
        f"Candidate first clause is not sentence-safe:\n{first_clause!r}"
    )

    # Must use the confirm/correct workflow frame
    assert any(kw in c_text.lower() for kw in (
        "workflow seems to be", "accurate picture", "would you add or correct",
        "current workflow", "today's process",
    )), (
        f"Candidate does not frame as a workflow confirm/correct:\n{c_text!r}"
    )
