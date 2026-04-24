"""utils/nemo_guardrails_gateway.py — Real NeMo Guardrails PRD Gateway.

Architecture (fastembed-first, following the article's star-schema intent pattern):
  1. Fast pre-filter — regex for obvious noise/task_request (~0ms, saves all other overhead)
  2. NeMo fastembed intent matching — builds a cosine similarity index from `user_messages`
     defined in rails.co, then finds the closest matching intent
     • Uses `fastembed.TextEmbedding` (same model NeMo uses internally)
     • classifier_source = "NEMO_FASTEMBED"
     • Threshold and margin control escalation
  3. NeMo LLM fallback — calls `LLMRails.generate_async` with full dialog flow only when
     fastembed confidence is ambiguous
     • classifier_source = "NEMO_LLM"
  4. SAFE_FALLBACK — fail-closed; if NeMo runtime fails entirely

classifier_source values (as specified in the implementation plan):
  "FAST_REGEX"       fast pre-filter hit (obvious noise or task)
  "NEMO_FASTEMBED"   fastembed embedding classified cleanly
  "NEMO_LLM"         LLM disambiguation used
  "SAFE_FALLBACK"    runtime error, fail-closed

The embedding index is built once from RailsConfig.user_messages and cached.
The LLMRails instance is also a singleton, only used for the LLM fallback path.

Public API (unchanged from previous implementation):
  result = run_nemo_guardrails_gateway(state, user_message)
  result = safe_fallback_result()
"""
from __future__ import annotations

import asyncio
import logging
import numpy as np
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── Thresholds ────────────────────────────────────────────────────────────────
_EMBED_CONFIDENCE_THRESHOLD = 0.52   # top1 score must exceed this
_EMBED_MARGIN = 0.06                 # top1 - top2 margin (gap between candidates)
_FASTEMBED_MODEL = "BAAI/bge-small-en-v1.5"

# ── Message classes ────────────────────────────────────────────────────────────
MESSAGE_CLASSES = frozenset({
    "valid_answer", "partial_answer", "user_correction", "meta_request",
    "task_request", "off_topic", "noise_input", "contradiction", "cross_section",
})

# Routing map: message_class → graph node name
_ROUTE_MAP: dict[str, str] = {
    "noise_input":     "await_answer",
    "task_request":    "task_request_blocked",
    "meta_request":    "answer_clarification",
    "off_topic":       "answer_clarification",
    "contradiction":   "contradiction_validator",
    "user_correction": "numeric_validation",
    "cross_section":   "numeric_validation",
    "partial_answer":  "numeric_validation",
    "valid_answer":    "numeric_validation",
}

# Permission table: (allow_commit, allow_section_complete, allow_advance)
_PERMISSIONS: dict[str, tuple[bool, bool, bool]] = {
    "valid_answer":    (True,  True,  True),
    "partial_answer":  (True,  False, False),
    "user_correction": (True,  False, False),
    "cross_section":   (True,  False, False),
    "contradiction":   (False, False, False),
    "meta_request":    (False, False, False),
    "task_request":    (False, False, False),
    "off_topic":       (False, False, False),
    "noise_input":     (False, False, False),
}

# Map from Colang 1.0 "define user <label>" names → normalised message_class
_LABEL_MAP: dict[str, str] = {
    "valid answer":          "valid_answer",
    "partial answer":        "partial_answer",
    "user correction":       "user_correction",
    "meta request":          "meta_request",
    "task request":          "task_request",
    "off topic":             "off_topic",
    "noise input":           "noise_input",
    "contradiction":         "contradiction",
    "cross section answer":  "cross_section",
    # normalised underscored forms
    "valid_answer":          "valid_answer",
    "partial_answer":        "partial_answer",
    "user_correction":       "user_correction",
    "meta_request":          "meta_request",
    "task_request":          "task_request",
    "off_topic":             "off_topic",
    "noise_input":           "noise_input",
    "contradiction":         "contradiction",
    "cross_section_answer":  "cross_section",
    "cross_section":         "cross_section",
}


