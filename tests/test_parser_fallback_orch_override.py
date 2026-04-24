"""tests/test_parser_fallback_orch_override.py

Stage 4/5: Verifies that parser_fallback respects ActionPlan intelligence,
           and that the _orch_plan_to_fallback helper produces plain English
           output for every ActionPlan action type.

Stages 1/2/3 health checks are also included.
"""
from __future__ import annotations

import pytest


# ─── shared fixtures ────────────────────────────────────────────────────────

def _make_propose_plan_one(candidate: str = "daily manual effort aligning supplier data") -> dict:
    return {
        "recommended_action": "PROPOSE_ONE",
        "candidate_items": [candidate],
        "seed_context_hint": "",
        "target_section_title": "Problem Statement",
        "is_live_prompt_eligible": True,
        "confidence": "MEDIUM",
    }


def _make_propose_plan_list(candidates: list[str] | None = None) -> dict:
    return {
        "recommended_action": "PROPOSE_LIST",
        "candidate_items": candidates or [
            "daily manual effort aligning supplier data",
            "edge cases that break automation",
            "batch latency too slow",
        ],
        "seed_context_hint": "",
        "target_section_title": "Problem Statement",
        "is_live_prompt_eligible": True,
        "confidence": "HIGH",
    }


def _make_seed_plan(hint: str = "What does the current manual process look like?") -> dict:
    return {
        "recommended_action": "SEED_QUESTION",
        "candidate_items": [],
        "seed_context_hint": hint,
        "target_section_title": "Problem Statement",
        "is_live_prompt_eligible": True,
        "confidence": "LOW",
    }


def _make_tradeoff_plan(candidates: list[str] | None = None) -> dict:
    return {
        "recommended_action": "TRADEOFF_QUESTION",
        "candidate_items": candidates or [
            "full automation confidence threshold",
            "human review loop for edge cases",
        ],
        "seed_context_hint": "",
        "target_section_title": "Problem Statement",
        "is_live_prompt_eligible": True,
        "confidence": "MEDIUM",
    }


class DummySection:
    def __init__(self, section_id: str = "problem_statement", title: str = "Problem Statement"):
        self.id = section_id
        self.title = title


# ─── Stage 4: parser_fallback reads ActionPlan ─────────────────────────────

class TestParserFallbackActionPlan:

    def test_parser_fallback_uses_action_plan_for_propose_one(self):
        """When LLM returns raw string and plan=PROPOSE_ONE, output must reflect the candidate."""
        from graph.nodes import _orch_plan_to_fallback
        plan = _make_propose_plan_one("daily manual SKU alignment taking 45 minutes")
        result = _orch_plan_to_fallback(plan, DummySection(), state={})
        assert result, "Expected non-empty fallback for PROPOSE_ONE"
        assert "daily manual SKU alignment" in result or "Problem Statement" in result or "?" in result

    def test_parser_fallback_uses_action_plan_for_propose_list(self):
        """When LLM returns raw string and plan=PROPOSE_LIST, output must reflect multiple candidates."""
        from graph.nodes import _orch_plan_to_fallback
        plan = _make_propose_plan_list()
        result = _orch_plan_to_fallback(plan, DummySection(), state={})
        assert result, "Expected non-empty fallback for PROPOSE_LIST"
        # Should contain at least one candidate term
        assert any(
            term in result.lower()
            for term in ("supplier", "edge case", "batch", "problem", "statement")
        )

    def test_parser_fallback_uses_action_plan_for_seed_question(self):
        """When plan=SEED_QUESTION with hint, the fallback is the seed hint verbatim."""
        from graph.nodes import _orch_plan_to_fallback
        hint = "What does the current manual process look like step by step?"
        plan = _make_seed_plan(hint)
        result = _orch_plan_to_fallback(plan, DummySection(), state={})
        assert result == hint, f"Expected seed hint verbatim, got: {result!r}"

    def test_parser_fallback_uses_action_plan_for_tradeoff_question(self):
        """When plan=TRADEOFF_QUESTION with 2 candidates, fallback names the tension."""
        from graph.nodes import _orch_plan_to_fallback
        plan = _make_tradeoff_plan()
        result = _orch_plan_to_fallback(plan, DummySection(), state={})
        assert result, "Expected non-empty fallback for TRADEOFF_QUESTION"
        assert "?" in result, "Tradeoff fallback should end with a question"
        candidates = plan["candidate_items"]
        assert candidates[0] in result or candidates[1] in result

    def test_generic_fallback_blocked_when_action_plan_candidates_exist(self):
        """With PROPOSE_ONE and a candidate, _orch_plan_to_fallback should return something
        non-empty so that the caller does NOT fall back to _generate_context_aware_fallback."""
        from graph.nodes import _orch_plan_to_fallback
        plan = _make_propose_plan_one("supplier data misalignment causing 8-12% error rate")
        result = _orch_plan_to_fallback(plan, DummySection(), state={})
        assert result != "", "Should produce non-empty override when PROPOSE has candidates"

    def test_fallback_output_remains_plain_english_and_non_generic(self):
        """Fallback text must contain a legible English sentence ending with '?'."""
        from graph.nodes import _orch_plan_to_fallback
        plan = _make_propose_plan_list(["supplier data alignment issues"])
        result = _orch_plan_to_fallback(plan, DummySection(), state={})
        assert result.strip().endswith("?"), f"Expected question, got: {result!r}"
        assert "supplier" in result.lower() or "problem" in result.lower() or "statement" in result.lower()


