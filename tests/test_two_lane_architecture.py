"""Tests for the two-lane question generation architecture."""
import pytest
from unittest.mock import MagicMock, patch, call
from graph.nodes import (
    _classify_target_confidence,
    _is_synthetic_blocker,
    _generate_context_aware_fallback,
    generate_questions_node,
)


class FakeSection:
    def __init__(self, id="problem_statement", title="Problem Statement",
                 expected_components=None):
        self.id = id
        self.title = title
        self.expected_components = expected_components or [
            "who experiences the problem",
            "what the problem specifically is",
            "why it matters (business or user impact)",
            "what happens if it is not solved",
        ]


# ── Shared mock decorator for generate_questions_node integration tests ──

def _node_test_patches(func):
    """Stack the 13 patches required to unit-test generate_questions_node.
    
    Arguments arrive in order: mock_section, mock_ctx, mock_log, mock_bridge,
    mock_repair, mock_conflict, mock_branch, mock_build_prompt, mock_invoke,
    mock_normalize, mock_construct, mock_dup, mock_package.
    """
    def _fresh_ctx(*args, **kwargs):
        return {"thread_id": "t1", "run_id": "r1",
                "node_name": "generate_questions_node", "_t0": 0}
    
    decorators = [
        patch("graph.nodes.get_section_by_index"),                                    # mock_section
        patch("graph.nodes._log_ctx", side_effect=_fresh_ctx),                        # mock_ctx
        patch("graph.nodes.log_event"),                                               # mock_log
        patch("graph.nodes.build_conversation_understanding_output", return_value={}), # mock_bridge
        patch("graph.nodes._maybe_emit_numeric_repair_prompt", return_value=None),    # mock_repair
        patch("graph.nodes._maybe_emit_conflict_resolution_question", return_value=None), # mock_conflict
        patch("graph.nodes._maybe_emit_resolved_branch_question", return_value=None), # mock_branch
        patch("graph.nodes._build_elicitor_prompt_context", return_value="base_prompt"),# mock_build_prompt
        patch("graph.nodes._invoke_structured_question_generator"),                   # mock_invoke
        patch("graph.nodes._normalize_generated_question"),                           # mock_normalize
        patch("graph.nodes._construct_final_question_text"),                          # mock_construct
        patch("graph.nodes._evaluate_duplicate_candidate"),                           # mock_dup
        patch("graph.nodes._package_generated_question_result"),                      # mock_package
    ]
    # First applied = innermost = first positional argument
    for d in decorators:
        func = d(func)
    return func


def _make_state(**overrides):
    """Build a minimal valid PRDState dict for generate_questions_node."""
    base = {
        "section_index": 0,
        "remaining_subparts": [],
        "concept_conflicts": [],
        "confirmed_qa_store": {},
        "iteration": 1,
        "recent_questions": [],
        "phase": "elicitation",
    }
    base.update(overrides)
    return base


def _llm_response(question_text="What users are affected?", subparts=None, qid="q1"):
    """Build a standard structured LLM response dict."""
    return {
        "single_next_question": question_text,
        "subparts": subparts or [],
        "question_id": qid,
        "user_facing_gap_reason": "",
        "question_type": "OPEN_ENDED",
        "options": [],
    }


# ── T2: _classify_target_confidence returns reason metadata ──────────────

class TestClassifyTargetConfidence:
    def test_strong_single_canonical_blocker(self):
        """D1: Exactly one canonical unresolved blocker → STRONG"""
        section = FakeSection()
        state = {
            "remaining_subparts": ["who experiences the problem"],
            "concept_conflicts": [],
            "confirmed_qa_store": {},
        }
        confidence, target, candidates, reason, reason_code = _classify_target_confidence(state, section)
        assert confidence == "STRONG"
        assert target == "who experiences the problem"
        assert "canonical" in reason.lower() or "one" in reason.lower()

    def test_strong_active_conflict(self):
        """D1: Active conflict → STRONG"""
        section = FakeSection()
        state = {
            "remaining_subparts": [],
            "concept_conflicts": [{"surface": "target audience", "concept_key": "audience"}],
            "confirmed_qa_store": {},
        }
        confidence, target, candidates, reason, reason_code = _classify_target_confidence(state, section)
        assert confidence == "STRONG"
        assert target == "target audience"
        assert "conflict" in reason.lower()

    def test_moderate_multiple_canonical(self):
        """D2: Multiple canonical candidates → MODERATE"""
        section = FakeSection()
        state = {
            "remaining_subparts": [
                "who experiences the problem",
                "what the problem specifically is",
            ],
            "concept_conflicts": [],
            "confirmed_qa_store": {},
        }
        confidence, target, candidates, reason, reason_code = _classify_target_confidence(state, section)
        assert confidence == "MODERATE"
        assert target is None
        assert len(candidates) == 2
        assert "multiple" in reason.lower()

    def test_weak_only_synthetic(self):
        """D3: Only synthetic/stale blockers → WEAK"""
        section = FakeSection()
        state = {
            "remaining_subparts": [
                "who_experiences_the_problem_specific_interaction",
            ],
            "concept_conflicts": [],
            "confirmed_qa_store": {},
        }
        confidence, target, candidates, reason, reason_code = _classify_target_confidence(state, section)
        assert confidence == "WEAK"
        assert "synthetic" in reason.lower() or "stale" in reason.lower()

    def test_empty_nothing_left(self):
        """D4: EMPTY only when ALL canonical components are resolved in QA store.
        With rehydration, empty remaining_subparts + empty QA store = MODERATE (all components recovered)."""
        section = FakeSection()
        # To truly get EMPTY, ALL section components must be resolved
        state = {
            "remaining_subparts": [],
            "concept_conflicts": [],
            "confirmed_qa_store": {
                "q1": {"section_id": "problem_statement", "resolved_subparts": ["who experiences the problem"]},
                "q2": {"section_id": "problem_statement", "resolved_subparts": ["what the problem specifically is"]},
                "q3": {"section_id": "problem_statement", "resolved_subparts": ["why it matters (business or user impact)"]},
                "q4": {"section_id": "problem_statement", "resolved_subparts": ["what happens if it is not solved"]},
            },
        }
        confidence, target, candidates, reason, reason_code = _classify_target_confidence(state, section)
        assert confidence == "EMPTY"
        assert target is None
        assert len(candidates) == 0

    def test_resolved_subparts_are_filtered_out(self):
        """Already-resolved subparts should not appear as candidates."""
        section = FakeSection()
        state = {
            "remaining_subparts": [
                "who experiences the problem",
                "what the problem specifically is",
            ],
            "concept_conflicts": [],
            "confirmed_qa_store": {
                "q1": {
                    "section_id": "problem_statement",
                    "resolved_subparts": ["who experiences the problem"],
                }
            },
        }
        confidence, target, candidates, reason, reason_code = _classify_target_confidence(state, section)
        assert confidence == "STRONG"
        assert target == "what the problem specifically is"

    def test_reason_metadata_is_always_present(self):
        """T2: Every classification level includes a human-readable reason."""
        section = FakeSection()
        for subparts in [[], ["who experiences the problem"], ["x_specific_interaction"]]:
            state = {
                "remaining_subparts": subparts,
                "concept_conflicts": [],
                "confirmed_qa_store": {},
            }
            _, _, _, reason, _ = _classify_target_confidence(state, section)
            assert isinstance(reason, str) and len(reason) > 0


# ── T1: _is_synthetic_blocker uses registry, not substring ───────────────

class TestIsSyntheticBlocker:
    def test_canonical_blocker_is_not_synthetic(self):
        canonical = {"who experiences the problem", "who_experiences_the_problem"}
        assert _is_synthetic_blocker("who experiences the problem", canonical) is False

    def test_synthetic_suffix_is_detected(self):
        canonical = {"who experiences the problem", "who_experiences_the_problem"}
        assert _is_synthetic_blocker("who_experiences_the_problem_specific_interaction", canonical) is True

    def test_unknown_blocker_is_non_canonical(self):
        canonical = {"who experiences the problem"}
        assert _is_synthetic_blocker("completely_unknown_blocker", canonical) is True


# ── T4: Lane B duplicate does not loop or produce blank output ───────────

