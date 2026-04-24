"""
Tests: NeMo guardrail section-relevance override + answer_clarification fail-safe.

Regression guard for:
  1. Rich Background workflow answers wrongly classified as off_topic
  2. answer_clarification crashing with 'contents are required' when question is empty
"""
import contextlib
import pytest
from unittest.mock import MagicMock, patch, call

# ── Fix 1: Section-relevance override unit tests ───────────────────────────────

from utils.nemo_guardrails_gateway import (
    _section_relevance_override,
    _SECTION_SIGNALS,
    _OVERRIDE_MIN_SIGNAL_COUNT,
)

# The Alex/Jamie background answer from the failing run
_BACKGROUND_WORKFLOW_ANSWER = (
    "Alex and Jamie run the batch reconciliation workflow. "
    "Alex exports the supplier list from ERP every weekday morning (around 8am), "
    "then Jamie runs a Python script to do the CSV validation steps. "
    "The main friction is that mapping fails silently when product codes don't match. "
    "The worst failure mode is production halting because the downstream handoff is blocked."
)


def test_background_workflow_answer_not_off_topic():
    """A rich Background workflow answer must NOT be classified as off_topic."""
    final_class, reason = _section_relevance_override(
        "off_topic",
        _BACKGROUND_WORKFLOW_ANSWER,
        "background",
        "NEMO_FASTEMBED",
        0.55,
    )
    assert final_class == "valid_answer", (
        f"Expected valid_answer but got {final_class!r}. "
        f"Background answer should pass section relevance override. Reason: {reason}"
    )
    assert "section signals matched" in reason
    assert "background" in reason


def test_nemo_off_topic_overridden_by_section_relevance():
    """Section relevance override fires when ≥2 signals match, converting off_topic → valid_answer."""
    # Minimal answer with exactly 2 signals: 'workflow' and 'batch'
    answer = "The workflow runs in weekly batches."
    final_class, reason = _section_relevance_override(
        "off_topic", answer, "background", "NEMO_FASTEMBED", 0.52
    )
    assert final_class == "valid_answer"
    assert "off_topic overridden to valid_answer" in reason


def test_genuine_off_topic_not_overridden():
    """A truly off-topic answer (no section signals) must NOT be overridden."""
    answer = "I really like coffee and cats."  # No background signals
    final_class, reason = _section_relevance_override(
        "off_topic", answer, "background", "NEMO_FASTEMBED", 0.52
    )
    assert final_class == "off_topic"
    assert reason == ""


def test_non_off_topic_class_not_affected_by_override():
    """Override only fires for off_topic — valid_answer passes through unchanged."""
    final_class, reason = _section_relevance_override(
        "valid_answer",
        _BACKGROUND_WORKFLOW_ANSWER,
        "background",
        "NEMO_FASTEMBED",
        0.90,
    )
    assert final_class == "valid_answer"
    assert reason == ""


def test_override_min_signal_count_is_two():
    """Exactly 1 signal match must NOT trigger override (need ≥2)."""
    assert _OVERRIDE_MIN_SIGNAL_COUNT == 2
    answer = "workflow"   # only 1 signal
    final_class, _ = _section_relevance_override(
        "off_topic", answer, "background", "NEMO_FASTEMBED", 0.52
    )
    assert final_class == "off_topic"


# ── Fix 2: answer_clarification fail-safe tests ────────────────────────────────

from graph.nodes import answer_clarification_node
from config.sections import get_section_by_index, PRD_SECTIONS

BG_IDX = next(i for i, s in enumerate(PRD_SECTIONS) if s.id == "background")


def _base_state(**overrides):
    s = {
        "thread_id": "t-ac", "run_id": "r-ac",
        "section_index": BG_IDX, "iteration": 1,
        "current_questions": "Describe the current background workflow.",
        "raw_answer_buffer": "Alex runs the CSV batch every weekday morning.",
        "message_class": "valid_answer",
        "remaining_subparts": [],
        "concept_conflicts": [],
        "prd_sections": {},
        "active_question_options": [],
        "reply_context_interpretation": {},
        "reply_context_message_text": "",
        "question_status": "OPEN",
    }
    s.update(overrides)
    return s


def _fresh_ctx(*a, **kw):
    return {"thread_id": "t1", "run_id": "r1",
            "node_name": "answer_clarification", "_t0": 0}


_EMPTY_PROMPT_PATCHES = [
    patch("graph.nodes._log_ctx", side_effect=_fresh_ctx),
    patch("graph.nodes.log_event"),
]


def test_answer_clarification_empty_question_no_llm_crash():
    """answer_clarification must return deterministic fallback when question is empty."""
    with contextlib.ExitStack() as stack:
        for p in _EMPTY_PROMPT_PATCHES:
            stack.enter_context(p)
        mock_llm_invoke = stack.enter_context(
            patch("graph.nodes.llm_invoke", side_effect=Exception("contents are required"))
        )
        result = answer_clarification_node(
            _base_state(current_questions="")  # empty question
        )

    # Must not raise — must return a dict with chat_history
    assert isinstance(result, dict)
    assert "chat_history" in result
    history = result["chat_history"]
    assert len(history) == 1
    msg = history[0]
    assert msg["role"] == "assistant"
    assert "background" in msg["content"].lower() or "section" in msg["content"].lower()
    # LLM must NOT have been called
    mock_llm_invoke.assert_not_called()


def test_guardrail_block_off_topic_empty_question_uses_section_name():
    """off_topic block with empty question → guardrail bypass message names the section."""
    with contextlib.ExitStack() as stack:
        for p in _EMPTY_PROMPT_PATCHES:
            stack.enter_context(p)
        mock_llm_invoke = stack.enter_context(
            patch("graph.nodes.llm_invoke", side_effect=Exception("should not be called"))
        )
        result = answer_clarification_node(
            _base_state(
                message_class="off_topic",
                current_questions="",  # guardrail fired before question was set
            )
        )

    assert isinstance(result, dict)
    assert "chat_history" in result
    msg = result["chat_history"][0]
    # Must contain the section title or relevant context
    assert "background" in msg["content"].lower() or "current section" in msg["content"].lower()
    mock_llm_invoke.assert_not_called()


def test_answer_clarification_normal_path_calls_llm():
    """When question and answer are both present, the LLM path should be attempted."""
    mock_response = MagicMock()
    mock_response.content = '{"response_text": "Please clarify the workflow steps."}'

    with contextlib.ExitStack() as stack:
        for p in _EMPTY_PROMPT_PATCHES:
            stack.enter_context(p)
        mock_llm_invoke = stack.enter_context(
            patch("graph.nodes.llm_invoke", return_value=mock_response)
        )
        stack.enter_context(
            patch("graph.nodes._get_llm", return_value=MagicMock())
        )
        stack.enter_context(
            patch("graph.nodes._log_llm_elapsed")
        )
        result = answer_clarification_node(
            _base_state(
                message_class="valid_answer",
                current_questions="Describe the current workflow.",
                raw_answer_buffer="Alex runs the batch.",
            )
        )

    # LLM should have been invoked
    mock_llm_invoke.assert_called_once()
    assert isinstance(result, dict)
