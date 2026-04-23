"""utils/prd_orchestrator.py — Inference-First PRD Orchestration Engine.

Central decision engine that inspects current PRD state and returns a
structured ActionPlan telling the caller:
  - what evidence exists
  - what confidence level has been reached
  - what action the assistant should take next (SEED_QUESTION / PROPOSE_ONE /
    PROPOSE_LIST / TRADEOFF_QUESTION / NO_QUESTION_NEEDED / DIRECT_ELICIT)
  - what candidates to surface (if any)

Pure Python, no LLM calls, no I/O.  Designed to run BEFORE generate_questions_node
so that prompts can be shaped by deterministic evidence, not blank elicitation.

Non-sequential section selection is bounded:
  evaluate active section + top 2 lookahead only (cost guardrail A2).
  Jump only when current = LOW confidence, candidate = MEDIUM or HIGH.
  One jump per turn maximum (T5).

Decision policy: see implementation_plan.md
"""

from __future__ import annotations

import logging
import re
from typing import Any

from utils.section_inference import (
    infer_section_candidates,
    LIVE_PROMPT_SECTIONS,
    _TARGET_SECTIONS as PHASE1_SECTIONS,
)

_log = logging.getLogger("prd_orchestrator")

# ── Section classification ────────────────────────────────────────────────────

# Primarily gathered from direct user input — NOT inference-first.
# Note: both problem_statement and background have been promoted to
# INFERENCE_FIRST_SECTIONS since they now have synthesis formatters that read
# headliner/elevator_pitch/key_stakeholders evidence.
DIRECT_ELICITATION_SECTIONS: frozenset[str] = frozenset()

# Infer from prior evidence first, then confirm.
INFERENCE_FIRST_SECTIONS: frozenset[str] = frozenset({
    "goals",
    "non_goals",
    "success_metrics",
    "assumptions",
    "out_of_scope",
    "proposed_solution",
    "risks",
    "timeline",
    "elevator_pitch",
    "key_stakeholders",
    "headliner",
    "problem_statement",  # promoted — full pain-point inferrer active
    "background",         # promoted — synthesis-first when prior evidence exists
})

# Phase 1 ordered priority for non-sequential jump evaluation (active + 2 lookahead)
_PHASE1_SECTION_ORDER: list[str] = [
    "goals",
    "non_goals",
    "success_metrics",
    "problem_statement",   # Phase 1.5 — inference-first when prior evidence exists
    "assumptions",
    "risks",
]

# ── Recommended action constants ──────────────────────────────────────────────

ACTION_SEED_QUESTION       = "SEED_QUESTION"
ACTION_PROPOSE_ONE         = "PROPOSE_ONE"
ACTION_PROPOSE_LIST        = "PROPOSE_LIST"
ACTION_TRADEOFF_QUESTION   = "TRADEOFF_QUESTION"
ACTION_NO_QUESTION_NEEDED  = "NO_QUESTION_NEEDED"
ACTION_DIRECT_ELICIT       = "DIRECT_ELICIT"

# ── Method-vs-outcome signal ──────────────────────────────────────────────────

_METHOD_VERBS = r"(?:use|using|build|deploy|implement|create|add|apply|leverage|integrate)\b"
_TECH_NOUNS   = r"(?:llm|gpt|ai|ml|model|classifier|dashboard|api|engine|pipeline|algorithm|neural|bert)"
_FEATURE_FRAMING_PATTERN = re.compile(
    rf"{_METHOD_VERBS}.{{0,30}}{_TECH_NOUNS}",
    re.IGNORECASE,
)

def _detect_feature_framing(text: str) -> bool:
    """True if the text describes a technical method rather than a business outcome."""
    return bool(_FEATURE_FRAMING_PATTERN.search(text))


# ── Forward hints grouper ─────────────────────────────────────────────────────

def _group_forward_hints(state: dict) -> dict[str, list[str]]:
    """Group forward_hints by section_id for fast lookup."""
    out: dict[str, list[str]] = {}
    for hint in (state.get("forward_hints") or []):
        sec = hint.get("section_id", "")
        if sec:
            out.setdefault(sec, []).append(hint.get("hint", ""))
    return out