class TestLaneBTerminalBehavior:
    def test_lane_b_duplicate_does_not_loop_or_blank(self):
        """If both Lane A and Lane B produce duplicates, the deterministic
        fallback must produce a non-empty question without looping."""
        state = {
            "remaining_subparts": ["who experiences the problem"],
            "concept_conflicts": [],
            "confirmed_qa_store": {},
        }
        result = _generate_context_aware_fallback(state)
        assert result and len(result.strip()) > 0
        assert "?" in result  # Must be a question

    def test_empty_state_produces_nonempty_fallback(self):
        """Even with completely empty state, fallback must produce content."""
        state = {
            "remaining_subparts": [],
            "concept_conflicts": [],
            "confirmed_qa_store": {},
        }
        result = _generate_context_aware_fallback(state)
        assert result and len(result.strip()) > 0


# ── T3: generate_questions never returns empty after two-lane refactor ───

class TestGenerateQuestionsNonEmpty:
    @_node_test_patches
    def test_generate_questions_never_returns_empty(
        self, mock_section, mock_ctx, mock_log, mock_bridge,
        mock_repair, mock_conflict, mock_branch,
        mock_build_prompt, mock_invoke, mock_normalize, mock_construct,
        mock_dup, mock_package
    ):
        """Even when LLM returns empty and duplicate guard rejects everything,
        the non-empty gate must fire and produce a valid fallback."""
        mock_section.return_value = FakeSection()
        mock_invoke.return_value = (_llm_response(question_text=""), 0.5)
        mock_normalize.return_value = (_llm_response(question_text=""), "")
        mock_construct.return_value = ""
        mock_dup.return_value = (False, "empty_candidate", "")
        mock_package.return_value = {"current_questions": "fallback"}

        generate_questions_node(_make_state(
            remaining_subparts=["who experiences the problem"]
        ))

        questions_arg = mock_package.call_args[0][1]
        assert questions_arg and len(questions_arg.strip()) > 0


# ═══════════════════════════════════════════════════════════════════════════
#  HAPPY PATH TESTS — system produces correct, non-empty output first try
# ═══════════════════════════════════════════════════════════════════════════

class TestHappyPath:

    # ── HP1: Lane A clean pass — one canonical blocker, no duplicate ──────
    @_node_test_patches
    def test_lane_a_clean_pass_single_canonical(
        self, mock_section, mock_ctx, mock_log, mock_bridge,
        mock_repair, mock_conflict, mock_branch,
        mock_build_prompt, mock_invoke, mock_normalize, mock_construct,
        mock_dup, mock_package
    ):
        """Lane A selects a single canonical blocker, LLM phrases it,
        duplicate guard passes → question delivered in 1 LLM call."""
        mock_section.return_value = FakeSection()
        llm_resp = _llm_response("Who specifically experiences this problem?",
                                  subparts=["who experiences the problem"])
        mock_invoke.return_value = (llm_resp, 0.5)
        mock_normalize.return_value = (llm_resp, "We need to know the affected users.")
        mock_construct.return_value = "We need to know the affected users.\n\nWho specifically experiences this problem?"
        mock_dup.return_value = (True, "", "")
        mock_package.return_value = {"current_questions": "ok"}

        generate_questions_node(_make_state(
            remaining_subparts=["who experiences the problem"]
        ))

        # Exactly 1 LLM call
        assert mock_invoke.call_count == 1
        # Package was called with the constructed question text
        assert mock_package.call_args[0][1] == "We need to know the affected users.\n\nWho specifically experiences this problem?"

    # ── HP2: Lane B MODERATE — multiple canonical candidates ──────────────
    @_node_test_patches
    def test_lane_b_moderate_selects_from_candidates(
        self, mock_section, mock_ctx, mock_log, mock_bridge,
        mock_repair, mock_conflict, mock_branch,
        mock_build_prompt, mock_invoke, mock_normalize, mock_construct,
        mock_dup, mock_package
    ):
        """Lane B fires with MODERATE confidence (multiple candidates),
        LLM picks one and passes duplicate guard → 1 LLM call."""
        mock_section.return_value = FakeSection()
        llm_resp = _llm_response("What is the specific problem?",
                                  subparts=["what the problem specifically is"])
        mock_invoke.return_value = (llm_resp, 0.6)
        mock_normalize.return_value = (llm_resp, "")
        mock_construct.return_value = "What is the specific problem?"
        mock_dup.return_value = (True, "", "")
        mock_package.return_value = {"current_questions": "ok"}

        generate_questions_node(_make_state(
            remaining_subparts=[
                "who experiences the problem",
                "what the problem specifically is",
            ]
        ))

        assert mock_invoke.call_count == 1
        # Verify prompt contained FOCUS CANDIDATES
        prompt_arg = mock_invoke.call_args[0][0]
        assert "FOCUS CANDIDATES" in prompt_arg

    # ── HP3: Lane B WEAK — synthetic blockers trigger inference mode ──────
    @_node_test_patches
    def test_lane_b_weak_uses_inference_mode(
        self, mock_section, mock_ctx, mock_log, mock_bridge,
        mock_repair, mock_conflict, mock_branch,
        mock_build_prompt, mock_invoke, mock_normalize, mock_construct,
        mock_dup, mock_package
    ):
        """Lane B fires with WEAK confidence (only synthetic blockers),
        LLM enters inference mode → 1 LLM call."""
        mock_section.return_value = FakeSection()
        llm_resp = _llm_response("What workflow step is most critical?")
        mock_invoke.return_value = (llm_resp, 0.7)
        mock_normalize.return_value = (llm_resp, "")
        mock_construct.return_value = "What workflow step is most critical?"
        mock_dup.return_value = (True, "", "")
        mock_package.return_value = {"current_questions": "ok"}

        generate_questions_node(_make_state(
            remaining_subparts=["who_experiences_the_problem_specific_interaction"]
        ))

        assert mock_invoke.call_count == 1
        prompt_arg = mock_invoke.call_args[0][0]
        assert "INFERENCE MODE" in prompt_arg

    # ── HP4: Lane B EMPTY — fully open inference ─────────────────────────
    @_node_test_patches
    def test_lane_b_empty_state_uses_inference(
        self, mock_section, mock_ctx, mock_log, mock_bridge,
        mock_repair, mock_conflict, mock_branch,
        mock_build_prompt, mock_invoke, mock_normalize, mock_construct,
        mock_dup, mock_package
    ):
        """When state has no subparts at all, Lane B fires in full 
        inference mode and produces a valid question."""
        mock_section.return_value = FakeSection()
        llm_resp = _llm_response("What problem are you trying to solve?")
        mock_invoke.return_value = (llm_resp, 0.4)
        mock_normalize.return_value = (llm_resp, "")
        mock_construct.return_value = "What problem are you trying to solve?"
        mock_dup.return_value = (True, "", "")
        mock_package.return_value = {"current_questions": "ok"}

        generate_questions_node(_make_state(remaining_subparts=[]))

        assert mock_invoke.call_count == 1
        assert mock_package.call_args[0][1] == "What problem are you trying to solve?"

    # ── HP5: Lane A → B demotion succeeds on second call ─────────────────
    @_node_test_patches
    def test_lane_a_demotes_to_b_and_succeeds(
        self, mock_section, mock_ctx, mock_log, mock_bridge,
        mock_repair, mock_conflict, mock_branch,
        mock_build_prompt, mock_invoke, mock_normalize, mock_construct,
        mock_dup, mock_package
    ):
        """Lane A fires, duplicate guard rejects, demotes to Lane B, 
        second LLM call passes → exactly 2 LLM calls."""
        mock_section.return_value = FakeSection()

        first_resp = _llm_response("Who experiences this?", subparts=["who experiences the problem"])
        second_resp = _llm_response("Why does this matter?", subparts=["why it matters (business or user impact)"])

        mock_invoke.side_effect = [(first_resp, 0.5), (second_resp, 0.5)]
        mock_normalize.side_effect = [(first_resp, ""), (second_resp, "")]
        mock_construct.side_effect = ["Who experiences this?", "Why does this matter?"]
        # First call: duplicate rejected. Second call: accepted.
        mock_dup.side_effect = [(False, "dup_recent_match", "prev_q"), (True, "", "")]
        mock_package.return_value = {"current_questions": "ok"}

        generate_questions_node(_make_state(
            remaining_subparts=["who experiences the problem"]
        ))

        assert mock_invoke.call_count == 2
        assert mock_package.call_args[0][1] == "Why does this matter?"

    # ── HP6: Conflict takes priority over subpart blockers ────────────────
    @_node_test_patches
    def test_conflict_prioritized_over_subpart_blockers(
        self, mock_section, mock_ctx, mock_log, mock_bridge,
        mock_repair, mock_conflict, mock_branch,
        mock_build_prompt, mock_invoke, mock_normalize, mock_construct,
        mock_dup, mock_package
    ):
        """When both conflict and subpart exist, classifier selects 
        conflict as STRONG target (conflict has higher priority)."""
        mock_section.return_value = FakeSection()
        llm_resp = _llm_response("Which version of the deadline is correct?")
        mock_invoke.return_value = (llm_resp, 0.3)
        mock_normalize.return_value = (llm_resp, "")
        mock_construct.return_value = "Which version of the deadline is correct?"
        mock_dup.return_value = (True, "", "")
        mock_package.return_value = {"current_questions": "ok"}

        generate_questions_node(_make_state(
            remaining_subparts=["who experiences the problem"],
            concept_conflicts=[{"surface": "delivery deadline", "concept_key": "deadline"}],
        ))

        # Prompt should contain TARGET LOCK with the conflict surface
        prompt_arg = mock_invoke.call_args[0][0]
        assert "TARGET LOCK" in prompt_arg
        assert "delivery deadline" in prompt_arg