# ── Result dataclass ───────────────────────────────────────────────────────────
@dataclass
class GatewayResult:
    message_class:           str
    confidence:              float
    allow_commit:            bool
    allow_section_complete:  bool
    allow_advance:           bool
    route_to:                str
    clarification_needed:    bool
    corrected_prior_content: bool
    target_section_override: Optional[str]
    classifier_source:       str
    guardrail_reason:        str
    signals:                 dict = field(default_factory=dict)


def _make_result(
    message_class: str,
    source: str,
    reason: str,
    confidence: float = 1.0,
    target_section: Optional[str] = None,
    corrected: bool = False,
    signals: Optional[dict] = None,
) -> GatewayResult:
    allow_commit, allow_complete, allow_advance = _PERMISSIONS[message_class]
    return GatewayResult(
        message_class=message_class,
        confidence=confidence,
        allow_commit=allow_commit,
        allow_section_complete=allow_complete,
        allow_advance=allow_advance,
        route_to=_ROUTE_MAP[message_class],
        clarification_needed=message_class in ("noise_input", "meta_request", "off_topic"),
        corrected_prior_content=corrected or (message_class == "user_correction"),
        target_section_override=target_section,
        classifier_source=source,
        guardrail_reason=reason,
        signals=signals or {},
    )


# ── Fastembed intent index ─────────────────────────────────────────────────────
# Built once from NeMo RailsConfig.user_messages and cached in module scope.

_embed_model = None
_intent_utterances: list[tuple[str, str]] = []  # list of (intent_label, utterance_text)
_embed_matrix: Optional[np.ndarray] = None         # shape (N, D)
_embed_index_built = False
_embed_index_error: Optional[Exception] = None


def _get_embed_model():
    """Lazy-load the fastembed TextEmbedding model singleton."""
    global _embed_model
    if _embed_model is None:
        from fastembed import TextEmbedding
        _embed_model = TextEmbedding(model_name=_FASTEMBED_MODEL)
        logger.info("fastembed TextEmbedding model loaded: %s", _FASTEMBED_MODEL)
    return _embed_model


def _build_embedding_index():
    """Build cosine-similarity index over all user_messages utterances from rails.co."""
    global _intent_utterances, _embed_matrix, _embed_index_built, _embed_index_error
    if _embed_index_built:
        return
    if _embed_index_error:
        raise _embed_index_error
    try:
        from dotenv import load_dotenv
        load_dotenv()

        from nemoguardrails import RailsConfig
        config_path = str(Path(__file__).parent.parent / "config" / "nemo_guardrails")
        config = RailsConfig.from_path(config_path)

        # Flatten all utterances: [(intent_label, utterance), ...]
        pairs: list[tuple[str, str]] = []
        for intent_label, utterances in config.user_messages.items():
            for utt in utterances:
                pairs.append((intent_label, utt.strip()))

        if not pairs:
            raise ValueError("No user_messages found in NeMo rails config — check rails.co")

        model = _get_embed_model()
        texts = [utt for _, utt in pairs]
        embeddings = list(model.embed(texts))  # list of np arrays
        matrix = np.array(embeddings, dtype=np.float32)

        # L2 normalise for cosine similarity via dot product
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1.0, norms)
        matrix = matrix / norms

        _intent_utterances = pairs
        _embed_matrix = matrix
        _embed_index_built = True
        logger.info(
            "NeMo fastembed index built: %d utterances across %d intents",
            len(pairs), len(config.user_messages),
        )
    except Exception as exc:
        _embed_index_error = exc
        logger.error("Failed to build fastembed intent index: %s", exc)
        raise


