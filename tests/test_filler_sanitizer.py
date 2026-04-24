"""tests/test_filler_sanitizer.py — unit tests for utils/filler_sanitizer.py

Covers all 4 specified acceptance tests plus structural sanitizer tests:
  - test_conversational_filler_not_committed
  - test_filler_only_answer_not_committed
  - test_side_fact_extraction_uses_cleaned_answer
  - test_generated_question_does_not_include_filler
"""

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
from utils.filler_sanitizer import sanitize_answer, is_filler_only


# ── sanitize_answer core tests ────────────────────────────────────────────────

class TestSanitizeAnswer:
    def test_em_dash_separator(self):
        raw = "Great clarifying question — the volume is what kills the clock, but edge-case complexity breaks the matcher."
        result = sanitize_answer(raw)
        assert "Great clarifying question" not in result
        assert "the volume is what kills the clock" in result

    def test_en_dash_separator(self):
        raw = "You're right – the real goal is reducing manual mapping from 45 minutes to 15 seconds."
        result = sanitize_answer(raw)
        assert "You're right" not in result
        assert "reducing manual mapping" in result

    def test_comma_separator(self):
        raw = "You're right, the real goal is reducing manual mapping from 45 minutes to 15 seconds."
        result = sanitize_answer(raw)
        assert "You're right" not in result
        assert "reducing manual mapping" in result

    def test_good_clarifying_question(self):
        raw = "Good clarifying question — the goal is same-day onboarding for supplier feeds."
        result = sanitize_answer(raw)
        assert "Good clarifying question" not in result
        assert "same-day onboarding" in result

    def test_exactly_with_substantive_content(self):
        raw = "Exactly — we need supplier feed onboarding to complete within seconds."
        result = sanitize_answer(raw)
        assert "Exactly" not in result.split()[0] if result else True
        assert "onboarding" in result

    def test_no_filler_unchanged(self):
        raw = "The volume is what kills the clock, but edge-case complexity breaks the matcher."
        result = sanitize_answer(raw)
        assert result == raw

    def test_pure_substantive_unchanged(self):
        raw = "We need to reduce error rate from 15% to under 2%."
        result = sanitize_answer(raw)
        assert result == raw

    def test_idempotent(self):
        raw = "Great question — the goal is speed and accuracy."
        once = sanitize_answer(raw)
        twice = sanitize_answer(once)
        assert once == twice

    def test_empty_input(self):
        assert sanitize_answer("") == ""

    def test_whitespace_only(self):
        assert sanitize_answer("   ") == ""

    def test_from_my_perspective(self):
        raw = "From my perspective, the onboarding flow is the biggest bottleneck."
        result = sanitize_answer(raw)
        assert "From my perspective" not in result
        assert "onboarding flow" in result

    def test_to_answer_your_question(self):
        raw = "To answer your question, we process about 10,000 records per day."
        result = sanitize_answer(raw)
        assert "To answer your question" not in result
        assert "10,000 records" in result

    def test_in_short(self):
        raw = "In short, the goal is to eliminate manual mapping entirely."
        result = sanitize_answer(raw)
        assert "In short" not in result
        assert "eliminate manual mapping" in result


# ── is_filler_only tests ──────────────────────────────────────────────────────

class TestIsFillerOnly:
    def test_great_question_exactly_is_filler_only(self):
        """test_filler_only_answer_not_committed — acceptance test"""
        assert is_filler_only("Great question, exactly.") is True

    def test_great_question_alone(self):
        assert is_filler_only("Great question.") is True

    def test_exactly_alone(self):
        assert is_filler_only("Exactly.") is True

    def test_you_are_right_alone(self):
        assert is_filler_only("You're right.") is True

    def test_substantive_answer_not_filler(self):
        assert is_filler_only("The volume is what kills the clock.") is False

    def test_filler_plus_substantive_not_filler_only(self):
        # Has substantive content after stripping filler
        raw = "Great clarifying question — the volume is what kills the clock."
        assert is_filler_only(raw) is False

    def test_empty_is_filler(self):
        assert is_filler_only("") is True

    def test_very_short_remainder_is_filler(self):
        # Sanitized remainder is too short to be substantive
        assert is_filler_only("Exactly. Yes.") is True