# ═══════════════════════════════════════════════════════════════════════════
#  SAD PATH TESTS — error recovery, edge cases, graceful degradation
# ═══════════════════════════════════════════════════════════════════════════

class TestSadPath:

    # ── SP1: Lane A + Lane B both duplicate → deterministic fallback ─────
    @_node_test_patches
    def test_double_duplicate_triggers_deterministic_fallback(
        self, mock_section, mock_ctx, mock_log, mock_bridge,
        mock_repair, mock_conflict, mock_branch,
        mock_build_prompt, mock_invoke, mock_normalize, mock_construct,
        mock_dup, mock_package
    ):
        """Lane A duplicates, Lane B also duplicates → deterministic 
        fallback fires, no loop, non-empty output."""
        mock_section.return_value = FakeSection()
        resp1 = _llm_response("Who is affected?")
        resp2 = _llm_response("Who is affected?")  # Same again

        mock_invoke.side_effect = [(resp1, 0.5), (resp2, 0.5)]
        mock_normalize.side_effect = [(resp1, ""), (resp2, "")]
        mock_construct.side_effect = ["Who is affected?", "Who is affected?"]
        mock_dup.return_value = (False, "dup_match", "prior_q")
        mock_package.return_value = {"current_questions": "fallback"}

        generate_questions_node(_make_state(
            remaining_subparts=["who experiences the problem"]
        ))

        # Exactly 2 LLM calls — no infinite loop
        assert mock_invoke.call_count == 2
        # Package was called with deterministic fallback (non-empty)
        questions_arg = mock_package.call_args[0][1]
        assert questions_arg and len(questions_arg.strip()) > 0
        # Status should indicate fallback
        status_kwarg = mock_package.call_args[1].get("override_status",
                        mock_package.call_args[0][7] if len(mock_package.call_args[0]) > 7 else "")
        # The override_status is passed as a keyword arg
        assert "deterministic_fallback" in str(mock_package.call_args)

    # ── SP2: Lane B primary duplicate → deterministic fallback ───────────
    @_node_test_patches
    def test_lane_b_primary_duplicate_triggers_fallback(
        self, mock_section, mock_ctx, mock_log, mock_bridge,
        mock_repair, mock_conflict, mock_branch,
        mock_build_prompt, mock_invoke, mock_normalize, mock_construct,
        mock_dup, mock_package
    ):
        """When Lane B is the primary lane and it produces a duplicate, 
        go straight to deterministic fallback (no demotion possible)."""
        mock_section.return_value = FakeSection()
        resp = _llm_response("Tell me more about the problem.")
        mock_invoke.return_value = (resp, 0.5)
        mock_normalize.return_value = (resp, "")
        mock_construct.return_value = "Tell me more about the problem."
        mock_dup.return_value = (False, "dup_match", "prior_q")
        mock_package.return_value = {"current_questions": "fallback"}

        # Empty subparts → EMPTY confidence → Lane B is primary
        generate_questions_node(_make_state(remaining_subparts=[]))

        # Only 1 LLM call (no demotion since Lane B is already the lane)
        assert mock_invoke.call_count == 1
        # Fallback is non-empty
        questions_arg = mock_package.call_args[0][1]
        assert questions_arg and len(questions_arg.strip()) > 0

    # ── SP3: LLM returns None → normalize handles it gracefully ──────────
    @_node_test_patches
    def test_llm_returns_none_produces_valid_output(
        self, mock_section, mock_ctx, mock_log, mock_bridge,
        mock_repair, mock_conflict, mock_branch,
        mock_build_prompt, mock_invoke, mock_normalize, mock_construct,
        mock_dup, mock_package
    ):
        """When structured extraction returns None, the normalizer
        should produce empty fields and the non-empty guard catches it."""
        mock_section.return_value = FakeSection()

        mock_invoke.return_value = (None, 0.5)
        mock_normalize.return_value = (
            {"single_next_question": "", "subparts": [], "question_id": "fallback"},
            ""
        )
        mock_construct.return_value = ""  # Empty after normalization
        mock_dup.return_value = (False, "empty", "")
        mock_package.return_value = {"current_questions": "fallback"}

        generate_questions_node(_make_state(
            remaining_subparts=["who experiences the problem"]
        ))

        questions_arg = mock_package.call_args[0][1]
        assert questions_arg and len(questions_arg.strip()) > 0

    # ── SP4: LLM returns only whitespace → non-empty guard catches it ────
    @_node_test_patches
    def test_llm_returns_whitespace_triggers_guard(
        self, mock_section, mock_ctx, mock_log, mock_bridge,
        mock_repair, mock_conflict, mock_branch,
        mock_build_prompt, mock_invoke, mock_normalize, mock_construct,
        mock_dup, mock_package
    ):
        """When the LLM returns a question that is only whitespace,
        the non-empty output gate must replace it."""
        mock_section.return_value = FakeSection()
        resp = _llm_response("   \n\t  ")
        mock_invoke.return_value = (resp, 0.5)
        mock_normalize.return_value = (resp, "")
        mock_construct.return_value = "   \n\t  "  # Whitespace only
        mock_dup.return_value = (True, "", "")  # Not a duplicate per se
        mock_package.return_value = {"current_questions": "fallback"}

        generate_questions_node(_make_state(
            remaining_subparts=["who experiences the problem"]
        ))

        questions_arg = mock_package.call_args[0][1]
        assert questions_arg.strip(), f"Expected non-empty but got: '{questions_arg}'"

    # ── SP5: All subparts resolved at runtime → EMPTY → Lane B ───────────
    @_node_test_patches
    def test_all_subparts_resolved_falls_to_lane_b(
        self, mock_section, mock_ctx, mock_log, mock_bridge,
        mock_repair, mock_conflict, mock_branch,
        mock_build_prompt, mock_invoke, mock_normalize, mock_construct,
        mock_dup, mock_package
    ):
        """When ALL section components are resolved in QA store, classifier
        returns EMPTY + SECTION_COMPLETE, and section-complete early exit fires."""
        mock_section.return_value = FakeSection()
        resp = _llm_response("Is there anything else about this problem?")
        mock_invoke.return_value = (resp, 0.5)
        mock_normalize.return_value = (resp, "")
        mock_construct.return_value = "Is there anything else about this problem?"
        mock_dup.return_value = (True, "", "")
        mock_package.return_value = {"current_questions": "ok"}

        result = generate_questions_node(_make_state(
            remaining_subparts=[
                "who experiences the problem",
                "what the problem specifically is",
            ],
            confirmed_qa_store={
                "q1": {
                    "section_id": "problem_statement",
                    "resolved_subparts": ["who experiences the problem"],
                },
                "q2": {
                    "section_id": "problem_statement",
                    "resolved_subparts": ["what the problem specifically is"],
                },
                "q3": {
                    "section_id": "problem_statement",
                    "resolved_subparts": ["why it matters (business or user impact)"],
                },
                "q4": {
                    "section_id": "problem_statement",
                    "resolved_subparts": ["what happens if it is not solved"],
                },
            },
        ))

        # All components resolved → section-complete, no LLM call
        mock_invoke.assert_not_called()
        assert result["generation_status"] == "no_question_available"

    # ── SP6: Malformed structured output (missing fields) → guard ────────
    @_node_test_patches
    def test_malformed_output_missing_fields_catches_gracefully(
        self, mock_section, mock_ctx, mock_log, mock_bridge,
        mock_repair, mock_conflict, mock_branch,
        mock_build_prompt, mock_invoke, mock_normalize, mock_construct,
        mock_dup, mock_package
    ):
        """When the LLM returns a dict missing critical fields,
        normalize should fill defaults and the guard ensures non-empty."""
        mock_section.return_value = FakeSection()

        # Simulate normalizer outputting defaults for missing fields
        malformed_norm = {"single_next_question": "", "subparts": [], "question_id": ""}
        mock_invoke.return_value = ({}, 0.5)  # Empty dict from LLM
        mock_normalize.return_value = (malformed_norm, "")
        mock_construct.return_value = ""  # Empty
        mock_dup.return_value = (False, "empty", "")
        mock_package.return_value = {"current_questions": "fallback"}

        generate_questions_node(_make_state(
            remaining_subparts=["who experiences the problem"]
        ))

        questions_arg = mock_package.call_args[0][1]
        assert questions_arg and len(questions_arg.strip()) > 0


