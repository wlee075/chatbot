"""tests/test_rich_first_turn_guardrail.py

Regression tests for Rich First-Turn Evidence Guardrail and orchestrator
action-branch mapping correctness (debug_tasks from senior's spec).

Tests:
  T1 – test_rich_first_turn_blocks_seed_question
  T2 – test_moderate_confidence_maps_to_propose_path_not_seed_hint
  T3 – test_summary_never_active_on_session_start
  T4 – test_structured_success_preserves_specific_question (unit)
  T5 – test_evidence_rich_input_yields_confirm_correct_prompt
"""
from __future__ import annotations
import pytest
from config.sections import PRD_SECTIONS
from utils.prd_orchestrator import inference_first_prd_orchestrator


# ── Helpers ───────────────────────────────────────────────────────────────────

def _section(section_id: str):
    """Return the PRDSection object for a given id."""
    for s in PRD_SECTIONS:
        if s.id == section_id:
            return s
    raise ValueError(f"Unknown section_id: {section_id!r}")


def _state(latest_user_turn: str = "", qa_store: dict | None = None) -> dict:
    # NOTE: get_current_snapshot reads from state["messages"], not "conversation_history"
    messages = (
        [{"role": "user", "content": latest_user_turn}] if latest_user_turn else []
    )
    return {
        "confirmed_qa_store":    qa_store or {},
        "messages":              messages,
        "conversation_history":  messages,  # kept for other consumers
        "prd_sections":          {},
        "section_index":         0,
        "iteration":             0,
        "recent_questions":      [],
        "remaining_subparts":    [],
        "concept_conflicts":     [],
    }


# ── T1: Rich first turn must not produce SEED_QUESTION ───────────────────────

def test_rich_first_turn_blocks_seed_question():
    """When the first user message contains ≥3 rich evidence signals
    (pain, failure modes, baseline, metric, mechanism, approval) the orchestrator
    must not recommend ACTION_SEED_QUESTION.

    This is the exact scenario from the senior's report:
    'manual grind aligning inconsistent supplier data', '~82% current accuracy',
    '45 minutes to under 15 seconds', 'failure points: renamed headers, missing GTINs',
    'self-correcting pipeline', 'needs sign-off for logs access'
    """
    rich_turn = (
        "We have a manual grind aligning inconsistent supplier data — renamed headers, "
        "missing GTINs, UOM chaos. We built a self-correcting pipeline but it only does "
        "about 82% accuracy automatically; human validation kicks in below 92%. "
        "The goal is to get batch time from 45 minutes down to under 15 seconds. "
        "We also need sign-off for log access and the review queue."
    )
    state = _state(latest_user_turn=rich_turn)
    section = _section("headliner")  # first interactive section; not in LIVE_PROMPT_SECTIONS

    plan = inference_first_prd_orchestrator(state, section)

    assert plan["recommended_action"] != "ACTION_SEED_QUESTION", (
        f"Rich first-turn must not result in SEED_QUESTION.\n"
        f"Got action={plan['recommended_action']!r}, "
        f"confidence={plan['confidence']!r}, "
        f"guardrail_log check: rich_signal_count should be ≥3"
    )


# ── T2: MODERATE confidence maps to PROPOSE path, not seed hint ───────────────

def test_moderate_confidence_maps_to_propose_path_not_seed_hint():
    """MODERATE confidence (medium inferrer output) must route to PROPOSE_ONE,
    never to SEED_QUESTION, regardless of is_live status.

    Exception: if the section has no live prompt injection AND the Rich First-Turn
    Guardrail is not triggered, the old behaviour (downgrade to SEED) should not
    apply anymore — PROPOSE_CONSTRAINED should be used instead.

    We test this by seeding a state with enough context for MEDIUM confidence
    but starting on a non-live section (headliner).
    """
    # Provide earlier confirmed context so the snapshot has some evidence
    qa_store = {
        "background:iter_0:round_1": {
            "section_id": "background",
            "answer": "Manual supplier data alignment takes 45 min per batch run.",
            "contradiction_flagged": False,
            "version": 1,
        }
    }
    rich_turn = (
        "We have a manual process, ~82% accuracy, and need it under 15 seconds. "
        "The pipeline has failure modes on renamed headers and missing GTINs. "
        "Sign-off required for log access."
    )
    state = _state(latest_user_turn=rich_turn, qa_store=qa_store)
    section = _section("headliner")

    plan = inference_first_prd_orchestrator(state, section)

    # When rich first-turn guardrail fires, action must not be SEED
    if plan.get("recommended_action") == "ACTION_SEED_QUESTION":
        pytest.fail(
            "MODERATE confidence with an evidence-rich turn must not map to SEED_QUESTION.\n"
            f"Got: recommended_action={plan['recommended_action']!r}, "
            f"confidence={plan['confidence']!r}"
        )


