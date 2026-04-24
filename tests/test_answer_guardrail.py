"""tests/test_answer_guardrail.py

5 regression tests for the answer validity guardrail (utils/answer_guardrail.py).

T1 – test_single_character_typo_does_not_pass
T2 – test_symbol_only_input_rejected
T3 – test_yes_valid_for_binary_question
T4 – test_sales_valid_for_choice_question
T5 – test_numeric_valid_for_metric_question
"""
from __future__ import annotations
import pytest

from utils.answer_guardrail import check_answer_quality


# ── T1: Single character typo ─────────────────────────────────────────────────

def test_single_character_typo_does_not_pass():
    """'ñ' submitted as an answer must be REJECTED regardless of question context.

    Expected:
    - passed=False
    - reason='single_char_typo'
    - clarification_prompt is non-empty and does not echo the garbage input
    """
    result = check_answer_quality("ñ", "Tell me about the current workflow pain.")

    assert not result.passed, (
        f"Single-char typo 'ñ' must be REJECTED but was PASSED. "
        f"reason={result.reason!r}, score={result.score}"
    )
    assert result.reason == "single_char_typo", (
        f"Expected reason='single_char_typo', got {result.reason!r}"
    )
    assert result.clarification_prompt, "clarification_prompt must be non-empty on reject"
    assert "ñ" not in result.clarification_prompt, (
        "clarification_prompt must NOT echo the garbage input back to the user"
    )


# ── T2: Symbol-only / punctuation-only ───────────────────────────────────────

@pytest.mark.parametrize("noise_input", ["...", ",", ".", "???", "!!!", "---"])
def test_symbol_only_input_rejected(noise_input: str):
    """Symbol-only / punctuation-only inputs must be REJECTED.

    Expected:
    - passed=False
    - clarification_prompt is non-empty
    """
    result = check_answer_quality(noise_input, "What department is most affected?")

    assert not result.passed, (
        f"Symbol-only input {noise_input!r} must be REJECTED but was PASSED. "
        f"reason={result.reason!r}"
    )
    assert result.clarification_prompt, (
        f"clarification_prompt must be non-empty for input {noise_input!r}"
    )


# ── T3: 'yes' valid for binary question ──────────────────────────────────────

def test_yes_valid_for_binary_question():
    """'yes' must be PASSED when the active question is binary.

    Expected:
    - passed=True
    """
    binary_question = "Do you currently have a manual review process? yes or no"
    result = check_answer_quality("yes", binary_question)

    assert result.passed, (
        f"'yes' must be PASSED for a binary question but was REJECTED. "
        f"reason={result.reason!r}, score={result.score}"
    )


# ── T4: 'sales' valid for choice question ────────────────────────────────────

def test_sales_valid_for_choice_question():
    """'sales' must be PASSED when the question asks about teams / departments.

    Expected:
    - passed=True
    """
    choice_question = "Which team benefits most from this change?"
    result = check_answer_quality("sales", choice_question)

    assert result.passed, (
        f"'sales' must be PASSED for a choice question but was REJECTED. "
        f"reason={result.reason!r}, score={result.score}"
    )


# ── T5: Numeric reply valid for metric question ───────────────────────────────

def test_numeric_valid_for_metric_question():
    """A short numeric reply like '15' must be PASSED when the question requests a metric.

    Expected:
    - passed=True
    """
    metric_question = "How many minutes does the current process take on average?"
    result = check_answer_quality("15", metric_question)

    assert result.passed, (
        f"'15' must be PASSED for a metric question but was REJECTED. "
        f"reason={result.reason!r}, score={result.score}"
    )


# ── Bonus: keyboard-mash rejected ────────────────────────────────────────────

@pytest.mark.parametrize("mash", ["aaaa", "kkkk", "xxxx", "1111"])
def test_keyboard_mash_rejected(mash: str):
    """Repeated single-character mashes must be REJECTED."""
    result = check_answer_quality(mash, "Describe the main pain point.")
    assert not result.passed, (
        f"Keyboard mash {mash!r} must be REJECTED but was PASSED. "
        f"reason={result.reason!r}"
    )
    assert result.reason == "repeated_char_mash", (
        f"Expected reason='repeated_char_mash', got {result.reason!r}"
    )
