"""utils/answer_guardrail.py — Pre-commit answer validity guardrail.

Runs before any semantic assessment or canonical write.
Prevents accidental keystrokes, noise characters, and semantically empty
replies from advancing section state.

No LLM calls, no I/O.  Pure str → GuardrailResult.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

# ── Patterns ──────────────────────────────────────────────────────────────────

# Words that count as valid short answers when the question context matches
_YES_NO_WORDS: frozenset[str] = frozenset({
    "yes", "no", "yep", "nope", "yeah", "nah", "correct", "wrong",
    "true", "false", "ok", "okay", "sure", "right", "agreed", "disagree",
    "both", "neither", "all", "none",
})

# Domain-relevant single words that are accepted as valid named-entity picks
_DOMAIN_SHORT_WORDS: frozenset[str] = frozenset({
    "sales", "operations", "ops", "engineering", "product", "finance",
    "marketing", "legal", "compliance", "support", "hr", "growth",
    "design", "data", "analytics", "platform", "infra", "infrastructure",
    "security", "all", "maybe", "unsure", "unknown", "tbd",
})

# Symbol/punctuation-only patterns — always noise
_SYMBOL_ONLY_RE = re.compile(r"^[\W_]+$")

# A "word" is one or more consecutive word-characters (letters, digits, underscore)
_WORD_RE = re.compile(r"\w+")

# Binary-question signals in the most-recent chatbot question
_BINARY_QUESTION_RE = re.compile(
    r"\byes or no\b|y/n\b|\bdo you\b|\bdoes your\b|\bis it\b|\bare you\b"
    r"|\bhave you\b|\bwill you\b|\bcan you\b",
    re.IGNORECASE,
)

# Numeric-question signals
_NUMERIC_QUESTION_RE = re.compile(
    r"\bhow many\b|\bhow much\b|\bwhat number\b|\bwhat %\b|\bpercentage\b"
    r"|\bhow long\b|\bhow often\b|\brate\b|\bcount\b|\btarget \w+ value\b",
    re.IGNORECASE,
)

# Choice / named-entity question signals
_CHOICE_QUESTION_RE = re.compile(
    r"\bwho\b|\bwhich team\b|\bwhich department\b|\bwhich option\b"
    r"|\bselect\b|\bchoose\b|\bpick\b|\bname\b|\bwhat role\b",
    re.IGNORECASE,
)


# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class GuardrailResult:
    passed: bool
    reason: str                           # machine-readable tag for logging
    clarification_prompt: str             # user-facing gentle redirect
    score: float                          # 0.0–1.0 internal signal strength
    signals: dict = field(default_factory=dict)  # diagnostic breakdown


# ── Core scoring logic ────────────────────────────────────────────────────────

def check_answer_quality(
    answer: str,
    current_question: str = "",
) -> GuardrailResult:
    """Return a GuardrailResult for the given raw user answer.

    Decision flow (fast-path exits in priority order):
    1. Empty / whitespace-only → reject immediately
    2. Symbol-only / punctuation-only → reject immediately
    3. Single character
        a. Question is binary → allow (user typed "y"/"n")
        b. Otherwise → reject (typo)
    4. Very short (2–3 chars) — check against allow-lists
        a. Yes/No word → allow for binary question
        b. Domain short word → allow
        c. Numeric-only short → allow only for numeric question
        d. Otherwise → reject
    5. Single non-ASCII character with no Latin letters → reject (accidental keystroke)
    6. Repeated same character (>= 4 repetitions) → reject (keyboard mash)
    7. No English word tokens found → reject (gibberish)
    8. Everything else → pass
    """
    raw = answer.strip()

    # ── 1. Empty ──────────────────────────────────────────────────────────────
    if not raw:
        return GuardrailResult(
            passed=False,
            reason="empty_answer",
            clarification_prompt=(
                "I didn't catch a response. Could you answer in a few words?"
            ),
            score=0.0,
            signals={"length": 0},
        )

    # ── 2. Symbol / punctuation only ─────────────────────────────────────────
    if _SYMBOL_ONLY_RE.match(raw):
        return GuardrailResult(
            passed=False,
            reason="symbol_only",
            clarification_prompt=(
                "That looks like it might be a typo. Could you rephrase your answer?"
            ),
            score=0.05,
            signals={"length": len(raw), "symbol_only": True},
        )

    normalized = unicodedata.normalize("NFKC", raw)
    words      = _WORD_RE.findall(raw)
    n_words    = len(words)
    char_len   = len(raw)

    is_binary  = bool(_BINARY_QUESTION_RE.search(current_question))
    is_numeric = bool(_NUMERIC_QUESTION_RE.search(current_question))
    is_choice  = bool(_CHOICE_QUESTION_RE.search(current_question))

    # ── 3. Single character ───────────────────────────────────────────────────
    if char_len == 1:
        if is_binary and raw.lower() in ("y", "n"):
            return GuardrailResult(
                passed=True,
                reason="single_char_binary_ok",
                clarification_prompt="",
                score=0.7,
                signals={"length": 1, "is_binary": True},
            )
        # Single non-ASCII (e.g., "ñ", "ü", "❤") → typo
        return GuardrailResult(
            passed=False,
            reason="single_char_typo",
            clarification_prompt=(
                "I may have caught a typo — could you answer in a full word or phrase?"
            ),
            score=0.0,
            signals={"length": 1, "char": repr(raw)},
        )

    # ── 4. Very short (2–3 chars) ─────────────────────────────────────────────
    if char_len <= 3:
        lower = raw.lower()

        # 4a. Yes/No word
        if lower in _YES_NO_WORDS:
            return GuardrailResult(
                passed=True,
                reason="short_yes_no_ok",
                clarification_prompt="",
                score=0.85,
                signals={"length": char_len, "yes_no": True},
            )

        # 4b. Domain short word
        if lower in _DOMAIN_SHORT_WORDS:
            return GuardrailResult(
                passed=True,
                reason="short_domain_word_ok",
                clarification_prompt="",
                score=0.8,
                signals={"length": char_len, "domain_word": True},
            )

        # 4c. Numeric reply for a numeric question
        if raw.replace(".", "").replace(",", "").isdigit() and is_numeric:
            return GuardrailResult(
                passed=True,
                reason="short_numeric_ok",
                clarification_prompt="",
                score=0.8,
                signals={"length": char_len, "numeric": True, "is_numeric_q": True},
            )

        # All other <=3-char answers
        return GuardrailResult(
            passed=False,
            reason="too_short",
            clarification_prompt=(
                "I didn't get enough detail from that reply. "
                "Could you answer in a few words or a short sentence?"
            ),
            score=0.1,
            signals={"length": char_len, "words": words},
        )

    # ── 5. Single non-ASCII char embedded at start (e.g., "ñ something" is ok) ─
    # But a single non-ASCII char alone was already caught in step 3.
    # Additional check: ALL chars are non-ASCII and there are no Latin word tokens
    if n_words == 0 and any(ord(c) > 127 for c in raw):
        return GuardrailResult(
            passed=False,
            reason="non_ascii_no_words",
            clarification_prompt=(
                "I may have caught a typo — could you clarify your answer?"
            ),
            score=0.05,
            signals={"length": char_len, "non_ascii": True},
        )

    # ── 6. Keyboard mash — repeated same char (e.g., "aaaa", "kkkk") ─────────
    if len(set(raw.lower().replace(" ", ""))) == 1 and char_len >= 4:
        return GuardrailResult(
            passed=False,
            reason="repeated_char_mash",
            clarification_prompt=(
                "That looks like it might be a keyboard error. "
                "Could you answer in a few words?"
            ),
            score=0.05,
            signals={"length": char_len, "repeated_char": True},
        )

    # ── 7. No recognisable word tokens (e.g., "???", "...", "!!!") ────────────
    if n_words == 0:
        return GuardrailResult(
            passed=False,
            reason="no_word_tokens",
            clarification_prompt=(
                "I didn't catch a clear answer there. "
                "Could you rephrase in a word or two?"
            ),
            score=0.05,
            signals={"length": char_len, "words": []},
        )

    # ── 8. Default: pass ──────────────────────────────────────────────────────
    return GuardrailResult(
        passed=True,
        reason="ok",
        clarification_prompt="",
        score=1.0,
        signals={"length": char_len, "words": n_words},
    )
