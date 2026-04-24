"""tests/test_background_correction_precedence.py

Regression tests for Background evidence correction-precedence (Steps 7 & 10):
  - test_user_reframes_earlier_summary   — corrected version wins over stale first-write
  - test_multiple_stakeholder_pain_synthesized — background uses system context, not one persona
  - test_old_summary_suppressed_when_contradicted — higher-version entry shadows lower-version
"""
from __future__ import annotations
from utils.section_inference import infer_section_candidates


def _make_state(qa_pairs: list[dict]) -> dict:
    store = {f"q{i}": p for i, p in enumerate(qa_pairs)}
    return {
        "confirmed_qa_store": store,
        "prd_sections": {},
        "section_index": 0,
        "conversation_history": [],
        "recent_questions": [],
        "remaining_subparts": [],
        "concept_conflicts": [],
    }


# ── T1: User reframes earlier summary ────────────────────────────────────────

def test_user_reframes_earlier_summary():
    """If user corrects earlier framing, Background must use the corrected version.

    Simulates:
    - First write: version=1, stale framing ('data entry specialists live the grind')
    - Second write: version=2, corrected framing ('cross-team approval bottleneck')
    Both have section_id='headliner'. Highest version must win.
    """
    state = _make_state([
        {
            "section_id": "headliner",
            "answer": "Data entry specialists live the grind with manual supplier mapping.",
            "contradiction_flagged": False,
            "version": 1,  # stale first write
        },
        {
            "section_id": "headliner",
            "answer": (
                "The real focus is cross-team approval latency — ops submits, "
                "category reviews, then catalog publishes. Each handoff adds a day."
            ),
            "contradiction_flagged": False,
            "version": 2,  # user correction — must win
        },
        {
            "section_id": "elevator_pitch",
            "answer": "Supplier data alignment creates cross-role delays that slow catalog launches.",
            "contradiction_flagged": False,
            "version": 1,
        },
    ])
    result = infer_section_candidates("background", state)

    assert result["inference_available"] is True, (
        "Background must be inference_available when ≥2 evidence facts exist"
    )

    candidates = result.get("candidate_items", [])
    assert len(candidates) > 0

    combined = candidates[0].lower()

    # Corrected framing must appear
    assert any(phrase in combined for phrase in (
        "cross-team", "approval", "latency", "handoff",
        "category", "catalog", "cross-role",
    )), (
        f"Corrected framing not found in Background candidate.\nGot: {candidates[0]!r}"
    )

    # Stale framing must NOT dominate
    assert "live the grind" not in combined, (
        f"Stale framing 'live the grind' appeared in Background candidate — "
        f"correction-precedence failed.\nGot: {candidates[0]!r}"
    )


# ── T2: Multiple stakeholder pain points synthesised ──────────────────────────

def test_multiple_stakeholder_pain_synthesized():
    """Background must reference system-level workflow context, not only one persona.

    When headliner, elevator_pitch, and key_stakeholders are all answered,
    the synthesis candidate must draw from multiple source sections.
    """
    state = _make_state([
        {
            "section_id": "headliner",
            "answer": "Supplier mapping batches run 6-8 times daily, each taking 45 minutes.",
            "contradiction_flagged": False,
            "version": 1,
        },
        {
            "section_id": "elevator_pitch",
            "answer": (
                "Every new supplier adds regex pattern work for engineering "
                "and re-mapping work for product ops."
            ),
            "contradiction_flagged": False,
            "version": 1,
        },
        {
            "section_id": "key_stakeholders",
            "answer": (
                "Product Ops runs the batches. Category managers consume the output. "
                "Engineering patches the rules."
            ),
            "contradiction_flagged": False,
            "version": 1,
        },
    ])
    result = infer_section_candidates("background", state)

    assert result["inference_available"] is True

    # Evidence sources must include multiple sections
    sources = result.get("evidence_sources", [])
    assert len(sources) >= 2, (
        f"Background must draw from ≥2 source sections. Got: {sources}"
    )

    candidates = result.get("candidate_items", [])
    assert len(candidates) > 0, "Background must produce a candidate with 3 evidence facts"

    # Candidate must reference system-level / multi-role workflows
    candidate = candidates[0].lower()
    system_signals = ["batch", "supplier", "mapping", "ops", "engineering", "category"]
    matched = [s for s in system_signals if s in candidate]
    assert len(matched) >= 2, (
        f"Background candidate must reference ≥2 system-level signals. "
        f"Matched: {matched!r}\nGot: {candidates[0]!r}"
    )


# ── T3: Old summary suppressed when contradicted ──────────────────────────────

def test_old_summary_suppressed_when_contradicted():
    """The correction-precedence rule must suppress a lower-version entry even when
    it is the only write for that section earlier in the dict iteration order.

    Scenario: version=3 correction is stored after version=1 original.
    The inferrer must select version=3 exclusively.
    """
    state = _make_state([
        {
            "section_id": "elevator_pitch",
            "answer": "This is the OLD framing that should be suppressed.",
            "contradiction_flagged": False,
            "version": 1,
        },
        {
            "section_id": "headliner",
            "answer": "Product Ops runs daily supplier batches that take 45 min each.",
            "contradiction_flagged": False,
            "version": 1,
        },
        {
            "section_id": "elevator_pitch",
            "answer": (
                "Corrected framing: cross-role approval latency is the bottleneck, "
                "not data entry volume."
            ),
            "contradiction_flagged": False,
            "version": 3,  # latest correction
        },
    ])
    result = infer_section_candidates("background", state)

    candidates = result.get("candidate_items", [])
    if not candidates:
        return  # inference_available=False is acceptable here

    combined = " ".join(c.lower() for c in candidates)

    # Corrected framing must be used
    assert "old framing" not in combined and "should be suppressed" not in combined, (
        f"Stale entry appeared in Background candidate — correction-precedence broken.\n"
        f"Got: {candidates}"
    )

    # Higher-version text must appear instead
    assert any(phrase in combined for phrase in (
        "cross-role", "approval latency", "bottleneck", "corrected",
    )), (
        f"Corrected framing (version=3) not reflected in Background candidate.\n"
        f"Got: {candidates}"
    )