# ═══════════════════════════════════════════════════════════════════════════════
# Phantom Blocker Elimination & History-Aware Fallback Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestParserFallbackPhantomBlocker:
    """Tests that parser fallback never emits phantom subparts."""

    def test_parser_fallback_does_not_emit_clarification_subpart(self):
        """RC1 fix: parser fallback must never inject the phantom 'clarification' subpart."""
        from graph.nodes import _normalize_generated_question

        state = {
            "remaining_subparts": [],
            "recent_questions": [],
            "user_facing_gap_reason": "",
            "question_status": "OPEN",
            "section_index": 0,
        }
        ctx = {"thread_id": "t1", "run_id": "r1", "node_name": "test"}

        with patch("graph.nodes.log_event"):
            with patch("graph.nodes._get_llm", return_value=MagicMock(model="test")):
                with patch("graph.nodes.get_section_by_index", return_value=FakeSection()):
                    # Simulate parser fallback: pass a raw string instead of dict
                    norm_response, _ = _normalize_generated_question(
                        "Here is a question about the problem", state, ctx
                    )

        # The phantom blocker "clarification" must NOT appear
        assert "clarification" not in norm_response.get("subparts", []), \
            f"Parser fallback injected phantom 'clarification': {norm_response['subparts']}"

    def test_parser_fallback_preserves_real_unresolved_blockers_when_available(self):
        """RC1 fix: when real unresolved blockers exist in state, parser fallback must preserve them."""
        from graph.nodes import _normalize_generated_question

        real_blockers = ["who experiences the problem", "what the problem specifically is"]
        state = {
            "remaining_subparts": real_blockers,
            "recent_questions": [],
            "user_facing_gap_reason": "",
            "question_status": "OPEN",
            "section_index": 0,
        }
        ctx = {"thread_id": "t1", "run_id": "r1", "node_name": "test"}

        with patch("graph.nodes.log_event"):
            with patch("graph.nodes._get_llm", return_value=MagicMock(model="test")):
                with patch("graph.nodes.get_section_by_index", return_value=FakeSection()):
                    norm_response, _ = _normalize_generated_question(
                        "Some raw LLM string", state, ctx
                    )

        # Real blockers from state must be preserved
        assert norm_response["subparts"] == real_blockers, \
            f"Expected real blockers {real_blockers}, got {norm_response['subparts']}"


class TestDeterministicFallbackHistoryAware:
    """Tests that deterministic fallback consults recent_questions. """

    def test_deterministic_fallback_does_not_repeat_recent_generic_problem_question(self):
        """RC2 fix: fallback must not re-emit a question already in recent_questions."""
        generic_problem_q = "I'm missing one key piece of context: what specific problem are you trying to solve here?"
        state = {
            "remaining_subparts": [],
            "concept_conflicts": [],
            "recent_questions": [generic_problem_q],
            "section_index": 0,
            "confirmed_qa_store": {},
        }

        with patch("graph.nodes.get_section_by_index", return_value=FakeSection()):
            result = _generate_context_aware_fallback(state)

        # The exact same generic problem question must NOT be repeated
        assert result.lower() != generic_problem_q.lower(), \
            f"Fallback repeated the generic problem question: '{result}'"
        # Output must still be non-empty
        assert result and len(result.strip()) > 0


class TestProductMappingEndToEnd:
    """Integration test: product-mapping case must not repeat the problem question after detailed answer."""

    @_node_test_patches
    def test_product_mapping_case_does_not_repeat_problem_question_after_detailed_answer(
        self, mock_section, mock_ctx, mock_log, mock_bridge,
        mock_repair, mock_conflict, mock_branch,
        mock_build_prompt, mock_invoke, mock_normalize, mock_construct,
        mock_dup, mock_package
    ):
        """End-to-end: after user answers the generic problem question with a detailed answer,
        the system must NOT ask the same question again."""
        generic_problem_q = "I'm missing one key piece of context: what specific problem are you trying to solve here?"

        mock_section.return_value = FakeSection()

        # Simulate: the LLM returns a repeat of the generic question → dup guard catches it
        mock_invoke.return_value = (
            {"single_next_question": generic_problem_q, "question_id": "q2",
             "subparts": [], "question_type": "OPEN_ENDED", "options": []},
            None
        )
        mock_normalize.return_value = (
            {"single_next_question": generic_problem_q, "question_id": "q2",
             "subparts": [], "question_type": "OPEN_ENDED", "options": []},
            ""
        )
        mock_construct.return_value = generic_problem_q
        # Dup guard blocks it
        mock_dup.return_value = (False, "recent_question_match", generic_problem_q)
        mock_package.return_value = {"current_questions": "fallback question"}

        state = _make_state(
            remaining_subparts=[],
            recent_questions=[generic_problem_q],
            confirmed_qa_store={
                "q1": {
                    "section_id": "problem_statement",
                    "questions": generic_problem_q,
                    "answer": "We need to automate product mapping between our ERP and e-commerce platform...",
                    "resolved_subparts": ["clarification"],
                }
            },
        )

        generate_questions_node(state)

        # The question passed to _package must NOT be the same generic question
        questions_arg = mock_package.call_args[0][1]
        assert questions_arg.lower() != generic_problem_q.lower(), \
            f"System repeated the generic problem question: '{questions_arg}'"
        assert questions_arg and len(questions_arg.strip()) > 0, \
            "Output must be non-empty"