# ── Snapshot builder ──────────────────────────────────────────────────────────

def get_current_snapshot(state: dict) -> dict:
    """Return a structured snapshot of current PRD state.

    Cost guardrail A2: this function only performs O(1) dict lookups.
    No section scanning beyond what is already in state.
    """
    qa_store    = state.get("confirmed_qa_store", {}) or {}
    prd_secs    = state.get("prd_sections", {}) or {}
    section_idx = state.get("section_index", 0)
    section_scores = state.get("section_scores", {}) or {}

    # Which sections have explicit user answers (non-contradicted)
    answered_sections: set[str] = set()
    for entry in qa_store.values():
        sec_id = entry.get("section_id", "")
        if sec_id and not entry.get("contradiction_flagged"):
            answered_sections.add(sec_id)

    # Which sections have any drafted content
    drafted_sections: set[str] = {k for k, v in prd_secs.items() if v and v.strip()}

    # Contradiction flags
    contradicted_sections: set[str] = set()
    for entry in qa_store.values():
        if entry.get("contradiction_flagged"):
            contradicted_sections.add(entry.get("section_id", ""))

    # How many entries are in each section
    section_qa_counts: dict[str, int] = {}
    for entry in qa_store.values():
        sec_id = entry.get("section_id", "")
        if sec_id:
            section_qa_counts[sec_id] = section_qa_counts.get(sec_id, 0) + 1

    # Latest user turn (for feature framing detection)
    recent_user_turns: list[str] = []
    messages = state.get("messages", []) or []
    for msg in reversed(messages[-6:]):
        role = getattr(msg, "type", None) or (msg.get("role") if isinstance(msg, dict) else None)
        if role in ("human", "user"):
            content = getattr(msg, "content", None) or (msg.get("content") if isinstance(msg, dict) else "")
            if content:
                recent_user_turns.append(str(content))

    latest_user_turn = recent_user_turns[0] if recent_user_turns else ""

    # Which inference-first sections are still unresolved
    unresolved_inference_sections = [
        sec_id for sec_id in INFERENCE_FIRST_SECTIONS
        if sec_id not in answered_sections
    ]

    # Section scores: which sections have a PASS verdict
    passed_sections = [
        sid for sid, v in section_scores.items()
        if isinstance(v, dict) and v.get("verdict") == "PASS"
    ]

    # Forward hints grouped by section
    forward_hints_by_section = _group_forward_hints(state)

    return {
        "section_index":                 section_idx,
        "answered_sections":             sorted(answered_sections),
        "drafted_sections":              sorted(drafted_sections),
        "contradicted_sections":         sorted(contradicted_sections),
        "section_qa_counts":             section_qa_counts,
        "unresolved_inference_sections": unresolved_inference_sections,
        "latest_user_turn":              latest_user_turn,
        "total_qa_pairs":                len(qa_store),
        "passed_sections":               passed_sections,
        "forward_hints_by_section":      forward_hints_by_section,
    }


# ── Conflict detection ────────────────────────────────────────────────────────

_MAXIMIZE_VERBS = re.compile(r"\b(?:maximize|increase|more|higher|faster|full|all|100%)\b", re.I)
_MINIMIZE_VERBS = re.compile(r"\b(?:minimize|reduce|less|lower|slower|zero|none|no\s+\w+)\b", re.I)

def _detect_candidate_conflict(candidates: list) -> bool:
    """Return True if the candidate list contains antonymous constraints.

    Accepts both list[str] (goals/non_goals/metrics) and list[dict]
    (pain_points) — normalizes dicts to their 'text' field before matching.
    """
    if len(candidates) < 2:
        return False
    texts = [c.get("text", "") if isinstance(c, dict) else str(c) for c in candidates]
    has_max = any(_MAXIMIZE_VERBS.search(t) for t in texts)
    has_min = any(_MINIMIZE_VERBS.search(t) for t in texts)
    return has_max and has_min



# ── Seed context hint ─────────────────────────────────────────────────────────