# ── Acceptance test: filler not committed ─────────────────────────────────────

def test_conversational_filler_not_committed():
    """Stored canonical answer excludes 'Great clarifying question' and keeps substance."""
    raw = "Great clarifying question — the volume is what kills the clock, but edge-case complexity breaks the matcher."
    cleaned = sanitize_answer(raw)
    assert "Great clarifying question" not in cleaned
    assert "the volume is what kills the clock" in cleaned
    assert "edge-case complexity" in cleaned
    assert is_filler_only(raw) is False  # has substantive content, so commit should proceed


# ── Acceptance test: filler-only not committed ────────────────────────────────

def test_filler_only_answer_not_committed():
    """If the entire answer is filler, is_filler_only should return True -> system should re-ask."""
    assert is_filler_only("Great question, exactly.") is True
    # Ensure no substantive content leaks through
    cleaned = sanitize_answer("Great question, exactly.")
    assert len(cleaned.strip()) < 10  # trivially short remainder or empty


# ── Acceptance test: side_fact_extraction uses cleaned answer ─────────────────

def test_side_fact_extraction_uses_cleaned_answer():
    """sanitize_answer strips 'You're right' — side-fact extraction gets clean content."""
    raw = "You're right — the real goal is reducing manual mapping from 45 minutes to 15 seconds."
    cleaned = sanitize_answer(raw)
    assert "You're right" not in cleaned
    assert "the real goal is reducing manual mapping" in cleaned
    assert "45 minutes to 15 seconds" in cleaned


# ── Acceptance test: generated question does not include filler ───────────────

def test_generated_question_does_not_include_filler():
    """
    Simulate the evidence injection path:
    If a contaminated QA entry is stored, _qa_texts_for_sections (via the
    read-time guard in section_inference) must return a sanitized text
    that does not include the filler phrase.
    """
    from utils.section_inference import _qa_texts_for_sections

    # Simulate a contaminated store entry (as if the old system stored filler)
    contaminated_store = {
        "headliner:iter_0:round_1": {
            "section_id": "headliner",
            "answer": "Good clarifying question — the goal is same-day onboarding for supplier feeds.",
            "questions": "What does your product do?",
            "version": 1,
        }
    }
    texts = _qa_texts_for_sections(["headliner"], contaminated_store, {})

    assert len(texts) == 1
    src, text = texts[0]
    assert "Good clarifying question" not in text, (
        f"Filler phrase found in evidence text: {text!r}"
    )
    assert "same-day onboarding" in text, (
        f"Substantive content missing from evidence text: {text!r}"
    )


# ── Evidence contamination guard integration test ─────────────────────────────