# ── T3: Summary section never active on session start ─────────────────────────

def test_summary_never_active_on_session_start():
    """The first interactive section must never be 'summary'.

    'summary' is a derived/report-only section and must not appear in PRD_SECTIONS
    as an interactive elicitation target. This guards against session start
    poisoning where Summary's framing biases the first question into a generic form.
    """
    interactive_ids = [s.id for s in PRD_SECTIONS]
    assert "summary" not in interactive_ids, (
        f"'summary' found in PRD_SECTIONS interactive list: {interactive_ids}. "
        "Summary is a derived-only section and must never be elicited directly."
    )

    # Also confirm the FIRST section is a proper evidence-gathering section
    first_section_id = PRD_SECTIONS[0].id
    assert first_section_id != "summary", (
        f"First interactive section is 'summary' (id={first_section_id!r}). "
        "This poisons session start with generic framing."
    )


# ── T4: Structured success preserves specific (non-generic) question ──────────

def test_structured_success_preserves_specific_question():
    """When the LLM returns a dict (structured extraction status=success),
    the final UI question must reference actual evidence from the user's message,
    not a generic 'what problem is being solved?' fallback.

    This is a unit test on the orchestrator output: when the plan has candidate_items
    populated, the produced question must not be the generic fallback phrase.
    """
    rich_turn = (
        "The main pain is that supplier data batches take 45 minutes manually and "
        "we're only at 82% accuracy with the current self-correcting pipeline."
    )
    state = _state(latest_user_turn=rich_turn)
    section = _section("headliner")

    plan = inference_first_prd_orchestrator(state, section)

    # If candidates are non-empty the prompt must never default to the generic phrase
    if plan.get("candidate_items"):
        # Seed hint should not be generic noise
        seed_hint = plan.get("seed_context_hint", "").lower()
        generic_openers = [
            "what problem is being solved",
            "could you share what you have in mind",
            "tell me more about the problem",
            "i'm still missing some details",
        ]
        for opener in generic_openers:
            assert opener not in seed_hint, (
                f"Seed hint falls back to generic opener when candidates exist.\n"
                f"seed_context_hint={plan['seed_context_hint']!r}"
            )


# ── T5: Evidence-rich input yields confirm/correct prompt ────────────────────

def test_evidence_rich_input_yields_confirm_correct_prompt():
    """When user provides: pain + failure modes + baseline + target latency,
    the orchestrator candidate_items must reference the actual content, not
    be empty (which forces the generic fallback path).

    This tests that the Rich First-Turn Guardrail populates candidates so the
    LLM constraint block contains actual content to anchor the question.
    """
    rich_turn = (
        "manual grind aligning inconsistent supplier data — renamed headers, "
        "missing GTINs, UOM chaos — self-correcting pipeline at 82% accuracy, "
        "human validation below 92%, target 45 minutes to 15 seconds, "
        "sign-off needed for logs access and review queue"
    )
    state = _state(latest_user_turn=rich_turn)
    section = _section("headliner")

    plan = inference_first_prd_orchestrator(state, section)

    # Rich first-turn guardrail must produce at least one candidate item
    assert len(plan.get("candidate_items", [])) >= 1, (
        "Evidence-rich input must yield ≥1 candidate_items to constrain the LLM. "
        f"Got candidate_items={plan.get('candidate_items', [])!r}, "
        f"action={plan.get('recommended_action')!r}"
    )

    # Candidate must reflect actual content (not an empty or stub string)
    candidate_text = plan["candidate_items"][0].lower()
    evidence_words = ["manual", "supplier", "82", "accuracy", "45", "15", "gtin", "sign"]
    matched = [w for w in evidence_words if w in candidate_text]
    assert len(matched) >= 2, (
        f"Candidate must reference actual evidence from the user turn. "
        f"Matched words: {matched!r}\ncandidate={plan['candidate_items'][0]!r}"
    )
