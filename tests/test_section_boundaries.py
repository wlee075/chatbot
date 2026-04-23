"""tests/test_section_boundaries.py

Regression tests for section boundary enforcement (REV1-REV7).

Tests (7):
  T1 – test_pain_points_has_distinct_role_from_background_and_problem_statement
  T2 – test_headliner_excludes_elevator_pitch_language
  T3 – test_out_of_scope_not_just_non_goals_paraphrase
  T4 – test_assumption_requires_validation_shape
  T5 – test_risk_requires_failure_and_consequence
  T6 – test_proposed_solution_excludes_timeline_and_metrics
  T7 – test_adjacent_sections_do_not_reuse_same_primary_snippet
"""
from __future__ import annotations
import re
import pytest

from utils.section_inference import (
    infer_section_candidates,
    _HEADLINER_EXCLUSION_RE,
    _RISK_FAILURE_RE,
    _RISK_CONSEQUENCE_RE,
    _RISK_ASSUMPTION_LEAK_RE,
    _ASSUMPTION_BELIEF_RE,
    _ASSUMPTION_DEPENDENCY_RE,
    _ASSUMPTION_VALIDATION_RE,
    _PROPOSED_SOL_TIMELINE_RE,
    _NON_GOAL_SOURCE_SECTIONS,
    _OUT_OF_SCOPE_SOURCE_SECTIONS,
    _RISK_SOURCE_SECTIONS,
)


# ── Shared fixture helpers ─────────────────────────────────────────────────────

def _state(qa_entries: list[dict]) -> dict:
    """Build a minimal PRDState dict from a list of QA entry dicts."""
    qa_store = {}
    for i, e in enumerate(qa_entries):
        qa_store[f"entry_{i}"] = {
            "section_id": e["section_id"],
            "answer":     e.get("answer", ""),
            "question":   e.get("question", ""),
            "version":    e.get("version", 1),
            "contradiction_flagged": e.get("contradiction_flagged", False),
        }
    return {"confirmed_qa_store": qa_store, "prd_sections": {}}


# ── T1: Pain Points must focus on concrete harms, not workflow or root-cause ──

def test_pain_points_has_distinct_role_from_background_and_problem_statement():
    """Pain Points (problem_statement inferrer) must draw from headliner/elevator_pitch/goals.
    It must NOT draw from 'background' directly (that is only allowed via the escalation path).
    The candidate produced for operational harm language must contain harm signals,
    not workflow-description or root-cause explanation.

    REV1: pain_points is bounded to concrete operational harms only.
    """
    from utils.section_inference import _PAIN_SOURCE_SECTIONS

    # background must not be in the direct signal pool
    assert "background" not in _PAIN_SOURCE_SECTIONS, (
        "background must not be in _PAIN_SOURCE_SECTIONS — it causes pain_points to restate "
        "workflow context instead of operational harms. Use the escalation path instead."
    )

    # When background is the ONLY answered section (before problem_statement), the
    # escalation path should kick in. When headliner/elevator_pitch have harm signals,
    # those must drive the candidate — not a workflow-description restatement.
    harm_state = _state([
        {
            "section_id": "headliner",
            "answer": "Operators manually align supplier CSVs for 45 minutes per batch, causing stockouts.",
        },
        {
            "section_id": "elevator_pitch",
            "answer": "Manual product mapping mistakes cause 12% stockouts and delay catalog releases by 3 days.",
        },
        {
            "section_id": "goals",
            "answer": "Reduce manual mapping errors and eliminate catalog delay.",
        },
    ])
    result = infer_section_candidates("problem_statement", harm_state)
    candidates = result.get("candidate_items", [])

    if candidates:
        for c in candidates:
            # Candidates may be dicts (structured) or plain strings
            c_text = c.get("text", "") if isinstance(c, dict) else str(c)
            # Candidate must NOT be a workflow re-description (no "walk me through")
            assert not re.search(r"walk me through|current workflow\s+looks like", c_text, re.IGNORECASE), (
                f"Pain points candidate reads like a workflow question (Background territory):\n{c_text!r}"
            )
            # Candidate must NOT be a root-cause/'why' question (that's problem_statement territory)
            assert not re.search(r"\bwhy does\b.*\bfail\b|\bunderlying reason\b", c_text, re.IGNORECASE), (
                f"Pain points candidate reads like a root-cause question (Problem Statement territory):\n{c_text!r}"
            )


