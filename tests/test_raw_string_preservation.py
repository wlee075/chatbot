"""tests/test_raw_string_preservation.py

Tests for the raw LLM string preservation policy introduced in
_normalize_generated_question (Stage O2 fix).

Rule: If the LLM returns a non-empty string that contains a '?', is not a
JSON fragment, and is longer than 20 chars, it is preserved as the question
text rather than replaced by a fallback.
"""
from __future__ import annotations
from unittest.mock import patch, MagicMock
import pytest


# ─── Helpers ────────────────────────────────────────────────────────────────

class DummySection:
    def __init__(self, section_id: str = "problem_statement", title: str = "Problem Statement"):
        self.id = section_id
        self.title = title


def _make_ctx() -> dict:
    return {
        "thread_id": "t-test",
        "run_id": "r-test",
        "node_name": "generate_questions_node",
    }


def _make_orch_plan() -> dict:
    return {
        "recommended_action": "SEED_QUESTION",
        "candidate_items": [],
        "seed_context_hint": "What does the current process look like?",
        "target_section_title": "Problem Statement",
        "is_live_prompt_eligible": True,
        "confidence": "LOW",
    }


_GENERIC_FALLBACK = "generic fallback question from context?"


def _normalize(raw: str, *, orch_plan=None) -> dict:
    """Call _normalize_generated_question with LLM + context-aware fallback mocked."""
    with (
        patch("graph.nodes._get_llm", return_value=MagicMock(model="gemini-2.5-flash")),
        patch("graph.nodes._generate_context_aware_fallback", return_value=_GENERIC_FALLBACK),
    ):
        from graph.nodes import _normalize_generated_question
        norm, _ = _normalize_generated_question(
            raw, {}, _make_ctx(), orch_plan=orch_plan, section=DummySection()
        )
        return norm


# ─── Core preservation tests ────────────────────────────────────────────────

class TestRawStringPreservation:

    def test_raw_llm_string_preserved_when_parse_fails_but_text_is_usable(self):
        """A well-formed question string returned by the LLM must be preserved verbatim
        as the single_next_question rather than replaced by a fallback."""
        usable_raw = (
            "From what you've described, it sounds like the core pain is supplier data "
            "misalignment that errors 8-12% of records. Is that the problem you're trying "
            "to solve, or is there another driver I'm missing?"
        )
        norm = _normalize(usable_raw)
        assert norm["single_next_question"] == usable_raw, (
            f"Usable raw string was discarded. Got: {norm['single_next_question']!r}"
        )

    def test_parser_fallback_does_not_overwrite_usable_raw_question(self):
        """When raw string is usable, parser_fallback must NOT replace it with
        _generate_context_aware_fallback or _orch_plan_to_fallback text."""
        usable_raw = "Does the supplier data mismatch affect all product lines equally?"
        norm = _normalize(usable_raw, orch_plan=_make_orch_plan())
        result = norm["single_next_question"]
        assert result == usable_raw, (
            f"Usable raw overwritten by fallback. Got: {result!r}"
        )

    def test_garbage_json_fragment_is_rejected_and_fallback_used(self):
        """A JSON-like fragment (starts with '{') must be rejected and not preserved."""
        garbage = '{"question_id": "q1", "single_next_question": ...'
        norm = _normalize(garbage, orch_plan=_make_orch_plan())
        result = norm["single_next_question"]
        # Should NOT equal the garbage input
        assert result != garbage
        # Should be non-empty (seed hint or generic fallback)
        assert result.strip() != ""

    def test_empty_string_triggers_fallback_not_preservation(self):
        """An empty raw string must use the fallback, not preserve the empty string."""
        norm = _normalize("", orch_plan=_make_orch_plan())
        result = norm["single_next_question"]
        assert result.strip() != ""

    def test_raw_string_too_short_is_rejected(self):
        """A raw string shorter than 20 chars must be rejected even if it contains '?'."""
        short = "What?"
        norm = _normalize(short, orch_plan=_make_orch_plan())
        result = norm["single_next_question"]
        assert result != short, "Short strings must be rejected — do not preserve them"


# ─── Mutation span shape tests ───────────────────────────────────────────────

class TestMutationLogFields:
    """Verify that the normalized response always has the required shape
    regardless of the path (usable string / garbage / orchestrator fallback)."""

    def test_normalized_response_always_has_question_id(self):
        norm = _normalize("How does the error propagate downstream?")
        assert "question_id" in norm

    def test_normalized_response_always_has_single_next_question(self):
        norm = _normalize("How does the error propagate downstream?")
        assert "single_next_question" in norm
        assert norm["single_next_question"].strip() != ""

    def test_normalized_response_always_has_subparts_list(self):
        norm = _normalize("How does the error propagate downstream?")
        assert "subparts" in norm
        assert isinstance(norm["subparts"], list)

    def test_final_response_assembly_preserves_first_valid_assistant_question(self):
        """When a usable raw string exists, the final assembled question
        must equal or contain the preserved raw string (not be replaced by fallback)."""
        usable = (
            "Given the 8-12% error rate, should the validation pipeline prioritize "
            "speed or precision when auto-correcting supplier records?"
        )
        from graph.nodes import _construct_final_question_text
        norm = _normalize(usable)
        final = _construct_final_question_text(norm, "", {})
        assert usable in final or usable == final, (
            f"Final assembled question did not contain usable raw string.\n"
            f"Expected: {usable!r}\nGot: {final!r}"
        )