def _extract_seed_context_hint(state: dict, section_id: str) -> str:
    """Pull one short context line from earlier sections to anchor the seed question."""
    qa_store = state.get("confirmed_qa_store", {}) or {}
    prd_secs = state.get("prd_sections", {}) or {}

    context_sources = {
        "goals":             ["headliner", "problem_statement", "elevator_pitch"],
        "non_goals":         ["goals", "proposed_solution", "assumptions"],
        "success_metrics":   ["problem_statement", "headliner", "goals"],
        "problem_statement": ["headliner", "elevator_pitch", "background"],  # Phase 1.5 pain inference
        "assumptions":       ["proposed_solution", "goals", "background"],
        "risks":             ["assumptions", "proposed_solution", "timeline"],
        "out_of_scope":      ["non_goals", "proposed_solution"],
        "proposed_solution": ["goals", "problem_statement"],
        "timeline":          ["goals", "proposed_solution"],
        "elevator_pitch":    ["headliner", "problem_statement", "goals"],
        "key_stakeholders":  ["goals", "background"],
    }

    priority_sources = context_sources.get(section_id, ["headliner", "problem_statement"])

    for src_id in priority_sources:
        for entry in qa_store.values():
            if entry.get("section_id") == src_id and not entry.get("contradiction_flagged"):
                answer = str(entry.get("answer", "") or entry.get("value", "") or "")
                if answer and len(answer) > 15:
                    return answer[:120].split(".")[0].strip()
        draft = prd_secs.get(src_id, "")
        if draft and len(draft.strip()) > 15:
            first_line = draft.strip().splitlines()[0].strip().lstrip("#").strip()
            if len(first_line) > 10:
                return first_line[:120]

    return ""


# ── Non-sequential section selection ─────────────────────────────────────────

def _select_highest_value_unresolved_section(
    state: dict,
    snapshot: dict,
    current_section_id: str,
    current_confidence: str,
) -> str | None:
    """Return a better candidate section_id, or None to keep current section.

    Rules (T4, T5):
    - Only evaluates Phase 1 sections.
    - Evaluates active section + 2 lookahead max (cost guardrail A2).
    - Returns a jump target only when current confidence is LOW and a
      candidate is MEDIUM or HIGH.
    - Returns None (no jump) otherwise.
    """
    if current_confidence != "low":
        return None

    # Evaluate up to 2 lookahead sections beyond the current one
    answered = set(snapshot.get("answered_sections", []))
    candidates_to_check: list[str] = []
    for sec_id in _PHASE1_SECTION_ORDER:
        if sec_id == current_section_id:
            continue
        if sec_id in answered:
            continue
        candidates_to_check.append(sec_id)
        if len(candidates_to_check) >= 2:
            break

    for candidate_id in candidates_to_check:
        inf = infer_section_candidates(candidate_id, state)
        if inf.get("confidence") in ("medium", "high") and inf.get("inference_available"):
            return candidate_id

    return None


# ── Main orchestrator ─────────────────────────────────────────────────────────