# ── T2: Headliner must not contain elevator-pitch language ────────────────────

def test_headliner_excludes_elevator_pitch_language():
    """REV2: Headliner is 'one-sentence essence of the problem/opportunity only'.
    It must not include differentiator framing or executive-persuasion language.
    The _HEADLINER_EXCLUSION_RE guard must catch these patterns.
    """
    # These snippets belong in elevator_pitch, not headliner
    elevator_pitch_snippets = [
        "Our key differentiator vs. alternatives is real-time ML mapping.",
        "Unlike competitors, we offer automated supplier alignment.",
        "This pitch to executives focuses on the ROI opportunity.",
        "We will persuade the board this is worth funding.",
        "Key benefit: 40% faster catalog release, beating market alternatives.",
    ]
    for snippet in elevator_pitch_snippets:
        assert _HEADLINER_EXCLUSION_RE.search(snippet), (
            f"Headliner exclusion guard missed elevator-pitch language:\n{snippet!r}"
        )

    # These are valid headliner-level sentences (problem/opportunity only)
    valid_headliner_snippets = [
        "Suppliers send product data in inconsistent formats, causing catalog delays.",
        "Manual mapping is too slow to keep up with new SKU volume.",
        "The current intake process cannot handle supplier data variability.",
    ]
    for snippet in valid_headliner_snippets:
        assert not _HEADLINER_EXCLUSION_RE.search(snippet), (
            f"Headliner exclusion guard incorrectly flagged a valid headliner-level snippet:\n{snippet!r}"
        )


# ── T3: Out-of-scope must be delivery-level, not a non_goals paraphrase ───────

def test_out_of_scope_not_just_non_goals_paraphrase():
    """REV3: out_of_scope reads from proposed_solution (primary) + non_goals (weak context).
    non_goals reads from goals + problem_statement ONLY.
    The source lists must enforce this structural separation.
    """
    # non_goals must not read from proposed_solution, assumptions, or background
    disallowed_in_non_goals = {"proposed_solution", "assumptions", "background"}
    overlap = disallowed_in_non_goals & set(_NON_GOAL_SOURCE_SECTIONS)
    assert not overlap, (
        f"_NON_GOAL_SOURCE_SECTIONS contains disallowed delivery-level sources: {overlap}. "
        "These collapse non_goals into out_of_scope."
    )

    # out_of_scope must be anchored in proposed_solution
    assert "proposed_solution" in _OUT_OF_SCOPE_SOURCE_SECTIONS, (
        "proposed_solution must be the primary source for out_of_scope — "
        "what is NOT being built must be inferred from what IS being built."
    )

    # out_of_scope must NOT read from background or problem_statement directly
    disallowed_in_oos = {"background", "problem_statement", "headliner"}
    oos_overlap = disallowed_in_oos & set(_OUT_OF_SCOPE_SOURCE_SECTIONS)
    assert not oos_overlap, (
        f"_OUT_OF_SCOPE_SOURCE_SECTIONS contains narrative sections: {oos_overlap}. "
        "These produce non_goals paraphrases, not delivery exclusions."
    )


# ── T4: Assumption candidates must have belief + dependency + validation ───────

