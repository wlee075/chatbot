"""utils/filler_sanitizer.py — strips conversational filler from user answers.

Purpose
-------
Users often prefix substantive answers with social lubricant phrases such as
"Great clarifying question — …" or "You're right, the real goal is …".
When these are committed verbatim to confirmed_qa_store the downstream
section-inference pipeline picks up the filler sentence as signal, producing
goals/metrics/question candidates that literally contain the filler text.

This module provides two public functions:

  sanitize_answer(raw: str) -> str
      Returns the substantive clause with leading filler stripped.
      The original string is returned unchanged if no filler is detected.

  is_filler_only(text: str) -> bool
      Returns True when the supplied text contains only filler and no
      substantive content (after sanitisation the remainder is empty or
      trivially short).

Both functions are pure (no I/O, no LLM calls).
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Filler phrase patterns — opening social / meta clauses to strip.
# Each entry is a raw regex fragment that matches the filler phrase at the
# START of a string (case-insensitive).  Ordered from most specific to least.
# ---------------------------------------------------------------------------

_FILLER_FRAGMENTS: list[str] = [
    # Praise / affirmation directed at the assistant
    r"great\s+(?:clarifying\s+)?question",
    r"good\s+(?:clarifying\s+)?question",
    r"excellent\s+(?:clarifying\s+)?question",
    r"fair\s+(?:clarifying\s+)?question",
    r"that\s*[''`]?s?\s+a\s+(?:great|good|fair|excellent)\s+(?:clarifying\s+)?question",
    # Agreement / acknowledgement
    r"you\s*[''`]?re\s+right",
    r"you\s+are\s+right",
    r"you\s*[''`]?re\s+correct",
    r"you\s+are\s+correct",
    r"that\s*[''`]?s?\s+(?:a\s+)?(?:good|fair|great)\s+point",
    r"exactly(?:\s+right)?",
    r"absolutely",
    r"correct(?:ly)?",
    r"yes[,.]?\s*(?:exactly|correct|absolutely|indeed|that\s+is\s+right)?",
    # Disagreement / soft negation
    r"no[,\s]+not\s+(?:really|exactly|at\s+all)",
    # Gratitude / acknowledgement of understanding
    r"thanks?\s*(?:so\s+much)?",
    r"thank\s+you\s*(?:so\s+much)?",
    # Meta-response / comprehension confirmation
    r"i\s+see\s+what\s+you(?:'re|\s+are)\s+(?:asking|getting\s+at|saying)",
    r"i\s+(?:get|understand)\s+(?:it|that|the\s+question|what\s+you\s+mean)",
    # Framing / meta
    r"from\s+my\s+perspective[,]?",
    r"to\s+answer\s+your\s+question[,]?",
    r"in\s+short[,]?",
    r"in\s+(?:a\s+)?(?:brief|summary)[,]?",
    r"to\s+be\s+(?:clear|specific|honest|direct)[,]?",
    r"my\s+answer\s+(?:is|would\s+be)[,]?",
    r"the\s+answer\s+(?:is|would\s+be)[,]?",
    r"to\s+put\s+it\s+simply[,]?",
    r"simply\s+put[,]?",
    r"as\s+I\s+(?:mentioned|said|noted)[,]?",
    r"sure[,.]?\s*(?:so)?",
    # Light correction opener (preserves the correction content that follows)
    r"actually[,]?",
    # Speech / voice transcript filler
    r"um+\s*,?\s*(?:(?:yeah|yes)\s*,?\s*)?(?:so\s+)?(?:basically\s+)?",
    r"uh+\s*,?\s*(?:(?:yeah|yes)\s*,?\s*)?(?:so\s+)?(?:basically\s+)?",
]

# Build a single compiled regex: matches the filler AT THE START of a string,
# optionally followed by punctuation and whitespace before the substantive part.
_FILLER_RE = re.compile(
    r"^(?:" + "|".join(_FILLER_FRAGMENTS) + r")"
    r"[\s,.\-–—!]*",
    re.IGNORECASE,
)

# Second-pass: match "filler + delimiter + content" constructs.
# Handles em-dash / en-dash / colon separators after filler phrases.
_FILLER_DELIMITER_RE = re.compile(
    r"^(?:" + "|".join(_FILLER_FRAGMENTS) + r")"
    r"\s*(?:[,.\-–—:!]+)\s*",
    re.IGNORECASE,
)

# Filler-only patterns: entire input is filler with no substantive payload.
_FILLER_ONLY_RE = re.compile(
    r"^(?:(?:" + "|".join(_FILLER_FRAGMENTS) + r")[\s,.\-–—!]*)+[.!?]?$",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# First-sentence filler detector (for multi-sentence inputs).
# Matches a COMPLETE sentence (ending in .!?) that is entirely filler —
# used by Strategy 0 to drop gratitude / comprehension-confirmation openers.
# ---------------------------------------------------------------------------

_FIRST_SENTENCE_FILLER_RE = re.compile(
    r"^(?:"
    + "|".join(_FILLER_FRAGMENTS)
    # "Thanks, that makes sense." / "Thanks, that clarifies things."
    + r"|thanks?[,.]?\s*(?:that\s+(?:really\s+)?(?:makes?\s+sense|helps?|clarifies?)[!.]?)"
    # "Thank you, that makes sense."
    + r"|thank\s+you[,.]?\s*(?:that\s+(?:really\s+)?(?:makes?\s+sense|helps?|clarifies?)[!.]?)"
    # "I see what you're asking." / "I see." (when standing alone)
    + r"|i\s+see[.!]?"
    # "Got it." / "Understood."
    + r"|got\s+it[.!]?"
    + r"|understood[.!]?"
    + r"|noted[.!]?"
    + r")[\s,.\-–—!?]*$",
    re.IGNORECASE,
)

# _MULTI_SENTENCE_RE splits text into (first_sentence, remainder).
# The first sentence ends at the first . ! ? followed by whitespace + capital / digit.
_MULTI_SENTENCE_RE = re.compile(
    r"^([^.!?]+[.!?])\s+(\S.*)",
    re.DOTALL,
)

# Minimum character threshold for "substantive" content after stripping.
_MIN_SUBSTANTIVE_CHARS = 10


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def sanitize_answer(raw: str) -> str:
    """Strip leading conversational filler from *raw* and return the remainder.

    Strategies applied in order:
    0. Multi-sentence — if the first complete sentence is pure filler (e.g.,
       "Thanks, that makes sense."), strip it and keep the rest.
       e.g. "Thanks, that makes sense. The target is 30% to 5%."
            → "The target is 30% to 5%."
    1. Filler → delimiter (em-dash, comma, colon) → substantive clause.
       e.g. "Great clarifying question — the volume kills the clock"
            → "the volume kills the clock"
    2. Plain filler prefix with no delimiter.
       e.g. "You're right the goal is reducing manual mapping"
            → "the goal is reducing manual mapping"
    3. If no filler is detected, return the original string unchanged.

    The function is idempotent: calling it twice returns the same result.
    """
    text = raw.strip()
    if not text:
        return text

    # Strategy 0: multi-sentence — first sentence is a standalone filler sentence.
    m_multi = _MULTI_SENTENCE_RE.match(text)
    if m_multi:
        first_sent = m_multi.group(1).strip()
        rest = m_multi.group(2).strip()
        if rest and _FIRST_SENTENCE_FILLER_RE.match(first_sent):
            text = rest  # drop filler opener, continue to check rest for more filler

    # Strategy 1: strip filler + delimiter
    m = _FILLER_DELIMITER_RE.match(text)
    if m:
        remainder = text[m.end():].strip()
        if remainder:
            return remainder

    # Strategy 2: strip filler-only prefix (no mandatory delimiter)
    m = _FILLER_RE.match(text)
    if m:
        remainder = text[m.end():].strip()
        if remainder:
            return remainder
        # The entire string was filler — return empty to trigger is_filler_only
        return ""

    return text


def is_filler_only(text: str) -> bool:
    """Return True if *text* is composed entirely of conversational filler.

    A filler-only answer has no substantive payload after sanitisation.
    The system should NOT commit such answers to the canonical store — it
    should re-ask the question instead.
    """
    stripped = text.strip()
    if not stripped:
        return True

    # Fast path: check against the explicit filler-only regex
    if _FILLER_ONLY_RE.match(stripped):
        return True

    # Slow path: sanitise and check if the remainder is too short to be useful
    remainder = sanitize_answer(stripped)
    substantive_chars = sum(1 for c in remainder if not c.isspace())
    return substantive_chars < _MIN_SUBSTANTIVE_CHARS