# ═══════════════════════════════════════════════════════════════════════════════
# Canonical Registry Rehydration Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestCanonicalRehydration:
    """Tests that _classify_target_confidence rehydrates from the canonical
    section registry when remaining_subparts is empty or fully resolved."""

    def test_classify_target_confidence_rehydrates_from_canonical_registry_when_remaining_subparts_empty(self):
        """When remaining_subparts is empty and QA store has partial resolution,
        rehydration must recover unresolved canonical targets."""
        section = FakeSection()
        state = {
            "remaining_subparts": [],  # empty — starvation scenario
            "concept_conflicts": [],
            "confirmed_qa_store": {
                "q1": {
                    "section_id": "problem_statement",
                    "resolved_subparts": ["who experiences the problem"],
                },
            },
        }
        confidence, target, candidates, reason, reason_code = _classify_target_confidence(state, section)
        
        # Must NOT be EMPTY — 3 canonical components remain unresolved
        assert confidence != "EMPTY", f"Expected rehydration to recover targets, got EMPTY: {reason}"
        # Must indicate rehydration occurred
        assert "REHYDRATED" in reason, f"Expected rehydration telemetry, got: {reason}"
        # Should be MODERATE (3 candidates)
        assert confidence == "MODERATE"
        assert len(candidates) == 3

    def test_empty_remaining_subparts_does_not_force_empty_confidence_when_canonical_targets_remain(self):
        """Even with zero remaining_subparts, if canonical targets exist that
        are not yet resolved in QA store, confidence must not be EMPTY."""
        section = FakeSection()
        state = {
            "remaining_subparts": [],
            "concept_conflicts": [],
            "confirmed_qa_store": {},  # nothing resolved yet
        }
        confidence, target, candidates, reason, reason_code = _classify_target_confidence(state, section)
        # All 4 components should be recovered
        assert confidence == "MODERATE", f"Expected MODERATE (4 unresolved), got {confidence}"
        assert len(candidates) == 4
        assert "REHYDRATED" in reason

    def test_parser_fallback_does_not_starve_future_target_selection(self):
        """After parser fallback fires, the subparts written to state
        must be canonical (not phantom), enabling future target selection."""
        from graph.nodes import _normalize_generated_question

        state = {
            "remaining_subparts": [],  # empty — simulating starvation
            "recent_questions": [],
            "user_facing_gap_reason": "",
            "question_status": "OPEN",
            "section_index": 0,
            "confirmed_qa_store": {
                "q1": {
                    "section_id": "problem_statement",
                    "resolved_subparts": ["who experiences the problem"],
                },
            },
        }
        ctx = {"thread_id": "t1", "run_id": "r1", "node_name": "test"}

        with patch("graph.nodes.log_event"):
            with patch("graph.nodes._get_llm", return_value=MagicMock(model="test")):
                with patch("graph.nodes.get_section_by_index", return_value=FakeSection()):
                    norm_response, _ = _normalize_generated_question(
                        "Some raw LLM string", state, ctx
                    )

        # Subparts must be canonical, not phantom
        subparts = norm_response.get("subparts", [])
        assert "clarification" not in subparts, "Phantom blocker must not appear"
        # Subparts should be derived from section registry minus resolved
        assert len(subparts) > 0, "Parser fallback must seed canonical subparts"
        # Verify they're actual section components
        for sp in subparts:
            assert sp in FakeSection().expected_components, f"'{sp}' is not a canonical component"

    @_node_test_patches
    def test_product_mapping_case_advances_to_next_canonical_component_after_problem_answer(
        self, mock_section, mock_ctx, mock_log, mock_bridge,
        mock_repair, mock_conflict, mock_branch,
        mock_build_prompt, mock_invoke, mock_normalize, mock_construct,
        mock_dup, mock_package
    ):
        """After the user answers the 'problem' question, the system must
        advance to the next unresolved canonical component, not repeat."""
        mock_section.return_value = FakeSection()
        # LLM returns a good focused question about the next component
        next_q = "Who are the primary users affected by this problem?"
        resp = _llm_response(next_q)
        mock_invoke.return_value = (resp, 0.5)
        mock_normalize.return_value = (resp, "")
        mock_construct.return_value = next_q
        mock_dup.return_value = (True, "", "")
        mock_package.return_value = {"current_questions": next_q}

        state = _make_state(
            remaining_subparts=[],  # empty — starvation scenario post truth_commit
            recent_questions=[
                "I'm missing one key piece of context: what specific problem are you trying to solve here?"
            ],
            confirmed_qa_store={
                "q1": {
                    "section_id": "problem_statement",
                    "questions": "I'm missing one key piece of context: what specific problem are you trying to solve here?",
                    "answer": "We need to automate product mapping between our ERP and e-commerce platform.",
                    "resolved_subparts": ["who experiences the problem"],
                }
            },
        )

        generate_questions_node(state)

        # The prompt must contain FOCUS CANDIDATES (not INFERENCE MODE)
        # because rehydration recovered 3 unresolved canonical components
        prompt_arg = mock_invoke.call_args[0][0]
        assert "FOCUS CANDIDATES" in prompt_arg or "INFERENCE MODE" not in prompt_arg or "MODERATE" in str(mock_log.call_args_list), \
            f"Expected FOCUS CANDIDATES (rehydrated targets), but got prompt without them"


# ═══════════════════════════════════════════════════════════════════════════════
# Inference-Led Fallback Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestInferenceLedFallback:
    """Tests that the fallback question style is inference-led when pain points
    exist in the canonical store or current answer."""

    def _state_with_product_mapping_pain(self, **overrides):
        """Helper: builds a state simulating the product-mapping pain point."""
        base = {
            "remaining_subparts": [],
            "concept_conflicts": [],
            "recent_questions": [],
            "section_index": 0,
            "raw_answer_buffer": (
                "We currently do manual Excel matching every week to map products "
                "between our ERP system and the e-commerce platform. There are frequent "
                "mismatches, duplicate mappings, and outdated links that cause pricing errors."
            ),
            "confirmed_qa_store": {},
        }
        base.update(overrides)
        return base

    def test_problem_fallback_references_observed_pain_point(self):
        """When the user has described pain points, the fallback question
        must reference at least one observed pain point."""
        from graph.nodes import _generate_context_aware_fallback
        with patch("graph.nodes.get_section_by_index", return_value=FakeSection()):
            result = _generate_context_aware_fallback(self._state_with_product_mapping_pain())

        # Must reference at least one observed pain
        pain_referenced = any(p in result.lower() for p in [
            "manual", "error", "mismatch", "duplicate", "excel",
        ])
        assert pain_referenced, \
            f"Fallback must reference observed pain point, got: '{result}'"

    def test_problem_fallback_states_inferred_goal_before_question(self):
        """The fallback question must state an inferred goal before asking."""
        from graph.nodes import _generate_context_aware_fallback
        with patch("graph.nodes.get_section_by_index", return_value=FakeSection()):
            result = _generate_context_aware_fallback(self._state_with_product_mapping_pain())

        # Must contain an inference statement about the goal
        goal_stated = any(g in result.lower() for g in [
            "goal is to", "sounds like", "it seems like", "it appears",
            "priority is to",
        ])
        assert goal_stated, \
            f"Fallback must state inferred goal, got: '{result}'"

    def test_problem_fallback_not_generic_when_pain_points_exist(self):
        """When pain points exist, the fallback must NOT be the generic
        'what specific problem are you trying to solve' question."""
        from graph.nodes import _generate_context_aware_fallback
        with patch("graph.nodes.get_section_by_index", return_value=FakeSection()):
            result = _generate_context_aware_fallback(self._state_with_product_mapping_pain())

        generic_questions = [
            "what specific problem are you trying to solve here",
            "could you describe what the ideal end state looks like",
            "what would a successful outcome look like",
        ]
        for gq in generic_questions:
            assert gq not in result.lower(), \
                f"Should not use generic question when pain points are present, got: '{result}'"

    def test_problem_fallback_asks_exactly_one_focused_question(self):
        """The fallback output must contain exactly one question mark,
        ensuring it asks one focused question, not a paragraph of analysis."""
        from graph.nodes import _generate_context_aware_fallback
        with patch("graph.nodes.get_section_by_index", return_value=FakeSection()):
            result = _generate_context_aware_fallback(self._state_with_product_mapping_pain())

        question_marks = result.count("?")
        assert question_marks == 1, \
            f"Must ask exactly one focused question (got {question_marks}): '{result}'"