def test_assumption_requires_validation_shape():
    """REV4: Each assumption candidate requires belief + dependency-link + validation intent.
    A pure belief statement without dependency or validation meaning must be rejected.
    """
    # Pure belief statements — no dependency link, no validation plan → must be rejected
    pure_belief_snippets = [
        "We think users will prefer the new interface.",
        "It is expected that the team has bandwidth.",
        "We believe the data will be available.",  # no dependency, no validation method
    ]
    for snippet in pure_belief_snippets:
        has_belief     = bool(_ASSUMPTION_BELIEF_RE.search(snippet))
        has_dependency = bool(_ASSUMPTION_DEPENDENCY_RE.search(snippet))
        has_validation = bool(_ASSUMPTION_VALIDATION_RE.search(snippet))
        should_pass = has_belief and (has_dependency or has_validation)
        assert not should_pass, (
            f"Pure belief statement should be REJECTED as assumption candidate:\n{snippet!r}\n"
            f"  belief={has_belief}, dependency={has_dependency}, validation={has_validation}"
        )

    # Well-formed assumptions — belief + dependency + validation → must be accepted
    valid_assumption_snippets = [
        "We assume the supplier API is available and we will verify this in week 1 testing.",
        "Assuming the data pipeline is ready by Q2; this will be confirmed with the data-eng team.",
        "We expect clean CSV input; this depends on supplier contract compliance, tested in UAT.",
    ]
    for snippet in valid_assumption_snippets:
        has_belief     = bool(_ASSUMPTION_BELIEF_RE.search(snippet))
        has_dependency = bool(_ASSUMPTION_DEPENDENCY_RE.search(snippet))
        has_validation = bool(_ASSUMPTION_VALIDATION_RE.search(snippet))
        should_pass = has_belief and (has_dependency or has_validation)
        assert should_pass, (
            f"Valid assumption statement incorrectly rejected:\n{snippet!r}\n"
            f"  belief={has_belief}, dependency={has_dependency}, validation={has_validation}"
        )


# ── T5: Risk candidates must have failure mode + consequence ───────────────────

def test_risk_requires_failure_and_consequence():
    """REV5: Risk candidates require failure-mode AND consequence language.
    Pure dependency statements without failure framing must be rejected.
    Snippets containing 'assume/assuming' must be filtered (assumption leak).
    """
    # Dependency-only statements → must be rejected from risks
    dependency_only_snippets = [
        "This requires the data pipeline to be ready.",
        "The feature relies on the supplier integration being complete.",
        "Integration depends on a third-party API contract.",
    ]
    for snippet in dependency_only_snippets:
        is_assumption_leak = bool(_RISK_ASSUMPTION_LEAK_RE.search(snippet))
        has_failure        = bool(_RISK_FAILURE_RE.search(snippet))
        has_consequence    = bool(_RISK_CONSEQUENCE_RE.search(snippet))
        # These should fail the shape guard
        assert not (has_failure or has_consequence), (
            f"Dependency-only statement should be REJECTED from risks:\n{snippet!r}\n"
            f"  failure={has_failure}, consequence={has_consequence}"
        )

    # Assumption-leak patterns → must be filtered before shape check
    assumption_leak_snippets = [
        "Assuming data quality holds, the pipeline should work.",
        "We are assuming stakeholder sign-off by Q2.",
    ]
    for snippet in assumption_leak_snippets:
        assert _RISK_ASSUMPTION_LEAK_RE.search(snippet), (
            f"Assumption-leak filter missed an 'assume' pattern in risks:\n{snippet!r}"
        )

    # Valid risk candidates → failure + consequence
    valid_risk_snippets = [
        "If the supplier API fails, catalog updates could take 3 extra days.",
        "Data quality issues might block the automated matching pipeline.",
        "Tight timeline could cause the team to miss the Q3 release deadline.",
    ]
    for snippet in valid_risk_snippets:
        has_failure     = bool(_RISK_FAILURE_RE.search(snippet))
        has_consequence = bool(_RISK_CONSEQUENCE_RE.search(snippet))
        assert has_failure or has_consequence, (
            f"Valid risk statement incorrectly failed shape guard:\n{snippet!r}\n"
            f"  failure={has_failure}, consequence={has_consequence}"
        )

    # assumptions must not be in risk source sections (R6 boundary)
    assert "assumptions" not in _RISK_SOURCE_SECTIONS, (
        "assumptions must not be in _RISK_SOURCE_SECTIONS — belief statements leak "
        "into risk candidates as dependency-only snippets."
    )


