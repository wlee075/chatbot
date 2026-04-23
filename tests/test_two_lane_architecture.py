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
        confidence, target, candidates, reason = _classify_target_confidence(state, section)
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
        confidence, target, candidates, reason = _classify_target_confidence(state, section)
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
        confidence, target, candidates, reason = _classify_target_confidence(state, section)
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
        confidence, target, candidates, reason = _classify_target_confidence(state, section)
        assert confidence == "WEAK"
        assert "synthetic" in reason.lower() or "stale" in reason.lower()

    def test_empty_nothing_left(self):
        """D4: No remaining subparts → EMPTY"""
        section = FakeSection()
        state = {
            "remaining_subparts": [],
            "concept_conflicts": [],
            "confirmed_qa_store": {},
        }
        confidence, target, candidates, reason = _classify_target_confidence(state, section)
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
        confidence, target, candidates, reason = _classify_target_confidence(state, section)
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
            _, _, _, reason = _classify_target_confidence(state, section)
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
        """When remaining_subparts are all in confirmed_qa_store, classifier
        returns EMPTY, and Lane B inference fires."""
        mock_section.return_value = FakeSection()
        resp = _llm_response("Is there anything else about this problem?")
        mock_invoke.return_value = (resp, 0.5)
        mock_normalize.return_value = (resp, "")
        mock_construct.return_value = "Is there anything else about this problem?"
        mock_dup.return_value = (True, "", "")
        mock_package.return_value = {"current_questions": "ok"}

        generate_questions_node(_make_state(
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
            },
        ))

        # Lane B inference mode should be used
        prompt_arg = mock_invoke.call_args[0][0]
        assert "INFERENCE MODE" in prompt_arg

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