def test_section_inference_goals_not_contaminated_by_filler():
    """_infer_goals should not produce 'Great clarifying question' in candidates/evidence."""
    from utils.section_inference import infer_section_candidates

    state = {
        "confirmed_qa_store": {
            "problem_statement:iter_0:round_1": {
                "section_id": "problem_statement",
                "answer": "Great clarifying question — manual mapping takes 4 hours per week. We need to reduce this significantly.",
                "questions": "What is the core pain?",
                "version": 1,
            }
        },
        "prd_sections": {},
    }

    result = infer_section_candidates("goals", state)

    for candidate in result.get("candidate_items", []):
        assert "Great clarifying question" not in candidate, (
            f"Filler phrase leaked into candidate: {candidate!r}"
        )
    for ev in result.get("evidence", []):
        assert "Great clarifying question" not in ev, (
            f"Filler phrase leaked into evidence: {ev!r}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Edge-case tests — 10 cases covering new filler patterns and Strategy 0
# ═══════════════════════════════════════════════════════════════════════════════


def test_ec01_polite_agreement_prefix():
    """EC-01: 'Yes exactly —' removes agreement filler, preserves correction framing."""
    raw = "Yes exactly — the real issue is supplier-specific edge cases."
    cleaned = sanitize_answer(raw)
    assert "Yes exactly" not in cleaned
    assert "the real issue is supplier-specific edge cases" in cleaned


def test_ec02_disagreement_prefix():
    """EC-02: 'No not really,' removes soft disagreement while preserving the negation meaning."""
    raw = "No not really, the problem is not speed but decision inconsistency."
    cleaned = sanitize_answer(raw)
    assert "No not really" not in cleaned
    assert "the problem is not speed but decision inconsistency" in cleaned
    # The substantive negation ("not") must survive
    assert "not speed" in cleaned


def test_ec03_thanks_prefix_drops_gratitude_keeps_metric():
    """EC-03: 'Thanks, that makes sense.' is a standalone filler sentence — drops it, keeps metric."""
    raw = "Thanks, that makes sense. The target is to reduce manual review from 30% to 5%."
    cleaned = sanitize_answer(raw)
    assert "Thanks" not in cleaned
    assert "that makes sense" not in cleaned
    assert "30%" in cleaned
    assert "5%" in cleaned


def test_ec04_meta_comment_before_answer():
    """EC-04: 'I see what you're asking.' is a standalone meta opener — strips it, keeps workflow."""
    raw = "I see what you're asking. The workflow starts with supplier CSV intake."
    cleaned = sanitize_answer(raw)
    assert "I see what you" not in cleaned
    assert "The workflow starts with supplier CSV intake" in cleaned


def test_ec05_filler_only_with_punctuation():
    """EC-05: 'Great question!' alone must not be committed as evidence."""
    assert is_filler_only("Great question!") is True
    cleaned = sanitize_answer("Great question!")
    assert len(cleaned.strip()) < 10


def test_ec06_filler_plus_list_preserves_numbered_structure():
    """EC-06: 'Sure —' before a numbered list: removes filler, keeps list structure intact."""
    raw = "Sure — 1. supplier CSV intake, 2. manual matching, 3. senior review."
    cleaned = sanitize_answer(raw)
    assert "Sure" not in cleaned.split()[0] if cleaned else True
    assert "1. supplier CSV intake" in cleaned
    assert "2. manual matching" in cleaned
    assert "3. senior review" in cleaned


def test_ec07_voice_transcript_filler():
    """EC-07: 'Um yeah so basically' speech filler is removed without losing the baseline value."""
    raw = "Um yeah so basically the baseline is 45 minutes per batch."
    cleaned = sanitize_answer(raw)
    assert "Um" not in cleaned
    assert "yeah" not in cleaned
    assert "basically" not in cleaned or cleaned.startswith("basically") is False
    assert "45 minutes per batch" in cleaned


def test_ec08_actually_correction_opener_strips_but_preserves_correction():
    """EC-08: 'Actually,' is a light correction marker — strips it, preserves negation and intent."""
    raw = "Actually, the goal is not automation for its own sake; it is reducing exception handling."
    cleaned = sanitize_answer(raw)
    assert not cleaned.lower().startswith("actually")
    # The correction content must be preserved completely
    assert "not automation for its own sake" in cleaned
    assert "reducing exception handling" in cleaned


def test_ec09_no_false_positive_on_domain_words():
    """EC-09: Domain language that incidentally resembles filler must pass through unchanged."""
    raw = "The system should handle fuzzy matching, supplier aliases, and manual overrides."
    cleaned = sanitize_answer(raw)
    assert cleaned == raw, f"False positive — raw was modified: {cleaned!r}"


def test_ec10_idempotency_on_already_cleaned_answer():
    """EC-10: Calling sanitize_answer twice must return identical output."""
    already_clean = "the volume is what kills the clock."
    first = sanitize_answer(already_clean)
    second = sanitize_answer(first)
    assert first == already_clean, f"First pass mutated clean input: {first!r}"
    assert second == first, f"Second pass not idempotent: {second!r} != {first!r}"