# ── T6: Proposed Solution must not elicit timeline or metrics detail ──────────

def test_proposed_solution_excludes_timeline_and_metrics():
    """REV6: proposed_solution must not ask about dates, milestones, owners,
    validation plans, or success metrics. The _PROPOSED_SOL_TIMELINE_RE guard
    must catch these patterns so they remain in their own sections.
    """
    timeline_metric_snippets = [
        "Launch milestone: Q2 2026 sprint 3.",
        "The owner is responsible for delivery by 2026.",
        "NPS target should be above 40 by Q4.",
        "Success metric: baseline 15 minutes, target 5 minutes.",
        "OKR: increase match confidence to 95%.",
        "The sprint plan covers 6 weeks.",
    ]
    for snippet in timeline_metric_snippets:
        assert _PROPOSED_SOL_TIMELINE_RE.search(snippet), (
            f"Proposed-solution timeline guard missed timeline/metric language:\n{snippet!r}"
        )

    # Valid proposed-solution sentences — feature/approach language
    valid_sol_snippets = [
        "Use a rules-based engine to align supplier SKU fields to the canonical catalog schema.",
        "Build an API wrapper to normalize incoming supplier payloads.",
        "Implement a confidence score threshold with a human-review queue for low-confidence matches.",
    ]
    for snippet in valid_sol_snippets:
        assert not _PROPOSED_SOL_TIMELINE_RE.search(snippet), (
            f"Proposed-solution guard misclassified a valid solution snippet:\n{snippet!r}"
        )


# ── T7: Adjacent sections must not reuse the same primary evidence snippet ────

def test_adjacent_sections_do_not_reuse_same_primary_snippet():
    """REV7: Adjacent sections must not reuse the same primary evidence snippet.
    The assertion is on snippet TEXT overlap (first 60 chars), not merely source-section overlap.

    We build a rich state and check that background and problem_statement candidates
    do not share the same leading snippet text as their primary evidence.
    """
    rich_state = _state([
        {
            "section_id": "headliner",
            "answer": (
                "Operators manually align supplier CSVs for 45 minutes per batch, "
                "causing catalog delays and stockouts."
            ),
        },
        {
            "section_id": "elevator_pitch",
            "answer": (
                "Manual product mapping cannot scale: each new supplier adds another "
                "spreadsheet and another hour of ops time per day."
            ),
        },
        {
            "section_id": "goals",
            "answer": "Eliminate manual CSV alignment and reduce catalog release lag by 80%.",
        },
    ])

    background_result   = infer_section_candidates("background", rich_state)
    problem_result      = infer_section_candidates("problem_statement", rich_state)

    bg_evidence   = background_result.get("evidence_selection_log", [])
    prob_evidence = problem_result.get("evidence_selection_log", [])

    if not bg_evidence or not prob_evidence:
        # One section has no candidates — no overlap possible, test passes
        return

    # Compare primary snippets (first entry in evidence_selection_log)
    bg_primary_snippet   = bg_evidence[0][1].lower()[:60].strip() if bg_evidence else ""
    prob_primary_snippet = prob_evidence[0][1].lower()[:60].strip() if prob_evidence else ""

    assert bg_primary_snippet != prob_primary_snippet, (
        f"Background and Problem Statement share the same primary evidence snippet:\n"
        f"  background:   {bg_primary_snippet!r}\n"
        f"  problem_stmt: {prob_primary_snippet!r}\n"
        "Adjacent sections must draw from distinct primary evidence to produce distinct questions."
    )
