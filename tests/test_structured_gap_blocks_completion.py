"""
Regression tests for the structured gap model (section_gap_state).

Contract:
- Orchestrator reads section_gap_state ONLY — never draft text.
- Composer renders [NEEDS CLARIFICATION] from gap state (output only).
- section_complete_detected MUST NOT fire when blocking gaps exist.
- advance_section_node MUST NOT advance when blocking gaps exist (even via ITER_CAP).
- draft marker text alone MUST NOT block orchestration when no structured gap exists.
- Structured gap without any draft marker MUST still block orchestration.
"""

from unittest.mock import MagicMock, patch
import uuid


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _make_section(section_id="success_metrics", expected_components=None):
    s = MagicMock()
    s.id = section_id
    s.title = section_id.replace("_", " ").title()
    s.expected_components = expected_components or ["baseline", "target", "measurement_method"]
    return s


def _make_state(
    section_id="success_metrics",
    qa_resolved=True,
    blocking_gaps=None,
    prd_section_draft="",
    iteration=3,
    verdict="",
    recovery_count=0,
):
    """Build a minimal PRDState-like dict for unit testing."""
    # QA store: mark all canonical components resolved when qa_resolved=True
    qa_store = {}
    if qa_resolved:
        qa_store["metric_baseline"] = {
            "section_id": section_id,
            "resolved_subparts": ["baseline", "target", "measurement_method"],
            "contradiction_flagged": False,
        }

    gap_state = {}
    if blocking_gaps is not None:
        gap_state[section_id] = blocking_gaps

    return {
        "thread_id": "t1",
        "run_id": "r1",
        "section_index": 0,
        "iteration": iteration,
        "confirmed_qa_store": qa_store,
        "section_gap_state": gap_state,
        "prd_sections": {section_id: prd_section_draft},
        "remaining_subparts": [],
        "concept_conflicts": [],
        "verdict": verdict,
        "recovery_mode_consecutive_count": recovery_count,
        "overall_score": -1.0,
    }


def _make_blocking_gap(component="target", question="What target should we use?"):
    return {
        "gap_id": str(uuid.uuid4()),
        "section_id": "success_metrics",
        "component": component,
        "question": question,
        "severity": "blocking",
        "source": "reflect_node",
        "resolved": False,
    }


def _make_resolved_gap(component="target"):
    return {
        "gap_id": str(uuid.uuid4()),
        "section_id": "success_metrics",
        "component": component,
        "question": "What target should we use?",
        "severity": "blocking",
        "source": "reflect_node",
        "resolved": True,
    }


# ─── Tests ───────────────────────────────────────────────────────────────────

def test_blocking_gap_prevents_section_complete_without_reading_draft_text():
    """
    _classify_target_confidence must return UNRESOLVED_GAP (not SECTION_COMPLETE)
    when section_gap_state has an unresolved blocking gap, regardless of draft text.
    The orchestrator reads section_gap_state ONLY.
    """
    from graph.nodes import _classify_target_confidence

    section = _make_section()
    blocking_gap = _make_blocking_gap()
    # Draft text is intentionally EMPTY — no [NEEDS CLARIFICATION] markers in draft.
    # The structured gap alone must block completion.
    state = _make_state(
        qa_resolved=True,
        blocking_gaps=[blocking_gap],
        prd_section_draft="",  # No draft markers
    )

    confidence, target, candidates, reason, reason_code = _classify_target_confidence(state, section)

    assert reason_code == "UNRESOLVED_GAP", (
        f"Expected UNRESOLVED_GAP but got {reason_code!r}. "
        "Orchestrator must read section_gap_state, not draft text."
    )
    assert confidence == "STRONG"
    assert target == blocking_gap["component"]


def test_composer_marker_does_not_control_orchestrator():
    """
    [NEEDS CLARIFICATION] text in draft alone must NOT change orchestrator routing
    when no structured gap exists in section_gap_state.
    """
    from graph.nodes import _classify_target_confidence

    section = _make_section()
    # Draft has a [NEEDS CLARIFICATION] marker but section_gap_state is empty.
    state = _make_state(
        qa_resolved=True,
        blocking_gaps=[],  # No structured gaps
        prd_section_draft="The baseline is unclear. [NEEDS CLARIFICATION: target metric]",
    )

    confidence, target, candidates, reason, reason_code = _classify_target_confidence(state, section)

    assert reason_code == "SECTION_COMPLETE", (
        f"Expected SECTION_COMPLETE but got {reason_code!r}. "
        "Composer markers alone must not control orchestrator routing."
    )


def test_resolved_gap_allows_section_complete():
    """
    When all blocking gaps are resolved, _classify_target_confidence should
    return SECTION_COMPLETE (not UNRESOLVED_GAP).
    """
    from graph.nodes import _classify_target_confidence

    section = _make_section()
    resolved_gap = _make_resolved_gap("target")
    state = _make_state(
        qa_resolved=True,
        blocking_gaps=[resolved_gap],
    )

    confidence, target, candidates, reason, reason_code = _classify_target_confidence(state, section)

    assert reason_code == "SECTION_COMPLETE", (
        f"Expected SECTION_COMPLETE but got {reason_code!r}. "
        "Resolved gaps should not block section completion."
    )