def _fastembed_classify(text: str) -> tuple[str, float, float]:
    """Return (intent_label_raw, top1_score, top1_minus_top2).

    Raises if the index is not built or the model fails.
    """
    _build_embedding_index()
    model = _get_embed_model()

    query_vec = np.array(list(model.embed([text]))[0], dtype=np.float32)
    norm = np.linalg.norm(query_vec)
    if norm > 0:
        query_vec = query_vec / norm

    scores = _embed_matrix @ query_vec   # cosine similarities, shape (N,)

    top_indices = np.argsort(scores)[::-1]
    top1_idx = top_indices[0]
    top1_score = float(scores[top1_idx])
    top1_label = _intent_utterances[top1_idx][0]

    # Find the top score from a *different* intent (for margin check)
    top2_score = 0.0
    for idx in top_indices[1:]:
        if _intent_utterances[idx][0] != top1_label:
            top2_score = float(scores[idx])
            break

    margin = top1_score - top2_score
    return top1_label, top1_score, margin


# ── NeMo LLM rails singleton (used only for LLM fallback) ────────────────────
_rails = None
_rails_init_error: Optional[Exception] = None


def _get_rails():
    """Lazy-init the NeMo LLMRails singleton — used only when LLM fallback is needed."""
    global _rails, _rails_init_error
    if _rails is not None:
        return _rails
    if _rails_init_error is not None:
        raise _rails_init_error
    try:
        from dotenv import load_dotenv
        load_dotenv()

        from nemoguardrails import RailsConfig, LLMRails
        config_path = str(Path(__file__).parent.parent / "config" / "nemo_guardrails")
        config = RailsConfig.from_path(config_path)

        # Inject the app's Gemini LLM so NeMo doesn't try to initialise its own
        try:
            from graph.nodes import _get_llm
            app_llm = _get_llm()
        except Exception:
            app_llm = None

        _rails = LLMRails(config, llm=app_llm)
        logger.info("NeMo LLMRails initialised (llm=%s)", type(app_llm).__name__ if app_llm else "None")
        return _rails
    except Exception as exc:
        _rails_init_error = exc
        logger.error("NeMo LLMRails init failed: %s", exc)
        raise


# ── NeMo LLM fallback (async) ─────────────────────────────────────────────────

async def _nemo_llm_classify_async(rails, message: str) -> tuple[Optional[str], float]:
    """Call NeMo LLM and extract intent from internal events.

    Following the article's pattern: enable internal_events logging,
    extract UserIntent / user_intent events from the log.

    Returns (intent_label_raw, confidence).
    """
    from nemoguardrails.rails.llm.options import GenerationOptions, GenerationLogOptions

    options = GenerationOptions(log=GenerationLogOptions(internal_events=True))
    try:
        response = await rails.generate_async(
            messages=[{"role": "user", "content": message}],
            options=options,
        )
    except Exception as exc:
        logger.warning("NeMo LLM generate_async failed: %s", exc)
        return None, 0.0

    internal_events = getattr(getattr(response, "log", None), "internal_events", None) or []
    logger.debug(
        "nemo_guardrails_internal_event_extracted events=%d",
        len(internal_events),
    )

    # Look for user_intent or UserIntent event
    for evt in reversed(internal_events):
        etype = evt.get("type", "")
        if etype in ("user_intent", "UserIntent"):
            intent = evt.get("intent") or evt.get("name") or evt.get("label")
            confidence = float(evt.get("confidence", 0.75))
            logger.info("nemo_guardrails_internal_event_extracted intent=%r", intent)
            return intent, confidence

    # If no intent event found, fall back to colang_history parsing
    colang_hist = getattr(getattr(response, "log", None), "colang_history", None) or ""
    if "user " in colang_hist:
        # Try to parse "  user task request" style lines
        for line in colang_hist.split("\n"):
            stripped = line.strip()
            if stripped.startswith("user ") and not stripped.startswith("user ..."):
                intent_raw = stripped[5:].strip()
                return intent_raw, 0.65

    return None, 0.0