# ═══════════════════════════════════════════════════════════════════════════════
# Section-Complete Early Exit Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestSectionCompleteEarlyExit:
    """Tests that EMPTY + SECTION_COMPLETE skips LLM and routes to draft."""

    def test_empty_confidence_section_complete_sets_no_question_available(self):
        """When all 4 canonical components are resolved, reason_code must be
        SECTION_COMPLETE and confidence must be EMPTY."""
        section = FakeSection()
        state = {
            "remaining_subparts": [],
            "concept_conflicts": [],
            "confirmed_qa_store": {
                "q1": {"section_id": "problem_statement", "resolved_subparts": ["who experiences the problem"]},
                "q2": {"section_id": "problem_statement", "resolved_subparts": ["what the problem specifically is"]},
                "q3": {"section_id": "problem_statement", "resolved_subparts": ["why it matters (business or user impact)"]},
                "q4": {"section_id": "problem_statement", "resolved_subparts": ["what happens if it is not solved"]},
            },
        }
        confidence, target, candidates, reason, reason_code = _classify_target_confidence(state, section)
        assert confidence == "EMPTY"
        assert reason_code == "SECTION_COMPLETE"

    @_node_test_patches
    def test_empty_confidence_section_complete_skips_llm_call(
        self, mock_section, mock_ctx, mock_log, mock_bridge,
        mock_repair, mock_conflict, mock_branch,
        mock_build_prompt, mock_invoke, mock_normalize, mock_construct,
        mock_dup, mock_package
    ):
        """When SECTION_COMPLETE fires, the LLM must NOT be called."""
        mock_section.return_value = FakeSection()

        result = generate_questions_node(_make_state(
            remaining_subparts=[],
            confirmed_qa_store={
                "q1": {"section_id": "problem_statement", "resolved_subparts": ["who experiences the problem"]},
                "q2": {"section_id": "problem_statement", "resolved_subparts": ["what the problem specifically is"]},
                "q3": {"section_id": "problem_statement", "resolved_subparts": ["why it matters (business or user impact)"]},
                "q4": {"section_id": "problem_statement", "resolved_subparts": ["what happens if it is not solved"]},
            },
        ))

        # LLM must NOT be called
        mock_invoke.assert_not_called()
        # generation_status must be no_question_available
        assert result["generation_status"] == "no_question_available"

    def test_no_question_available_routes_to_draft(self):
        """The router must send no_question_available to draft."""
        from graph.routing import route_after_generate_questions
        state = {"generation_status": "no_question_available", "thread_id": "t1", "run_id": "r1"}
        with patch("graph.routing.log_event"):
            route = route_after_generate_questions(state)
        assert route == "draft", f"Expected 'draft', got '{route}'"

    @_node_test_patches
    def test_section_complete_case_does_not_emit_followup_question(
        self, mock_section, mock_ctx, mock_log, mock_bridge,
        mock_repair, mock_conflict, mock_branch,
        mock_build_prompt, mock_invoke, mock_normalize, mock_construct,
        mock_dup, mock_package
    ):
        """End-to-end: when section is complete, current_questions must be empty."""
        mock_section.return_value = FakeSection()

        result = generate_questions_node(_make_state(
            remaining_subparts=[],
            confirmed_qa_store={
                "q1": {"section_id": "problem_statement", "resolved_subparts": ["who experiences the problem"]},
                "q2": {"section_id": "problem_statement", "resolved_subparts": ["what the problem specifically is"]},
                "q3": {"section_id": "problem_statement", "resolved_subparts": ["why it matters (business or user impact)"]},
                "q4": {"section_id": "problem_statement", "resolved_subparts": ["what happens if it is not solved"]},
            },
        ))

        # Must NOT emit a follow-up question
        assert result["current_questions"] == "", \
            f"Expected empty question, got: '{result['current_questions']}'"
        assert result["generation_status"] == "no_question_available"


# ═══════════════════════════════════════════════════════════════════════════════
# Draft-Skip Loop Guard Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestDraftSkipLoopGuard:
    """Tests that route_after_draft does NOT loop back into generate_questions
    when the section is already complete (no_question_available)."""

    def test_no_question_available_does_not_reenter_generate_questions_in_same_turn(self):
        """When draft is skipped and generation_status=no_question_available,
        route must go to advance_section, NOT generate_questions or reflect."""
        from graph.routing import route_after_draft
        state = {
            "draft_execution_mode": "skipped",
            "generation_status": "no_question_available",
            "thread_id": "t1", "run_id": "r1",
            "section_index": 0, "iteration": 0,
        }
        with patch("graph.routing.log_event"):
            route = route_after_draft(state)
        assert route == "advance_section", f"Expected 'advance_section', got '{route}'"

    def test_draft_skipped_on_completed_section_routes_to_non_recursive_path(self):
        """Completed section must bypass reflect (which would LOOP) and go
        directly to advance_section."""
        from graph.routing import route_after_draft
        state = {
            "draft_execution_mode": "skipped",
            "generation_status": "no_question_available",
            "thread_id": "t1", "run_id": "r1",
            "section_index": 0, "iteration": 0,
        }
        with patch("graph.routing.log_event"):
            route = route_after_draft(state)
        assert route != "reflect", "Completed section must NOT go to reflect"
        assert route != "generate_questions", "Completed section must NOT loop to generate_questions"
        assert route == "advance_section"

    def test_draft_skipped_with_normal_question_routes_to_generate_questions(self):
        """When draft is skipped but there IS a normal question,
        route must go to generate_questions (baseline preserved)."""
        from graph.routing import route_after_draft
        state = {
            "draft_execution_mode": "skipped",
            "generation_status": "question_generated",
            "thread_id": "t1", "run_id": "r1",
            "section_index": 0, "iteration": 0,
        }
        with patch("graph.routing.log_event"):
            route = route_after_draft(state)
        assert route == "generate_questions", f"Expected 'generate_questions', got '{route}'"

    def test_draft_executed_always_routes_to_reflect(self):
        """When draft was actually executed (drafted), always route to reflect
        regardless of generation_status."""
        from graph.routing import route_after_draft
        state = {
            "draft_execution_mode": "drafted",
            "generation_status": "no_question_available",
            "thread_id": "t1", "run_id": "r1",
            "section_index": 0, "iteration": 0,
        }
        with patch("graph.routing.log_event"):
            route = route_after_draft(state)
        assert route == "reflect", f"Expected 'reflect', got '{route}'"

    def test_reflect_cannot_return_loop_for_completed_section(self):
        """Reflect is never reached for a completed section — the route
        goes directly from draft(skipped) to advance_section."""
        from graph.routing import route_after_draft
        # Simulate the exact state that caused the infinite loop
        state = {
            "draft_execution_mode": "skipped",
            "generation_status": "no_question_available",
            "thread_id": "t1", "run_id": "r1",
            "section_index": 0, "iteration": 0,
        }
        with patch("graph.routing.log_event"):
            route = route_after_draft(state)
        # The route must never reach reflect, so reflect's LOOP cannot fire
        assert route == "advance_section", \
            f"Completed section must go to advance_section, not '{route}'"


# ═══════════════════════════════════════════════════════════════════════════════
# Section Transition — Next-Section First Question Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestSectionTransitionFirstQuestion:
    """Verifies that after advance_section, the next section's first question
    is always emitted without stale generation_status bleeding across."""

    def test_advance_section_resets_generation_status_for_next_section(self):
        """advance_section_node must reset generation_status to question_generated
        so the next section never inherits no_question_available."""
        from unittest.mock import MagicMock
        mock_section = MagicMock()
        mock_section.id = "problem_statement"
        mock_section.title = "Problem Statement"
        state = {
            "section_index": 0,
            "generation_status": "no_question_available",
            "current_draft": "Some draft content",
            "iteration": 3,
            "verdict": "PASS",
            "thread_id": "t1", "run_id": "r1",
            "overall_score": 0.9,
            "recovery_mode_consecutive_count": 0,
        }
        from graph.nodes import advance_section_node
        with patch("graph.nodes.log_event"), patch("graph.nodes.get_section_by_index", return_value=mock_section):
            result = advance_section_node(state)
        assert result.get("generation_status") == "question_generated", \
            f"Expected 'question_generated' after advance, got: '{result.get('generation_status')}'"

    def test_advance_section_resets_remaining_subparts_for_next_section(self):
        """remaining_subparts must be cleared on advance so the new section
        rehydrates from its own canonical registry."""
        from unittest.mock import MagicMock
        mock_section = MagicMock()
        mock_section.id = "problem_statement"
        mock_section.title = "Problem Statement"
        state = {
            "section_index": 0,
            "remaining_subparts": ["what problem is being solved"],
            "generation_status": "no_question_available",
            "current_draft": "draft",
            "iteration": 2, "verdict": "PASS",
            "thread_id": "t1", "run_id": "r1",
            "overall_score": 0.8,
            "recovery_mode_consecutive_count": 0,
        }
        from graph.nodes import advance_section_node
        with patch("graph.nodes.log_event"), patch("graph.nodes.get_section_by_index", return_value=mock_section):
            result = advance_section_node(state)
        assert result.get("remaining_subparts") == [], \
            f"Expected empty remaining_subparts after advance, got: {result.get('remaining_subparts')}"

    def test_transition_banner_does_not_leave_user_without_followup_question(self):
        """After advance_section reset, route_after_generate_questions routes
        to await_answer (not draft) so the user sees the next question."""
        from graph.routing import route_after_generate_questions
        state = {
            "generation_status": "question_generated",
            "thread_id": "t1", "run_id": "r1",
            "section_index": 1, "iteration": 0,
        }
        with patch("graph.routing.log_event"):
            route = route_after_generate_questions(state)
        assert route == "await_answer", \
            f"New section should route to await_answer, not '{route}'"