def inference_first_prd_orchestrator(state: dict, section: Any) -> dict:
    """Central decision engine for evidence-first PRD gathering.

    Parameters
    ----------
    state : dict
        The LangGraph PRDState dict.
    section : PRDSection
        The section currently being elicited.

    Returns
    -------
    ActionPlan dict (see field list below).
    One jump maximum per turn: caller must pass _jump_already_used=True on
    recursive/follow-up calls to prevent thrashing (T5).

    Fields:
        current_snapshot        : dict
        target_section_id       : str
        target_section_title    : str
        is_inference_first      : bool
        is_live_prompt_eligible : bool  — False for assumptions/risks in Phase 1
        evidence_summary        : list[str]
        evidence_sources        : list[str]
        confidence              : "LOW" | "MEDIUM" | "HIGH"
        recommended_action      : str
        candidate_items         : list[str]
        has_conflict            : bool
        feature_framing_detected: bool
        seed_context_hint       : str
        reasoning_summary       : str
        metric_baselines        : list[dict]
        section_jumped          : bool   — True if target_section_id != section.id
        jump_reason             : str
    """
    section_id    = section.id
    section_title = section.title

    snapshot = get_current_snapshot(state)
    qa_store = state.get("confirmed_qa_store", {}) or {}

    _log.info(
        "orchestrator_snapshot_built",
        extra={
            "event_type": "orchestrator_snapshot_built",
            "section_id": section_id,
            "answered_sections": snapshot["answered_sections"],
            "total_qa_pairs": snapshot["total_qa_pairs"],
            "passed_sections": snapshot["passed_sections"],
        },
    )

    # ── Gate 1: Direct-elicitation sections — bypass inference UNLESS evidence exists ───
    if section_id in DIRECT_ELICITATION_SECTIONS:
        # problem_statement is special: when prior-section evidence exists, run inference
        # (pain signals often appear in headliner/background before ps is asked).
        # For LOW evidence, fall through to direct elicitation as normal.
        run_inference_anyway = (
            section_id == "problem_statement"
            and any(
                v.get("section_id") in ("headliner", "elevator_pitch", "background")
                for v in qa_store.values()
                if isinstance(v, dict) and not v.get("contradiction_flagged")
            )
        )
        if not run_inference_anyway:
            _log.info(
                "orchestrator_action_decided",
                extra={
                    "event_type": "orchestrator_action_decided",
                    "section_id": section_id,
                    "is_inference_first": False,
                    "recommended_action": ACTION_DIRECT_ELICIT,
                    "confidence": "LOW",
                    "is_live_prompt_eligible": False,
                },
            )
            return {
                "current_snapshot":         snapshot,
                "target_section_id":        section_id,
                "target_section_title":     section_title,
                "is_inference_first":       False,
                "is_live_prompt_eligible":  False,
                "evidence_summary":         [],
                "evidence_sources":         [],
                "confidence":               "LOW",
                "recommended_action":       ACTION_DIRECT_ELICIT,
                "candidate_items":          [],
                "has_conflict":             False,
                "feature_framing_detected": False,
                "seed_context_hint":        "",
                "reasoning_summary":        f"{section_title} is a direct-elicitation section. Ask grounded questions without inference.",
                "metric_baselines":         [],
                "section_jumped":           False,
                "jump_reason":              "",
            }
        # else: fall through to inference path below

    # ── Gate 2: Section already explicitly answered → no question needed ───────
    already_answered = any(
        v.get("section_id") == section_id and not v.get("contradiction_flagged")
        for v in qa_store.values()
    )
    if already_answered:
        _log.info(
            "orchestrator_action_decided",
            extra={
                "event_type": "orchestrator_action_decided",
                "section_id": section_id,
                "is_inference_first": True,
                "recommended_action": ACTION_NO_QUESTION_NEEDED,
                "confidence": "HIGH",
                "reason": "explicit_user_answers_present",
                "is_live_prompt_eligible": section_id in LIVE_PROMPT_SECTIONS,
            },
        )
        return {
            "current_snapshot":         snapshot,
            "target_section_id":        section_id,
            "target_section_title":     section_title,
            "is_inference_first":       True,
            "is_live_prompt_eligible":  section_id in LIVE_PROMPT_SECTIONS,
            "evidence_summary":         [],
            "evidence_sources":         [],
            "confidence":               "HIGH",
            "recommended_action":       ACTION_NO_QUESTION_NEEDED,
            "candidate_items":          [],
            "has_conflict":             False,
            "feature_framing_detected": False,
            "seed_context_hint":        "",
            "reasoning_summary":        f"I've captured enough for {section_title} based on what you shared.",
            "metric_baselines":         [],
            "section_jumped":           False,
            "jump_reason":              "",
        }

    # ── Gate 3: Run inference helper for the section ───────────────────────────
    inf = infer_section_candidates(section_id, state)

    confidence_raw   = inf.get("confidence", "low")
    candidates       = inf.get("candidate_items", [])
    evidence         = inf.get("evidence", [])
    evidence_sources = inf.get("evidence_sources", [])
    metric_baselines = inf.get("metric_baselines", [])

    # Feature-framing check
    feature_framing_detected = inf.get("feature_framing_detected", False)
    if not feature_framing_detected:
        latest = snapshot.get("latest_user_turn", "")
        if latest:
            feature_framing_detected = _detect_feature_framing(latest)

    # Conflict check
    has_conflict = inf.get("has_conflict", False)
    if not has_conflict and len(candidates) >= 2:
        has_conflict = _detect_candidate_conflict(candidates)

    # ── Non-sequential section jump evaluation (T4, T5) ──────────────────────
    jump_candidate = _select_highest_value_unresolved_section(
        state, snapshot, section_id, confidence_raw
    )
    section_jumped = False
    jump_reason    = ""

    if jump_candidate and jump_candidate != section_id:
        # Re-run inference for the jump target
        jump_inf = infer_section_candidates(jump_candidate, state)
        jump_section_id    = jump_candidate
        jump_section_title = jump_candidate.replace("_", " ").title()  # fallback label
        jump_reason = (
            f"I already have enough for {section_title} to continue. "
            f"Before I need to clarify {jump_section_title} — I can see some signals from earlier."
        )
        section_jumped   = True
        section_id       = jump_section_id
        section_title    = jump_section_title
        inf              = jump_inf
        confidence_raw   = jump_inf.get("confidence", "low")
        candidates       = jump_inf.get("candidate_items", [])
        evidence         = jump_inf.get("evidence", [])
        evidence_sources = jump_inf.get("evidence_sources", [])
        metric_baselines = jump_inf.get("metric_baselines", [])
        has_conflict     = bool(jump_inf.get("has_conflict")) or (
            len(candidates) >= 2 and _detect_candidate_conflict(candidates)
        )

        _log.info(
            "orchestrator_section_jump_reason",
            extra={
                "event_type": "orchestrator_section_jump_reason",
                "from_section": section.id,
                "to_section": section_id,
                "reason": jump_reason,
                "from_confidence": "LOW",
                "to_confidence": confidence_raw,
            },
        )

    # ── Is live prompt injection enabled for this section? (T2) ─────────────
    is_live = section_id in LIVE_PROMPT_SECTIONS

    # ── Map confidence to action ───────────────────────────────────────────────
    seed_hint = _extract_seed_context_hint(state, section_id)

    if confidence_raw == "low" or not candidates:
        confidence = "LOW"
        action = ACTION_SEED_QUESTION
        reasoning = (
            f"No grounded evidence for {section_title}. "
            + ("Feature framing detected — redirect to business outcome. " if feature_framing_detected else "")
            + ("Context hint available for anchoring seed. " if seed_hint else "Ask one narrow opening question.")
        )
    elif has_conflict:
        confidence = "MEDIUM"
        action = ACTION_TRADEOFF_QUESTION
        reasoning = f"{section_title} has conflicting candidates — surface tension and ask tradeoff."
    elif confidence_raw == "medium":
        confidence = "MEDIUM"
        action = ACTION_PROPOSE_ONE
        reasoning = f"One grounded clue found for {section_title}. Propose one hedged candidate and ask confirm/correct."
    else:  # high
        confidence = "HIGH"
        action = ACTION_PROPOSE_LIST
        reasoning = f"Strong evidence for {section_title}. Propose 2–3 candidates for confirm/correct/extend."

    # ── Rich First-Turn Evidence Guardrail ────────────────────────────────────
    # If the latest user turn already contains ≥3 rich evidence signals
    # (pain, failure mode, baseline, target metric, mechanism, approval dependency)
    # the SEED_QUESTION path is FORBIDDEN. Downgrading a rich answer to a generic
    # opener wastes a turn and signals the system isn't listening.
    _RICH_SIGNAL_PATTERNS = [
        re.compile(r"\b(manual|manually|human.validat|by hand|handl)\b", re.I),       # operational pain
        re.compile(r"\b(fail|broken|error|miss|inconsist|corrupt|chaos|UOM|GTIN)\b", re.I),  # failure modes
        re.compile(r"\b(\d+\s*%|accuracy|precision|recall)\b", re.I),                # current baseline
        re.compile(r"\b(\d+\s*(second|minute|hour|min|sec)|latency|throughput)\b", re.I),  # target metric
        re.compile(r"\b(pipeline|automat|model|self.correct|algorithm|ML|AI)\b", re.I),  # mechanism
        re.compile(r"\b(sign.off|approv|log.access|review.queue|clearance|stakeholder)\b", re.I), # approval
    ]
    _latest_turn = snapshot.get("latest_user_turn", "") or ""
    _rich_signal_count = sum(1 for p in _RICH_SIGNAL_PATTERNS if p.search(_latest_turn))
    _is_rich_first_turn = _rich_signal_count >= 3

    if _is_rich_first_turn and action == ACTION_SEED_QUESTION:
        # Evidence-rich input: promote to PROPOSE_ONE regardless of is_live.
        # The user already gave us a full problem frame — seed questions are insulting.
        action = ACTION_PROPOSE_ONE if not candidates else action
        # If there are no inferrer candidates yet (first turn, no QA store),
        # use the latest turn text as the sole candidate summary.
        if not candidates:
            candidates = [
                (_latest_turn[:200] + "…") if len(_latest_turn) > 200 else _latest_turn
            ]
        _log.info(
            "orchestrator_rich_first_turn_guardrail",
            extra={
                "event_type": "orchestrator_rich_first_turn_guardrail",
                "section_id": section_id,
                "rich_signal_count": _rich_signal_count,
                "prior_action": ACTION_SEED_QUESTION,
                "promoted_to": ACTION_PROPOSE_ONE,
            },
        )

    # Override action to SEED_QUESTION for non-live sections (observe-only — T2)
    # Exception: rich first-turn evidence bypasses this downgrade.
    if not is_live and not _is_rich_first_turn and action in (ACTION_PROPOSE_ONE, ACTION_PROPOSE_LIST, ACTION_TRADEOFF_QUESTION):
        action = ACTION_SEED_QUESTION  # observing; don't inject live yet

    # ── Persona stance — propagated to question generator for continuity guardrail ──
    # When the section is key_stakeholders, the inferrer returns role candidates in
    # operator > manager > approver > non-target order. We surface the primary tiers
    # and any prestige-only (non-target) roles so the next-turn question generator can
    # enforce self-consistency (Conversation Continuity Guardrail).
    _persona_stance: list[str] = []
    _non_target_personas: list[str] = []
    if section_id == "key_stakeholders" and candidates:
        for cand in candidates:
            cand_lower = cand.lower()
            if any(w in cand_lower for w in ("executive", "sponsor", "not primary", "non-target", "non target", "strategic visibility")):
                _non_target_personas.append(cand)
            else:
                _persona_stance.append(cand)

    _log.info(
        "orchestrator_action_decided",
        extra={
            "event_type": "orchestrator_action_decided",
            "section_id": section_id,
            "is_inference_first": True,
            "is_live_prompt_eligible": is_live,
            "recommended_action": action,
            "confidence": confidence,
            "candidate_count": len(candidates),
            "has_conflict": has_conflict,
            "feature_framing": feature_framing_detected,
            "section_jumped": section_jumped,
        },
    )

    return {
        "current_snapshot":         snapshot,
        "target_section_id":        section_id,
        "target_section_title":     section_title,
        "is_inference_first":       True,
        "is_live_prompt_eligible":  is_live,
        "evidence_summary":         evidence,
        "evidence_sources":         evidence_sources,
        "confidence":               confidence,
        "recommended_action":       action,
        "candidate_items":          candidates,
        "has_conflict":             has_conflict,
        "feature_framing_detected": feature_framing_detected,
        "seed_context_hint":        seed_hint,
        "reasoning_summary":        jump_reason if section_jumped else reasoning,
        "metric_baselines":         metric_baselines,
        "section_jumped":           section_jumped,
        "jump_reason":              jump_reason,
        # Persona Continuity Guardrail fields (always present; empty for non-stakeholder sections)
        "persona_stance":           _persona_stance,
        "non_target_personas":      _non_target_personas,
    }