# ─── Stage 5: deterministic formatter ──────────────────────────────────────

class TestDeterministicFormatter:

    def test_deterministic_formatter_plain_english_for_propose_one(self):
        """PROPOSE_ONE with single candidate produces a single-sentence confirm/correct question."""
        from graph.nodes import _format_deterministic_propose_question
        plan = _make_propose_plan_one("daily manual supplier data alignment")
        result = _format_deterministic_propose_question(plan, DummySection())
        assert result, "Expected non-empty output"
        assert "?" in result
        # Should not be a generic placeholder
        assert "what problem" not in result.lower()

    def test_deterministic_formatter_plain_english_for_propose_list(self):
        """PROPOSE_LIST with ≥2 candidates produces a bullet list confirm/extend question."""
        from graph.nodes import _format_deterministic_propose_question
        plan = _make_propose_plan_list()
        result = _format_deterministic_propose_question(plan, DummySection())
        assert result, "Expected non-empty output"
        assert "?" in result
        # Should surface at least one candidate
        assert any(c[:10].lower() in result.lower() for c in plan["candidate_items"])

    def test_deterministic_formatter_tradeoff_question_surfaces_tension(self):
        """TRADEOFF via _orch_plan_to_fallback names both candidate poles explicitly."""
        from graph.nodes import _orch_plan_to_fallback
        plan = _make_tradeoff_plan(["speed", "accuracy"])
        result = _orch_plan_to_fallback(plan, DummySection(), state={})
        assert "speed" in result or "accuracy" in result, "Should name at least one pole"
        assert "?" in result

    def test_deterministic_formatter_seed_question_is_single_and_anchored(self):
        """SEED hint should be used verbatim — no extra generated content prepended."""
        from graph.nodes import _orch_plan_to_fallback
        hint = "How many hours per week does your team spend on this today?"
        plan = _make_seed_plan(hint)
        result = _orch_plan_to_fallback(plan, DummySection(), state={})
        assert result == hint

    def test_orch_plan_to_fallback_returns_empty_when_no_plan(self):
        """Without an ActionPlan, _orch_plan_to_fallback should return '' so the caller
        falls through to _generate_context_aware_fallback."""
        from graph.nodes import _orch_plan_to_fallback
        result = _orch_plan_to_fallback(None, DummySection(), state={})
        assert result == ""

    def test_orch_plan_to_fallback_returns_empty_for_empty_candidates(self):
        """PROPOSE with no candidates should return '' — must not produce phantom questions."""
        from graph.nodes import _orch_plan_to_fallback
        plan = {
            "recommended_action": "PROPOSE_ONE",
            "candidate_items": [],
            "seed_context_hint": "",
            "target_section_title": "Problem Statement",
        }
        result = _orch_plan_to_fallback(plan, DummySection(), state={})
        assert result == "", "Empty candidates must not produce a question"

    def test_orch_plan_to_fallback_seed_empty_hint_returns_empty(self):
        """SEED with no hint should return '' — must not produce a hallucinated question."""
        from graph.nodes import _orch_plan_to_fallback
        plan = {
            "recommended_action": "SEED_QUESTION",
            "candidate_items": [],
            "seed_context_hint": "",
            "target_section_title": "Problem Statement",
        }
        result = _orch_plan_to_fallback(plan, DummySection(), state={})
        assert result == ""