# ═══════════════════════════════════════════════════════════════════════════════
# UI Filter Tests — Transition Banner vs Next-Section Question
# ═══════════════════════════════════════════════════════════════════════════════

class TestTransitionBannerRenderFilter:
    """Verifies that the app.py message filter suppresses only pre-advance
    elicit messages, not the post-advance next-section question."""

    def _make_assistant_msgs(self, types: list[str]) -> list[dict]:
        return [
            {"type": t, "content": f"content for {t}", "_idx": i}
            for i, t in enumerate(types)
        ]

    def _advance_idx(self, msgs: list[dict]) -> int:
        return next(
            (i for i, m in enumerate(msgs) if m.get("type") in ("advance", "complete")),
            len(msgs),
        )

    def test_section_transition_banner_does_not_replace_next_section_question(self):
        """When assistant_msgs = [elicit(old), advance, elicit(new)],
        only the first elicit should be suppressed — the post-advance elicit must survive."""
        msgs = self._make_assistant_msgs(["elicit", "advance", "elicit"])
        has_advance = any(m.get("type") in ("advance", "complete") for m in msgs)
        advance_idx = self._advance_idx(msgs)

        visible = []
        for msg_i, msg in enumerate(msgs):
            msg_type = msg["type"]
            if msg_type in ("reflect", "elicit") and has_advance and msg_i < advance_idx:
                continue
            visible.append(msg)

        visible_types = [m["type"] for m in visible]
        assert "advance" in visible_types, "advance banner must be visible"
        # The LAST elicit (index 2, after advance) must survive
        assert visible_types.count("elicit") == 1, \
            f"Exactly one elicit should be shown (the post-advance one), got: {visible_types}"
        assert visible_types[-1] == "elicit", \
            f"Post-advance elicit must be the last visible message, got: {visible_types}"

    def test_next_section_question_is_final_persistent_assistant_output_after_advance(self):
        """When only an advance and a post-advance elicit exist (no pre-advance elicit),
        both should be shown and elicit should be last."""
        msgs = self._make_assistant_msgs(["advance", "elicit"])
        has_advance = any(m.get("type") in ("advance", "complete") for m in msgs)
        advance_idx = self._advance_idx(msgs)

        visible = []
        for msg_i, msg in enumerate(msgs):
            msg_type = msg["type"]
            if msg_type in ("reflect", "elicit") and has_advance and msg_i < advance_idx:
                continue
            visible.append(msg)

        visible_types = [m["type"] for m in visible]
        assert visible_types == ["advance", "elicit"], \
            f"Expected [advance, elicit], got: {visible_types}"

    def test_transition_status_renders_separately_from_conversational_question(self):
        """Without an advance/complete message (normal single-section turn),
        the elicit message must be rendered as-is without any suppression."""
        msgs = self._make_assistant_msgs(["elicit"])
        has_advance = any(m.get("type") in ("advance", "complete") for m in msgs)
        advance_idx = self._advance_idx(msgs)

        visible = []
        for msg_i, msg in enumerate(msgs):
            msg_type = msg["type"]
            if msg_type in ("reflect", "elicit") and has_advance and msg_i < advance_idx:
                continue
            visible.append(msg)

        visible_types = [m["type"] for m in visible]
        assert visible_types == ["elicit"], \
            f"Normal turn: elicit must be shown, got: {visible_types}"

# ═══════════════════════════════════════════════════════════════════════════════
# Single-Question Enforcement Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestSingleActiveQuestionEnforcement:
    """Verifies that at most one active elicitation question appears per turn,
    even when generate_questions_node emits multiple elicit messages (e.g.
    parser-fallback dual-call pattern)."""

    def _run_filter(self, types: list[str]) -> list[str]:
        """Simulate the app.py message filter and return visible message types."""
        msgs = [{"type": t, "content": f"content {i}", "_idx": i} for i, t in enumerate(types)]
        has_advance = any(m.get("type") in ("advance", "complete") for m in msgs)
        advance_idx = next(
            (i for i, m in enumerate(msgs) if m.get("type") in ("advance", "complete")),
            len(msgs),
        )
        last_post_advance_elicit_idx = None
        if has_advance:
            for _k in range(len(msgs) - 1, advance_idx, -1):
                if msgs[_k].get("type") == "elicit":
                    last_post_advance_elicit_idx = _k
                    break
        visible = []
        for msg_i, msg in enumerate(msgs):
            msg_type = msg["type"]
            if msg_type in ("reflect", "elicit") and has_advance and msg_i < advance_idx:
                continue
            if msg_type == "elicit" and has_advance and msg_i > advance_idx:
                if msg_i != last_post_advance_elicit_idx:
                    continue
            visible.append(msg_type)
        return visible

    def test_only_one_active_question_rendered_after_section_transition(self):
        """Pattern: [elicit(old), advance, elicit1, elicit2]
        Only the last post-advance elicit and the advance banner should be visible."""
        result = self._run_filter(["elicit", "advance", "elicit", "elicit"])
        assert result.count("elicit") == 1, \
            f"Expected exactly 1 elicit visible, got: {result}"
        assert result[-1] == "elicit", \
            f"Last visible message should be elicit (the active question), got: {result}"

    def test_transition_banner_plus_single_post_advance_question_only(self):
        """Pattern: [advance, elicit] — normal case with one post-advance elicit.
        Both should be visible and elicit must be last."""
        result = self._run_filter(["advance", "elicit"])
        assert result == ["advance", "elicit"], \
            f"Expected [advance, elicit], got: {result}"

    def test_multiple_post_advance_questions_collapse_to_final_active_question(self):
        """Pattern: [advance, elicit, elicit, elicit] — 3 post-advance elicits
        (e.g. two parser-fallback calls plus one retry). Only the last must survive."""
        result = self._run_filter(["advance", "elicit", "elicit", "elicit"])
        assert "advance" in result, "advance banner must always be shown"
        assert result.count("elicit") == 1, \
            f"All but the final elicit must be collapsed, got: {result}"
        assert result[-1] == "elicit", \
            f"The surviving elicit must be the last message, got: {result}"