def test_reflector_missing_target_creates_structured_gap():
    """
    When reflect_node parses a blocking_gaps entry for 'target' from its JSON block,
    the returned section_gap_state must contain a blocking gap with component='target'.
    """
    from graph.nodes import reflect_node

    # Build a mock reflector LLM response with blocking_gaps JSON
    reflection_text = """
1. COMPLETENESS — 5/10
2. SPECIFICITY — 5/10
3. INTERNAL CONSISTENCY — 8/10
4. IMPLEMENTABILITY — 5/10
5. OVERALL SCORE — 5.0/10

6. REQUIREMENT STATUS
- UNRESOLVED: target accuracy — no numeric target defined

7. REQUIREMENT GAPS
- What accuracy target should we hit for the final automated output?

8. TRIAGE DECISION
TRIAGE: NORMAL ITERATION

VERDICT: REWORK - Missing target accuracy value

```json
{
  "verdict": "REWORK",
  "brief_rationale": "Missing target value",
  "technical_gaps": ["No numeric target defined for mapping accuracy"],
  "user_gaps": ["What accuracy target should we use for the final automated + human-reviewed output?"],
  "confidence": 0.4,
  "blocking_gaps": [{"component": "target", "question": "What accuracy target should we use for the final automated + human-reviewed output?"}]
}
```
"""
    mock_llm = MagicMock()
    mock_response = MagicMock()
    mock_response.content = reflection_text
    mock_llm.invoke.return_value = mock_response

    section = _make_section()
    state = {
        "thread_id": "t1",
        "run_id": "r1",
        "section_index": 0,
        "iteration": 1,
        "section_gap_state": {},
        "section_draft_meta": {},
        "store_version": 1,
        "current_draft": "Draft with missing target.",
        "overall_score": -1.0,
        "recovery_mode_consecutive_count": 0,
        "section_qa_pairs": [],
    }

    with (
        patch("graph.nodes._get_llm", return_value=mock_llm),
        patch("graph.nodes.get_section_by_index", return_value=section),
        patch("graph.nodes.llm_invoke", return_value=mock_response),
        patch("graph.nodes._get_memoized_prd_so_far", return_value=("", "")),
        patch("graph.nodes._build_visual_context_block", return_value=""),
        patch("graph.nodes.REFLECTOR_SYSTEM", "{section_title}{prior_sections_block}{visual_context_block}{global_rigor_block}{expected_components_list}{specificity_guidance}{scoring_interpretation_block}"),
        patch("graph.nodes.REFLECTOR_PRIOR_SECTIONS_BLOCK", ""),
        patch("graph.nodes.GLOBAL_RIGOR_BLOCK", ""),
        patch("graph.nodes.SCORING_INTERPRETATION_BLOCK", ""),
        patch("graph.nodes.log_event"),
    ):
        result = reflect_node(state)

    assert "section_gap_state" in result, "reflect_node must write section_gap_state"
    section_gaps = result["section_gap_state"].get(section.id, [])
    assert len(section_gaps) > 0, f"Expected gaps for {section.id}, got {section_gaps}"
    target_gaps = [g for g in section_gaps if g["component"] == "target"]
    assert target_gaps, f"Expected a 'target' blocking gap, got: {section_gaps}"
    assert not target_gaps[0]["resolved"], "Newly written gap must be resolved=False"
    assert target_gaps[0]["severity"] == "blocking"


def test_iter_cap_cannot_advance_when_blocking_gap_exists():
    """
    advance_section_node with ITER_CAP must return REWORK (no section_index change)
    when section_gap_state has an unresolved blocking gap.
    """
    from graph.nodes import advance_section_node

    section = _make_section()
    blocking_gap = _make_blocking_gap()
    state = _make_state(
        qa_resolved=True,
        blocking_gaps=[blocking_gap],
        iteration=5,  # At cap
        verdict="",   # Not PASS
        recovery_count=0,
    )
    state["section_index"] = 0

    with (
        patch("graph.nodes.get_section_by_index", return_value=section),
        patch("graph.nodes.log_event"),
        patch("graph.nodes.DEFAULT_MAX_RECOVERY_MODE_CONSECUTIVE_ITERATIONS", 3),
        patch("graph.nodes.PRD_SECTIONS", [section, MagicMock()]),
    ):
        result = advance_section_node(state)

    # Must return REWORK, not advance.
    assert result.get("verdict") == "REWORK", (
        f"Expected REWORK (blocked by gap) but got verdict={result.get('verdict')!r}. "
        "ITER_CAP must not override required blocking gaps."
    )
    # section_index must NOT be in result (or must be 0, i.e. unchanged)
    assert result.get("section_index", 0) == 0, (
        "advance_section_node must not increment section_index when blocked by gap."
    )


def test_draft_marker_without_structured_gap_does_not_block():
    """
    [NEEDS CLARIFICATION] marker in draft text alone must not block _classify_target_confidence
    when section_gap_state has no unresolved blocking gaps.
    """
    from graph.nodes import _classify_target_confidence

    section = _make_section()
    state = _make_state(
        qa_resolved=True,
        blocking_gaps=[],
        prd_section_draft="Target: [NEEDS CLARIFICATION: what target?]",
    )

    _, _, _, _, reason_code = _classify_target_confidence(state, section)

    assert reason_code == "SECTION_COMPLETE", (
        f"Got {reason_code!r}. Draft text alone must not block orchestration."
    )


def test_structured_gap_without_draft_marker_still_blocks():
    """
    A blocking gap in section_gap_state must block section completion
    even when the draft text contains NO [NEEDS CLARIFICATION] markers.
    """
    from graph.nodes import _classify_target_confidence

    section = _make_section()
    state = _make_state(
        qa_resolved=True,
        blocking_gaps=[_make_blocking_gap("measurement_method", "How will this be measured?")],
        prd_section_draft="All metrics captured. Accuracy target: 95%. Baseline: 70%.",  # No markers
    )

    _, _, _, _, reason_code = _classify_target_confidence(state, section)

    assert reason_code == "UNRESOLVED_GAP", (
        f"Got {reason_code!r}. Structured gap must block completion even without draft markers."
    )