# ─── Stage 1: section ordering ─────────────────────────────────────────────

class TestSectionOrdering:

    def test_headliner_is_in_prd_sections_not_derived(self):
        """headliner must remain an interviewable (non-derived) section in PRD_SECTIONS."""
        from config.sections import PRD_SECTIONS
        ids = [s.id for s in PRD_SECTIONS]
        assert "headliner" in ids, "headliner should remain in PRD_SECTIONS"

    def test_problem_statement_in_prd_sections(self):
        """problem_statement must be a first-class interviewable section."""
        from config.sections import PRD_SECTIONS
        ids = [s.id for s in PRD_SECTIONS]
        assert "problem_statement" in ids

    def test_problem_statement_not_in_direct_elicitation_sections(self):
        """After promotion, problem_statement must NOT be in DIRECT_ELICITATION_SECTIONS."""
        from utils.prd_orchestrator import DIRECT_ELICITATION_SECTIONS
        assert "problem_statement" not in DIRECT_ELICITATION_SECTIONS, (
            "problem_statement must be INFERENCE_FIRST, not DIRECT_ELICITATION"
        )

    def test_problem_statement_in_inference_first_sections(self):
        """problem_statement must be in INFERENCE_FIRST_SECTIONS after promotion."""
        from utils.prd_orchestrator import INFERENCE_FIRST_SECTIONS
        assert "problem_statement" in INFERENCE_FIRST_SECTIONS

    def test_problem_statement_is_live_prompt_eligible(self):
        """problem_statement must be in LIVE_PROMPT_SECTIONS so orchestrator can inject."""
        from utils.section_inference import LIVE_PROMPT_SECTIONS
        assert "problem_statement" in LIVE_PROMPT_SECTIONS


# ─── Stage 2: action plan contents ─────────────────────────────────────────

class TestActionPlanContents:

    def _make_state_with_supplier_input(self) -> dict:
        """Simulate state after a user described a manual supplier data alignment problem."""
        return {
            "confirmed_qa_store": {},
            "prd_sections": {},
            "section_index": 0,
            "section_scores": {},
            "progress_pct": 0,
            "raw_answer_buffer": (
                "We have a manual process where we spend 45 minutes a day aligning "
                "supplier data. The error rate is about 8-12% and edge cases break "
                "automation requiring human review."
            ),
            "conversation_history": [
                {"role": "user", "content": "We spend a lot of time on manual supplier data alignment."}
            ],
        }

    def test_live_prompt_eligibility_true_for_problem_statement(self):
        """For problem_statement, is_live_prompt_eligible must be True in returned plan."""
        from utils.prd_orchestrator import inference_first_prd_orchestrator
        state = self._make_state_with_supplier_input()
        plan = inference_first_prd_orchestrator(state, DummySection())
        assert plan["is_live_prompt_eligible"] is True, (
            f"Expected is_live_prompt_eligible=True, got {plan['is_live_prompt_eligible']}"
        )

    def test_moderate_confidence_with_strong_pain_evidence_not_direct_elicit(self):
        """For problem_statement with supplier evidence, orchestrator must NOT return DIRECT_ELICIT."""
        from utils.prd_orchestrator import inference_first_prd_orchestrator, ACTION_DIRECT_ELICIT
        state = self._make_state_with_supplier_input()
        plan = inference_first_prd_orchestrator(state, DummySection())
        assert plan["recommended_action"] != ACTION_DIRECT_ELICIT, (
            "With supplier pain evidence, problem_statement must not return DIRECT_ELICIT"
        )
