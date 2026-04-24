"""tests/test_deterministic_formatter.py

Regression tests for _format_deterministic_propose_question corruption guards.

Tests (5):
  T1 – test_deterministic_propose_does_not_embed_nested_prompt_text
  T2 – test_deterministic_propose_does_not_render_truncated_snippet_fragments
  T3 – test_citation_sanitization_does_not_corrupt_clean_formatter_output
  T4 – test_background_formatter_uses_clean_sentence_not_raw_quote
  T5 – test_incomplete_snippet_only_used_as_evidence_not_display_text
"""
from __future__ import annotations
import re
import pytest

# Import the function under test directly.
# _format_deterministic_propose_question is a module-level function in nodes.py;
# we test the sanitization helper via its observable outputs only.
from graph.nodes import _format_deterministic_propose_question


# ── Helpers ────────────────────────────────────────────────────────────────────

class _FakeSection:
    """Minimal section stub."""
    def __init__(self, section_id: str = "background", title: str = "Background"):
        self.id    = section_id
        self.title = title


def _plan(
    candidates: list,
    action: str = "PROPOSE_ONE",
    section_id: str = "background",
    section_title: str = "Background",
) -> dict:
    return {
        "candidate_items":       candidates,
        "recommended_action":    action,
        "target_section_id":     section_id,
        "target_section_title":  section_title,
    }


_FORBIDDEN_PATTERNS = [
    re.compile(r'^"From what', re.IGNORECASE),    # nested opener in opening quote
    re.compile(r'"From what.*?"', re.IGNORECASE), # nested opener in inline quote
    re.compile(r'\b\w{1,3}"\s*[.!]\s*$'),         # dangling fragment like Wh".
    re.compile(r'From what.*?From what', re.IGNORECASE | re.DOTALL),  # double opener
    re.compile(r'here\'?s my read.*?here\'?s my read', re.IGNORECASE | re.DOTALL),
]


def _assert_clean(output: str) -> None:
    """Assert output does not match any forbidden corruption pattern."""
    for pat in _FORBIDDEN_PATTERNS:
        assert not pat.search(output), (
            f"Forbidden pattern {pat.pattern!r} found in output:\n{output!r}"
        )


# ── T1: Nested prompt text must not appear in output ─────────────────────────

def test_deterministic_propose_does_not_embed_nested_prompt_text():
    """If candidate.text is a prior assistant opener phrase (R4 corruption),
    _sanitize_candidate_text must strip it before display.

    This is the exact bug from the production log:
      candidate = "From what you've shared, here's my read of the current state:
                   manual product mapping delays... 1. Delayed time-to-market. Wh"
      formatter wrapped it → double opener + truncated tail in UI.
    """
    # Simulate the corrupted candidate — a pre-rendered question body stored as candidate text
    corrupted_candidate = (
        "From what you've shared, here's my read of the current state: "
        "manual product mapping delays new items from reaching the market. "
        "1. Delayed time-to-market. Wh"
    )
    section = _FakeSection()
    output = _format_deterministic_propose_question(
        _plan([{"text": corrupted_candidate, "category": "workflow"}]),
        section,
    )

    # If all candidates are rejected, output is "" (LLM fallback) — also acceptable
    if output:
        _assert_clean(output)
        # Must not contain the nested opener
        assert "here's my read" not in output.lower(), (
            f"Nested opener phrase leaked into output:\n{output!r}"
        )


# ── T2: Truncated snippet fragments must not render in output ─────────────────

def test_deterministic_propose_does_not_render_truncated_snippet_fragments():
    """A candidate text that ends mid-word (truncated at 180 chars) must either
    be trimmed to the last complete sentence/phrase, or rejected entirely.

    The forbidden output pattern is any sentence ending with 1-3 chars then quote:
      '...Delayed time-to-market. Wh".'
    """
    truncated = (
        "Supplier data arrives in inconsistent formats, causing downstream delays; "
        "manual reconciliation takes 45 minutes per batch. Teams cannot release "
        "new product catalog entries until the sync completes. Wh"  # mid-word cut
    )
    section = _FakeSection()
    output = _format_deterministic_propose_question(
        _plan([{"text": truncated}]),
        section,
    )

    if output:
        # Must not end with a dangling short token
        assert not re.search(r'\b\w{1,3}["\u201c\u201d]?\s*[.!?]?\s*$', output), (
            f"Output ends with a dangling fragment:\n{output!r}"
        )
        _assert_clean(output)


# ── T3: Clean formatter output must not be corrupted by sanitization ──────────

def test_citation_sanitization_does_not_corrupt_clean_formatter_output():
    """When the candidate text is clean (no nested opener, no quotes, complete
    sentences), the formatter output must be a valid, non-empty question string.

    This guards against over-aggressive sanitization stripping good candidates.
    """
    clean_candidate = (
        "Manual supplier CSV alignment takes 45 minutes per batch, "
        "with human sign-off required below 92% match confidence"
    )
    section = _FakeSection()
    output = _format_deterministic_propose_question(
        _plan([{"text": clean_candidate}]),
        section,
    )

    assert output, "Clean candidate must produce non-empty formatter output"
    assert len(output) >= 50, f"Output too short: {output!r}"
    _assert_clean(output)

    # Must contain a question mark (it's a confirm/correct question)
    assert "?" in output, f"Output must be a question:\n{output!r}"


# ── T4: Background formatter uses synthesised sentence, not raw quote ─────────

def test_background_formatter_uses_clean_sentence_not_raw_quote():
    """For the Background section, the only tested output format is
    'From what you've shared, the current state seems to be that [evidence].'
    The evidence must NOT appear inline as a quoted string.

    The old bug: output was
      'From what you've shared, it sounds like ... is: "[raw evidence]".'
    The new rule: write prose ABOUT the evidence, not a sentence CONTAINING it as a quote.
    """
    candidate = "operators manually align supplier files before the daily sync trigger"
    section = _FakeSection("background", "Background")
    output = _format_deterministic_propose_question(
        _plan([{"text": candidate}]),
        section,
    )

    assert output, "Must produce non-empty output for clean candidate"
    # The evidence must NOT appear as an inline quoted string
    assert f'"{candidate}"' not in output, (
        f"Evidence was inline-quoted in output (forbidden):\n{output!r}"
    )
    # Must contain the evidence content naturally (not in quotes)
    assert "operator" in output.lower() or "supplier" in output.lower() or "align" in output.lower(), (
        f"Evidence content not reflected in output:\n{output!r}"
    )
    _assert_clean(output)


# ── T5: Incomplete snippet used only as evidence, not as displayed prose ───────

def test_incomplete_snippet_only_used_as_evidence_not_display_text():
    """A snippet that is too short (< 20 chars after sanitization) must be
    rejected by _sanitize_candidate_text and not appear as display prose.

    If all candidates are rejected, output must be "" (caller falls back to LLM).
    """
    too_short_candidates = [
        {"text": "Wh"},           # dangling fragment
        {"text": '"'},            # bare quote
        {"text": "   "},          # whitespace only
        {"text": "manual gri"},   # 10 chars < 20 threshold
    ]
    section = _FakeSection()
    output = _format_deterministic_propose_question(
        _plan(too_short_candidates),
        section,
    )

    # All candidates must be rejected → LLM fallback → output is ""
    assert output == "", (
        f"All corrupt/too-short candidates must produce empty output for LLM fallback.\n"
        f"Got: {output!r}"
    )