def _run_async(coro):
    """Run an async coroutine from a sync context, handling existing event loops."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import nest_asyncio
            nest_asyncio.apply()
            return loop.run_until_complete(coro)
        return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)


# ── Fast pre-filter (regex, no NeMo overhead) ─────────────────────────────────
_NOISE_SYMBOL_RE = re.compile(r"^[\W_]+$")
_NOISE_KEYBOARD_RE = re.compile(
    r"^(asdf|qwer|zxcv|hjkl|qwerty|asdfgh|zxcvbn|[b-df-hj-np-tv-z]{2,6})$",
    re.IGNORECASE,
)
_WORD_RE = re.compile(r"\w+")

_TASK_FAST_RE = re.compile(
    r"\b("
    r"write (the )?code|code (this|it)|implement (this|it)|"
    r"generate (the )?(pdf|prd|doc|report|slide|final)|"
    r"(draft|write|produce|export|build|create|compile|finalize|ship) "
    r"(it|this|the prd|the pdf|the doc|the report|the artifact|a (report|slide|table)|final)"
    r")\b",
    re.IGNORECASE,
)


def _fast_prefilter(text: str) -> Optional[str]:
    """Return a message_class string if the fast regex catches it, else None."""
    raw = text.strip()
    if not raw:
        return "noise_input"
    words = _WORD_RE.findall(raw)
    if _NOISE_SYMBOL_RE.match(raw):
        return "noise_input"
    if len(raw) == 1:
        return "noise_input"
    if len(words) == 1 and _NOISE_KEYBOARD_RE.match(raw):
        return "noise_input"
    if len(set(raw.lower())) == 1 and len(raw) >= 4:
        return "noise_input"
    if _TASK_FAST_RE.search(raw):
        return "task_request"
    return None


# ── Cross-section target inference ────────────────────────────────────────────
_CROSS_SECTION_SIGNALS: dict[str, re.Pattern] = {
    "success_metrics": re.compile(
        r"\b(kpi|metric|target|baseline|reduce from|from .* to .*|\d+%|"
        r"\d+ (min|sec|hours?|days?)|success (criteri|measure)|okr|nps|sla)\b", re.I),
    "key_stakeholders": re.compile(
        r"\b(ceo|cto|vp|director|manager|team lead|stakeholder|exec|"
        r"engineering|product|sales|ops|legal) team\b", re.I),
    "risks": re.compile(
        r"\b(risk is|the risk|failure (mode|case)|could cause|might block|potential issue)\b", re.I),
    "assumptions": re.compile(r"\b(assume|assuming|we expect|it is assumed)\b", re.I),
    "timeline": re.compile(
        r"\b(\d+ (month|week|day|sprint|quarter)s?|mvp|rollout|milestone|launch date)\b", re.I),
    "proposed_solution": re.compile(
        r"\b(we (should|will|plan to)|the (solution|platform|system|product) (should|will|would))\b", re.I),
}


def _infer_cross_section_target(text: str, current_section_id: str) -> Optional[str]:
    for sec_id, pat in _CROSS_SECTION_SIGNALS.items():
        if sec_id != current_section_id and pat.search(text):
            return sec_id
    return None


# ── Section-relevance signal tables (used by override) ────────────────────────
# Each section lists keyword stems / whole-words that indicate the user is
# answering a PRD elicitation question for that section. A match against ≥2
# different signal groups overrides an off_topic classification to valid_answer.
# Signals are lower-cased for comparison; multi-word phrases are also checked.

_SECTION_SIGNALS: dict[str, list[str]] = {
    # Background / current state
    "background": [
        "workflow", "step", "steps", "process", "current state", "currently",
        "who", "alex", "jamie", "frequency", "daily", "weekly", "weekday",
        "tool", "tools", "excel", "csv", "supplier", "mapped", "mapping",
        "friction", "manual", "validation", "production", "handoff", "batch",
        "batches", "run", "export", "import", "bottleneck", "tedious",
        "downstream", "upstream", "department", "analyst", "team",
    ],
    "problem_statement": [
        "problem", "issue", "pain", "pain point", "struggle", "challenge",
        "impact", "cost", "consequence", "matter", "why", "losing",
        "delayed", "slow", "error", "mistake", "manual",
    ],
    "proposed_solution": [
        "solution", "solve", "automate", "platform", "system", "tool",
        "dashboard", "pipeline", "algorithm", "recommend", "propose",
    ],
    "success_metrics": [
        "metric", "kpi", "measure", "target", "baseline", "goal", "okr",
        "%", "percent", "reduce", "improve", "increase", "accuracy",
        "sla", "latency", "time", "rate",
    ],
    "key_stakeholders": [
        "stakeholder", "team", "owner", "cto", "ceo", "vp", "director",
        "manager", "engineer", "product", "design", "ops", "finance",
        "legal", "exec", "sponsor",
    ],
    "risks": [
        "risk", "assumption", "constraint", "dependency", "blocker",
        "failure", "mitigation", "concern", "issue", "uncertainty",
    ],
    "non_goals": [
        "not", "out of scope", "not build", "won't", "will not",
        "exclude", "defer", "later", "future",
    ],
    "timeline": [
        "week", "month", "quarter", "sprint", "milestone", "deadline",
        "launch", "rollout", "phase", "q1", "q2", "q3", "q4", "mvp",
    ],
    "summary": [
        "we are building", "we want to", "goal is", "we need",
        "product", "service", "app", "feature",
    ],
}

# Minimum number of distinct signal words that must match to override
_OVERRIDE_MIN_SIGNAL_COUNT = 2


def _section_relevance_override(
    message_class: str,
    user_message: str,
    section_id: str,
    source: str,
    top1_score: float,
) -> tuple[str, str]:  # (final_class, override_reason)
    """Override off_topic if the answer contains expected section signals.

    Returns (final_class, reason). If override fires, final_class differs from
    message_class. Callers are responsible for emitting the three required log events:
      nemo_guardrails_raw_classification
      nemo_guardrails_section_relevance_override
      nemo_guardrails_final_classification
    """
    if message_class != "off_topic":
        return message_class, ""

    text_lower = user_message.lower()
    signals = _SECTION_SIGNALS.get(section_id, []) or _SECTION_SIGNALS.get("background", [])

    matched: list[str] = []
    for sig in signals:
        if sig in text_lower:
            matched.append(sig)

    if len(matched) >= _OVERRIDE_MIN_SIGNAL_COUNT:
        reason = (
            f"off_topic overridden to valid_answer: "
            f"{len(matched)} section signals matched in section '{section_id}': "
            + ", ".join(matched[:8])
        )
        return "valid_answer", reason

    return message_class, ""


# ── Main public function ───────────────────────────────────────────────────────

def run_nemo_guardrails_gateway(state: dict, user_message: str) -> GatewayResult:
    """Classify user_message via the NeMo Guardrails PRD Gateway.

    Following the article's star-schema pattern:
      1. Fast pre-filter (regex, ~0ms) for obvious noise and task requests
      2. NeMo fastembed embedding similarity — classify against rails.co user intents
      3. LLM fallback — only when fastembed confidence < threshold or margin < _EMBED_MARGIN
      4. SAFE_FALLBACK — fail-closed if any runtime error occurs
    """
    raw = user_message.strip()

    # ── 1. Fast pre-filter ──────────────────────────────────────────────────────
    prefilter_class = _fast_prefilter(raw)
    if prefilter_class:
        logger.info("nemo_guardrails_classified source=FAST_REGEX class=%s", prefilter_class)
        return _make_result(
            prefilter_class, "FAST_REGEX",
            f"fast pre-filter: {prefilter_class}",
            confidence=1.0,
            signals={"filter": "regex"},
        )

    # ── Section context ───────────────────────────────────────────────────────
    section_idx = state.get("section_index", 0)
    try:
        from config.sections import PRD_SECTIONS
        current_section_id = PRD_SECTIONS[section_idx].id
    except Exception:
        current_section_id = ""

    # ── 2. NeMo fastembed classification ────────────────────────────────────
    try:
        intent_raw, top1_score, margin = _fastembed_classify(raw)
    except Exception as exc:
        logger.error("nemo_guardrails_safe_fallback_used reason=fastembed_error error=%s", exc)
        return safe_fallback_result(reason=f"fastembed error: {exc}")

    logger.info(
        "nemo_guardrails_classified source=NEMO_FASTEMBED "
        "intent=%r score=%.3f margin=%.3f threshold=%.3f margin_threshold=%.3f",
        intent_raw, top1_score, margin, _EMBED_CONFIDENCE_THRESHOLD, _EMBED_MARGIN,
    )

    # ── 3. Ambiguity check → LLM fallback ───────────────────────────────────
    if top1_score < _EMBED_CONFIDENCE_THRESHOLD or margin < _EMBED_MARGIN:
        logger.info(
            "nemo_guardrails_llm_fallback_used reason=low_confidence "
            "score=%.3f margin=%.3f",
            top1_score, margin,
        )
        try:
            rails = _get_rails()
            llm_intent, llm_conf = _run_async(_nemo_llm_classify_async(rails, raw))
            if llm_intent:
                intent_raw = llm_intent
                top1_score = llm_conf
                source = "NEMO_LLM"
                logger.info(
                    "nemo_guardrails_classified source=NEMO_LLM intent=%r conf=%.3f",
                    intent_raw, top1_score,
                )
            else:
                # LLM returned nothing — use fastembed result if above noise floor
                source = "NEMO_FASTEMBED" if top1_score > 0.3 else "SAFE_FALLBACK"
        except Exception as exc:
            logger.warning("LLM fallback failed: %s — using fastembed result", exc)
            source = "NEMO_FASTEMBED" if top1_score > 0.3 else "SAFE_FALLBACK"
    else:
        source = "NEMO_FASTEMBED"

    if source == "SAFE_FALLBACK":
        return safe_fallback_result(reason="ambiguous classification, below safe floor")

    # ── Map intent label → message_class ────────────────────────────────────
    label = (intent_raw or "").lower().strip()
    message_class = _LABEL_MAP.get(label, "")

    if message_class not in MESSAGE_CLASSES:
        logger.warning(
            "nemo_guardrails_safe_fallback_used reason=unknown_intent raw=%r", intent_raw
        )
        return safe_fallback_result(reason=f"unknown intent: {intent_raw!r}")

    logger.info(
        "nemo_guardrails_raw_classification source=%s class=%s confidence=%.3f",
        source, message_class, top1_score,
    )

    # ── Section-relevance override: fix off_topic false positives ───────────
    final_class, override_reason = _section_relevance_override(
        message_class, raw, current_section_id, source, top1_score
    )
    if final_class != message_class:
        logger.info(
            "nemo_guardrails_section_relevance_override "
            "original_class=%s final_class=%s section_id=%s reason=%r",
            message_class, final_class, current_section_id, override_reason,
        )
        message_class = final_class
    else:
        logger.debug(
            "nemo_guardrails_section_relevance_override not_triggered class=%s",
            message_class,
        )

    logger.info(
        "nemo_guardrails_final_classification source=%s class=%s confidence=%.3f",
        source, message_class, top1_score,
    )

    # Cross-section: infer target section from signal words
    cross_target: Optional[str] = None
    if message_class == "cross_section":
        cross_target = _infer_cross_section_target(raw, current_section_id)

    return _make_result(
        message_class, source,
        override_reason if override_reason else f"NeMo {source} classified as {message_class}",
        confidence=top1_score,
        target_section=cross_target,
        corrected=(message_class == "user_correction"),
        signals={
            "nemo_intent_raw": intent_raw or "",
            "section_id": current_section_id,
            "relevance_override": bool(override_reason),
        },
    )


# ── Safe fallback ─────────────────────────────────────────────────────────────

def safe_fallback_result(reason: str = "safe fallback") -> GatewayResult:
    """Conservative fail-closed result: no commit, no advance, clarify."""
    return GatewayResult(
        message_class="noise_input",
        confidence=0.0,
        allow_commit=False,
        allow_section_complete=False,
        allow_advance=False,
        route_to="await_answer",
        clarification_needed=True,
        corrected_prior_content=False,
        target_section_override=None,
        classifier_source="SAFE_FALLBACK",
        guardrail_reason=reason,
        signals={},
    )