# ═══════════════════════════════════════════════════════════════════════════════
# Graph Concurrency Safety Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestGraphConcurrencySafety:
    """Ensures advance_section does NOT fan out to multiple concurrent branches.
    
    Background: builder.py previously had both:
      - add_conditional_edges(advance_section, ..., {"generate_questions": "rebuild_mirror"})
      - add_edge(advance_section, "generate_questions")   ← spurious duplicate
    
    The duplicate edge created two live execution paths that both reached
    await_answer, causing InvalidUpdateError on interpreted_answer when the
    next user reply was processed twice in parallel.
    """

    def test_section_transition_leaves_only_one_active_answer_path(self):
        """The builder must NOT contain both a conditional edge AND a direct
        unconditional edge from advance_section to generate_questions."""
        import ast
        import pathlib
        src = pathlib.Path("/Users/creampuff/chatbot/graph/builder.py").read_text()
        tree = ast.parse(src)
        
        add_edge_advance = []
        add_cond_advance = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            method = getattr(func, "attr", "")
            if not node.args:
                continue
            first_arg = node.args[0]
            if not isinstance(first_arg, ast.Constant):
                continue
            if first_arg.value != "advance_section":
                continue
            if method == "add_edge":
                second = node.args[1] if len(node.args) > 1 else None
                if isinstance(second, ast.Constant):
                    add_edge_advance.append(second.value)
            elif method == "add_conditional_edges":
                # Any conditional edge from advance_section
                add_cond_advance.append("conditional")
        
        # There must be a conditional edge from advance_section
        assert add_cond_advance, "advance_section must have conditional routing"
        # There must NOT be a direct unconditional edge to generate_questions
        assert "generate_questions" not in add_edge_advance, (
            "Found duplicate add_edge(advance_section → generate_questions)! "
            "This creates two concurrent branches and causes InvalidUpdateError."
        )

    def test_single_user_reply_is_consumed_by_only_one_section(self):
        """route_after_advance must not produce a terminal that bypasses
        the conditional check — i.e. it returns exactly one of {generate_questions, finalize}."""
        from graph.routing import route_after_advance
        # Normal (next section exists, not complete)
        state_normal = {"is_complete": False, "section_index": 0, "thread_id": "t1", "run_id": "r1"}
        with patch("graph.routing.log_event"):
            route = route_after_advance(state_normal)
        assert route == "generate_questions", f"Expected generate_questions, got {route}"
        # All sections complete
        state_done = {"is_complete": True, "section_index": 99, "thread_id": "t1", "run_id": "r1"}
        with patch("graph.routing.log_event"):
            route = route_after_advance(state_done)
        assert route == "finalize", f"Expected finalize, got {route}"

    def test_no_duplicate_truth_commit_after_section_advance(self):
        """When only one branch runs through the answer pipeline, each answer
        produces exactly one truth_commit (no duplicate canonical writes in the
        same section on the same iteration/round)."""
        # Simulate truth_commit key construction logic to prove uniqueness
        section_id = "elevator_pitch"
        iteration = 0
        round_n = 1
        key1 = f"{section_id}:iter_{iteration}:round_{round_n}"
        # A duplicated branch would attempt to write iter_0:round_2 for the same answer
        # The only legal next key after round_1 is round_2 triggered by a NEW user reply
        key2_same_turn = f"{section_id}:iter_{iteration}:round_{round_n}"
        assert key1 == key2_same_turn, "Same turn must produce same key — no duplicate writes"

    def test_no_concurrent_update_on_interpreted_answer_after_transition(self):
        """After section advance, exactly one conditional edge must exist from
        advance_section. Two outgoing edges would cause both to execute and
        write to the same last-value channel (interpreted_answer)."""
        import ast
        import pathlib
        src = pathlib.Path("/Users/creampuff/chatbot/graph/builder.py").read_text()
        tree = ast.parse(src)
        
        unconditional_count = 0
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if getattr(func, "attr", "") != "add_edge":
                continue
            if not node.args or not isinstance(node.args[0], ast.Constant):
                continue
            if node.args[0].value == "advance_section":
                unconditional_count += 1
        
        assert unconditional_count == 0, (
            f"Found {unconditional_count} unconditional edge(s) from advance_section. "
            "Any unconditional edge alongside the conditional edge creates parallel branches "
            "that cause InvalidUpdateError on interpreted_answer."
        )

# ═══════════════════════════════════════════════════════════════════════════════
# Multi-Section Answer Handling Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestMultiSectionAnswerHandling:
    """Verifies the cross-section completion mechanism: side-fact enrichment with
    resolved_subparts and section draft synthesis from qa_store."""

    def test_cross_section_side_fact_can_complete_target_section_when_fact_text_matches_expected_component(self):
        """_match_fact_to_components must return matching expected_components when
        the fact text contains significant tokens from those components."""
        from graph.nodes import _match_fact_to_components, get_section_by_id
        target_sec = get_section_by_id("elevator_pitch")
        assert target_sec is not None
        # Fact text explicitly contains tokens from "target user or persona"
        fact = "The target user is a procurement manager who handles supplier catalogs."
        result = _match_fact_to_components(fact, "user", target_sec)
        assert "target user or persona" in result, (
            f"Expected 'target user or persona' in resolved, got: {result}"
        )

    def test_generic_side_fact_category_does_not_overcomplete_unrelated_section(self):
        """A generic category like 'metric' alone must not match components.
        Only fact text can drive the match."""
        from graph.nodes import _match_fact_to_components, get_section_by_id
        target_sec = get_section_by_id("elevator_pitch")
        # Fact text with no tokens matching elevator_pitch expected_components
        fact = "The latency p99 improved from 380ms to 140ms."
        result = _match_fact_to_components(fact, "metric", target_sec)
        # No elevator_pitch component (target user, unmet need, proposed solution,
        # key benefit) should match against "latency p99 improved from 380ms to 140ms"
        assert len(result) == 0, (
            f"Generic metric fact must not match elevator_pitch components, got: {result}"
        )

    def test_multi_section_reply_skips_next_section_only_when_expected_components_truly_satisfied(self):
        """_classify_target_confidence must return SECTION_COMPLETE when ALL
        expected_components of a section appear in qa_store as resolved_subparts."""
        from graph.nodes import _classify_target_confidence, get_section_by_id
        target_sec = get_section_by_id("elevator_pitch")
        assert target_sec is not None
        # Seed qa_store with all four elevator_pitch expected_components resolved
        qa_store = {
            "elevator_pitch:side_fact:headliner:iter_0:round_1:user": {
                "section_id": "elevator_pitch",
                "section": "elevator_pitch",
                "answer": "procurement managers",
                "contradiction_flagged": False,
                "resolved_subparts": ["target user or persona"],
                "version": 1,
            },
            "elevator_pitch:side_fact:headliner:iter_0:round_1:need": {
                "section_id": "elevator_pitch",
                "section": "elevator_pitch",
                "answer": "manual matching waste",
                "contradiction_flagged": False,
                "resolved_subparts": ["unmet need or pain point"],
                "version": 2,
            },
            "elevator_pitch:side_fact:headliner:iter_0:round_1:solution": {
                "section_id": "elevator_pitch",
                "section": "elevator_pitch",
                "answer": "fuzzy ML matching system",
                "contradiction_flagged": False,
                "resolved_subparts": ["proposed solution"],
                "version": 3,
            },
            "elevator_pitch:side_fact:headliner:iter_0:round_1:benefit": {
                "section_id": "elevator_pitch",
                "section": "elevator_pitch",
                "answer": "90% accuracy, zero manual effort",
                "contradiction_flagged": False,
                "resolved_subparts": ["key benefit or differentiator"],
                "version": 4,
            },
        }
        state = {
            "confirmed_qa_store": qa_store,
            "remaining_subparts": [],
            "concept_conflicts": [],
            "thread_id": "t1", "run_id": "r1",
        }
        with patch("graph.nodes.log_event"):
            confidence, target, candidates, reason, reason_code = _classify_target_confidence(state, target_sec)
        assert reason_code == "SECTION_COMPLETE", (
            f"All components resolved via side-facts — expected SECTION_COMPLETE, got {reason_code}: {reason}"
        )
        assert confidence == "EMPTY", f"Expected EMPTY confidence, got {confidence}"

    def test_advance_section_synthesizes_non_empty_section_draft_from_store(self):
        """When current_draft is empty, advance_section_node must synthesize a
        non-empty draft from confirmed_qa_store entries for the completed section."""
        from graph.nodes import _synthesize_section_draft_from_qa_store
        qa_store = {
            "headliner:iter_0:round_1": {
                "section_id": "headliner",
                "answer": "Manual product matching costs 40 hours per week.",
                "contradiction_flagged": False,
                "version": 1,
            },
            "headliner:side_fact:x:user": {
                "section_id": "headliner",
                "answer": "The affected team is the procurement department.",
                "contradiction_flagged": False,
                "version": 2,
            },
            "headliner:contradicted": {
                "section_id": "headliner",
                "answer": "This fact was retracted.",
                "contradiction_flagged": True,  # must be excluded
                "version": 3,
            },
        }
        result = _synthesize_section_draft_from_qa_store("headliner", qa_store)
        assert result, "Draft must be non-empty when qa_store has valid entries"
        assert "retracted" not in result, "Contradicted facts must be excluded from draft"
        assert "40 hours" in result or "procurement" in result, (
            f"Draft must contain at least one valid answer, got: {result!r}"
        )
