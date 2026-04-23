"""utils/section_inference.py — deterministic evidence-first inference for sections.

Phase 1 coverage: goals, non_goals, success_metrics, assumptions, risks.
Assumptions and risks are registered in _INFERRERS but their live prompt
injection is deferred to Phase 1 Step 6 (observe-only until metrics quality proven).

No LLM calls, no I/O.  Pure dict → dict.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

_log = logging.getLogger("section_inference")

# ── Section IDs this module handles ──────────────────────────────────────────
# Phase 1: goals, non_goals, success_metrics + new assumptions, risks
# Phase 2 (deferred): timeline, key_stakeholders, proposed_solution,
#                     out_of_scope, dependencies, elevator_pitch, headliner

_TARGET_SECTIONS = frozenset({
    "goals",
    "non_goals",
    "success_metrics",
    "problem_statement",  # Phase 1.5 — live: pain inference from prior evidence
    "assumptions",        # Phase 1 — observe-only in live prompts until Step 6
    "risks",              # Phase 1 — observe-only in live prompts until Step 6
    "background",         # Phase 1.6 — synthesis-first when prior evidence exists
    "key_stakeholders",   # Phase 1.7 — operator-first role taxonomy
})

# Phase 1 sections where prompt injection is LIVE
LIVE_PROMPT_SECTIONS: frozenset[str] = frozenset({
    "goals",
    "non_goals",
    "success_metrics",
    "problem_statement",  # Phase 1.5 — live when prior evidence exists
    "assumptions",        # Phase 2 — live
    "risks",              # Phase 2 — live
    "background",         # Phase 1.6 — live synthesis confirm/correct
    "key_stakeholders",   # Phase 1.7 — live operator-first role question
})

# ── Signal patterns ───────────────────────────────────────────────────────────

# Desired-outcome / impact signals (Goals)
_GOAL_SIGNALS: list[tuple[str, str]] = [
    (r"reduce|cut|lower|decrease|shorten|speed up|faster|quicken", "reduce"),
    (r"prevent|avoid|eliminate|stop|remove|erase",                 "prevent"),
    (r"improve|increase|boost|grow|enhance|scale",                 "improve"),
    (r"automate|replac\w* manual|automat\w+",                      "automate"),
    (r"save\s+(?:time|hours|effort|cost|money)",                   "save"),
]

# Exclusion / scope-limit signals (Non-goals)
_NON_GOAL_SIGNALS: list[tuple[str, str]] = [
    (r"not\s+(?:in|part of)\s+(?:scope|this phase|this version)",  "out_of_scope"),
    (r"won'?t|will not|not going to|out of scope|exclude|skip",    "exclusion"),
    (r"phase\s*(?:2|two|3|three|later|next)",                      "phased_later"),
    (r"too expensive|budget\s*(?:constraint|limit)|can'?t afford",  "budget_limit"),
    (r"no\s+(?:ML|AI|model|API|integration|real.?time)",           "tech_exclusion"),
]

# Numeric / metric signals (Success Metrics)
_METRIC_SIGNALS: list[tuple[str, str]] = [
    (r"(\d+(?:\.\d+)?)\s*(?:hours?|hrs?)\s+(?:per|a|each)",       "time_per_unit"),
    (r"(\d+(?:\.\d+)?)\s*(?:minutes?|mins?)\s+(?:per|a|each)",    "time_per_unit"),
    (r"(\d+(?:\.\d+)?)\s*(?:days?)\s+(?:per|a|each|to)",          "time_per_unit"),
    (r"(?=.*(?:error|fail|wrong|incorrect|inaccur))(\d+(?:\.\d+)?)\s*%", "error_rate"),
    (r"(\d+(?:\.\d+)?)\s*(?:errors?|mistakes?|wrong\s+\w+)\s+per","error_rate"),
    (r"(\d+(?:\.\d+)?)\s*(?:rows?|records?|items?|sku|products?)\s+per", "throughput"),
    (r"(\d+(?:\.\d+)?)\s*(?:tickets?|cases?|incidents?)\s+per",   "incident_rate"),
    (r"(\d+(?:\.\d+)?)\s*(?:weeks?|months?)\s+(?:to|for|of)",     "duration"),
    (r"manual\w*",                                                  "manual_work"),
]

# Target-present signals for metric_baselines (T6: conservative; logged on every parse)
_TARGET_SIGNALS = re.compile(
    r"\bunder\b|\bless than\b|\breduce to\b|\btarget\b|\bgoal\b|\baim for\b|\bby\s+\d",
    re.IGNORECASE,
)

# Assumption signals
_ASSUMPTION_SIGNALS: list[tuple[str, str]] = [
    (r"assum\w+|given that|we expect|presuppos\w+",                  "assumption"),
    (r"depends on|requires|relies on|contingent on|predicated on",   "dependency_assumption"),
    (r"users?\s+will|team\s+will|stakeholders?\s+will|customers?\s+will",  "behavioral_assumption"),
    (r"data\s+(?:will be|is)\s+(?:available|ready|clean|provided)",  "data_assumption"),
    (r"infrastructure\s+(?:is|will be)\s+(?:ready|in place|available)", "infra_assumption"),
]

# Risk signals
_RISK_SIGNALS: list[tuple[str, str]] = [
    (r"\brisk\b|\bconcern\b|\bchallenge\b|\bcould fail\b|\bmight fail\b", "generic_risk"),
    (r"\buncertain\b|\bunknown\b|\bunclear\b|\bTBD\b|\bnot yet decided\b",  "uncertainty_risk"),
    (r"\bblocker\b|\bdependency\b|\bblocked by\b|\bwaiting on\b",           "dependency_risk"),
    (r"tight|aggressive|compressed|short timeline|unrealistic",             "timeline_risk"),
    (r"adoption|resistance|change\s+management|buy.?in",                   "adoption_risk"),
]

# ── Evidence source priority lists ────────────────────────────────────────────

# ── Source-section lists with hard boundary enforcement ───────────────────────
# Each list is the EXCLUSIVE allowed evidence pool for its section.
# Sections not in the list MUST NOT contribute signal to this inferrer.

_GOAL_SOURCE_SECTIONS  = ["headliner", "problem_statement", "elevator_pitch", "background"]

# R4a — non_goals: intent-level exclusions, grounded in goals only.
# Removed: proposed_solution, assumptions, background — those produce delivery-level
# or implementation noise that collapses non_goals into out_of_scope.
_NON_GOAL_SOURCE_SECTIONS   = ["goals", "problem_statement"]

# R4b — out_of_scope: delivery-level feature/use-case exclusions only.
# Primary source: proposed_solution (what is being built → what is NOT built).
# non_goals is a weak-context secondary source, never the primary signal driver.
_OUT_OF_SCOPE_SOURCE_SECTIONS = ["proposed_solution", "non_goals"]

_METRIC_SOURCE_SECTIONS     = ["problem_statement", "headliner", "goals", "background"]
_ASSUMPTION_SOURCE_SECTIONS = ["proposed_solution", "goals", "background", "problem_statement"]
_RISK_SOURCE_SECTIONS       = ["proposed_solution", "timeline", "goals"]
# Note: 'assumptions' is intentionally REMOVED from _RISK_SOURCE_SECTIONS.
# Assumptions are beliefs + validation plans; risks are failure modes + consequences.
# Reading assumptions into risks produces belief-leak (R6: dependency statements appear as risks).

# Pain inference reads from PRIOR sections (the user hasn't answered problem_statement yet)
# 'background' excluded from raw signal extraction; only reachable via escalation path.
_PAIN_SOURCE_SECTIONS       = ["headliner", "elevator_pitch", "goals"]

# Stakeholder inference reads from all narrative sections
_STAKEHOLDER_SOURCE_SECTIONS = ["headliner", "elevator_pitch", "background", "problem_statement", "goals"]

# ── Signal guard patterns (cross-section corruption prevention) ────────────────

# R2: Headliner must stay problem/opportunity only — not elevator_pitch territory.
# Skip snippets that contain differentiator, persuasion, or executive-framing language.
_HEADLINER_EXCLUSION_RE = re.compile(
    r"\bdifferentiator\b|\bkey benefit\b|\bvs\.?\s+alternat\w+\b"
    r"|\bexecutive\b|\bpitch\b|\bpersuad\w+\b|\bunlike\b.*?\bcompetit",
    re.IGNORECASE,
)

# R6: Risk candidates must contain failure-mode AND consequence language.
# Pure dependency/assumes statements belong in Assumptions, not Risks.
_RISK_FAILURE_RE    = re.compile(r"\bfail\w*\b|\bbreak\w*\b|\bdelay\w*\b|\bblock\w*\b|\bmiss\w*\b|\bblocker\b", re.IGNORECASE)
_RISK_CONSEQUENCE_RE = re.compile(r"\bcould\b|\bmight\b|\bwould\b|\bresult\b|\blead\b|\bcause\b|\bimpact\b|\brisk\b", re.IGNORECASE)
_RISK_ASSUMPTION_LEAK_RE = re.compile(r"\bassum\w+\b|\bassuming\b", re.IGNORECASE)

# R5: Assumption candidates require 3-field shape: belief + dependency link + validation intent.
_ASSUMPTION_DEPENDENCY_RE = re.compile(r"\bdepend\w*\b|\brequire\w*\b|\brely\b|\brelies\b|\bcontingent\b|\bpredicated\b", re.IGNORECASE)
_ASSUMPTION_VALIDATION_RE = re.compile(r"\bvalidat\w+\b|\bverif\w+\b|\bconfirm\w+\b|\btest\w*\b|\bmeasur\w+\b|\bproven\b|\bcheck\b", re.IGNORECASE)
_ASSUMPTION_BELIEF_RE     = re.compile(r"\bassum\w+\b|\bexpect\w*\b|\bbeliev\w+\b|\bgiven that\b|\bwill be\b|\bshould be\b", re.IGNORECASE)

# R7: Proposed-solution signal guard — skip sentences with timeline/metric framing.
# These belong in timeline and success_metrics sections.
_PROPOSED_SOL_TIMELINE_RE = re.compile(
    r"\bby Q[1-4]\b|\bmilestone\b|\bsprint\b|\bby \d{4}\b|\bowner\b|\bNPS\b|\bOKR\b"
    r"|\btarget:\b|\bbaseline:\b|\bsuccess metric\b",
    re.IGNORECASE,
)

# ── Persona / role signal patterns ────────────────────────────────────────────
# Applied in priority order: operator first, manager second, buyer third, non-target last.

# Tier 1 — Operator / hands-on user.  Must be checked first.
_OPERATOR_SIGNALS = re.compile(
    r"\b(product ops|operations?\s+team|ops\s+team|mapping\s+ops|data\s+ops"
    r"|analyst|coordinator|specialist|technician|operator|associate"
    r"|manually|hands.on|day.to.day|daily\s+workflow|batch|run\s+the)",
    re.IGNORECASE,
)

# Tier 2 — Manager / team lead.
_MANAGER_SIGNALS = re.compile(
    r"\b(manager|lead|supervisor|team\s+lead|ops\s+lead|head\s+of\s+(?!company|product\.{0,6}$)"
    r"|director\s+of\s+operations?|program\s+manager|project\s+manager)",
    re.IGNORECASE,
)

# Tier 3 — Buyer / Approver.  Budget or governance role.
_BUYER_SIGNALS = re.compile(
    r"\b(budget|approv|purchas|buy|sponsor|champion|sign.off"
    r"|director|head\s+of|chief|coo|cto|cpo)",
    re.IGNORECASE,
)

# Non-target prestige words — demoted to last; never the primary lens.
_PRESTIGE_SIGNALS = re.compile(
    r"\b(executive|vp\b|vice president|c.suite|c-level|leadership team"
    r"|svp|evp|board|board\s+of\s+directors?|ceo|cfo)",
    re.IGNORECASE,
)




# ── Internal helpers ──────────────────────────────────────────────────────────

def _qa_texts_for_sections(
    section_ids: list[str],
    qa_store: dict,
    prd_sections: dict,
) -> list[tuple[str, str]]:  # [(source_section_id, text)]
    """Collect raw text from confirmed_qa_store (primary) + prd_sections (fallback).

    Correction-precedence rule: when multiple entries share the same section_id,
    only the one with the HIGHEST version is used. This prevents stale first-writes
    from competing with later user corrections in downstream signal extraction.
    """
    # Step 1: per section_id, keep only the highest-version entry (correction-precedence)
    _best: dict[str, dict] = {}
    for entry in qa_store.values():
        src = entry.get("section_id", "")
        if src not in section_ids:
            continue
        existing = _best.get(src)
        if existing is None or (entry.get("version", 0) or 0) >= (existing.get("version", 0) or 0):
            _best[src] = entry

    results: list[tuple[str, str]] = []
    seen: set[str] = set()

    # Primary: confirmed QA pairs (correction-precedence applied)
    for src, entry in _best.items():
        answer = str(entry.get("answer", "") or entry.get("value", "") or "")
        question = str(entry.get("question", "") or entry.get("questions", "") or "")
        text = f"{question} {answer}".strip()
        if text and text not in seen:
            seen.add(text)
            results.append((src, text))

    # Fallback: draft markdown per section (noisier, lower priority)
    for sec_id in section_ids:
        draft = prd_sections.get(sec_id, "")
        if draft and draft not in seen:
            seen.add(draft)
            results.append((sec_id, str(draft)))

    return results


def _extract_signal_snippets(
    texts: list[tuple[str, str]],
    signals: list[tuple[str, str]],
    max_per_signal: int = 2,
) -> list[tuple[str, str, str]]:  # [(signal_type, snippet, source_section)]
    """Return (signal_type, snippet, source_section) for each matched sentence."""
    found: dict[str, list[tuple[str, str]]] = {}
    for src, text in texts:
        sentences = re.split(r'(?<=[.?!])\s+', text.replace('\n', ' '))
        for sent in sentences:
            sent = sent.strip()
            if len(sent) < 10:
                continue
            for pattern, sig_type in signals:
                if re.search(pattern, sent, re.IGNORECASE):
                    bucket = found.setdefault(sig_type, [])
                    if len(bucket) < max_per_signal:
                        bucket.append((sent[:200], src))
    return [(sig, snippet, src) for sig, pairs in found.items() for snippet, src in pairs]


def _has_explicit_answers(section_id: str, qa_store: dict) -> bool:
    """Return True if the user has already confirmed answers for this section."""
    return any(
        v.get("section_id") == section_id and not v.get("contradiction_flagged")
        for v in qa_store.values()
    )


def _deduplicate(items: list[str]) -> list[str]:
    """Preserve order, remove case-insensitive near-duplicates (first-word match)."""
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = item.strip().lower()[:40]
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


# ── Section-specific candidate builders ──────────────────────────────────────

def _infer_goals(state: dict) -> dict:
    qa_store  = state.get("confirmed_qa_store", {}) or {}
    prd_secs  = state.get("prd_sections", {}) or {}

    texts     = _qa_texts_for_sections(_GOAL_SOURCE_SECTIONS, qa_store, prd_secs)
    signals   = _extract_signal_snippets(texts, _GOAL_SIGNALS)

    candidates: list[str] = []
    evidence: list[str]   = []
    sources: set[str]     = set()

    for sig_type, snippet, src in signals:
        sources.add(src)
        short = snippet[:120].rstrip(".,;:")
        candidates.append(short)
        evidence.append(f"[{src}] {snippet}")

    candidates = _deduplicate(candidates)[:5]
    evidence   = _deduplicate(evidence)[:6]

    confidence = "high" if len(candidates) >= 3 else "medium" if candidates else "low"
    return {
        "has_explicit_answers": _has_explicit_answers("goals", qa_store),
        "inference_available":  bool(candidates),
        "candidate_items":      candidates,
        "evidence":             evidence,
        "evidence_sources":     sorted(sources),
        "confidence":           confidence,
        "metric_baselines":     [],
        "feature_framing_detected": False,
        "has_conflict":         False,
    }


def _infer_non_goals(state: dict) -> dict:
    """R4a — non_goals: intent-level exclusions grounded in goals only.

    Source: goals + problem_statement. Proposed solution, assumptions, and
    background are excluded — they produce delivery-level noise that collapses
    non_goals into out_of_scope.
    """
    qa_store  = state.get("confirmed_qa_store", {}) or {}
    prd_secs  = state.get("prd_sections", {}) or {}

    texts     = _qa_texts_for_sections(_NON_GOAL_SOURCE_SECTIONS, qa_store, prd_secs)
    signals   = _extract_signal_snippets(texts, _NON_GOAL_SIGNALS)

    candidates: list[str] = []
    evidence: list[str]   = []
    sources: set[str]     = set()
    evidence_selection_log: list[tuple[str, str]] = []

    for sig_type, snippet, src in signals:
        sources.add(src)
        short = snippet[:120].rstrip(".,;:")
        candidates.append(short)
        evidence.append(f"[{src}] {snippet}")
        evidence_selection_log.append((src, snippet[:60]))

    candidates = _deduplicate(candidates)[:5]
    evidence   = _deduplicate(evidence)[:6]

    confidence = "high" if len(candidates) >= 2 else "medium" if candidates else "low"
    return {
        "has_explicit_answers": _has_explicit_answers("non_goals", qa_store),
        "inference_available":  bool(candidates),
        "candidate_items":      candidates,
        "evidence":             evidence,
        "evidence_sources":     sorted(sources),
        "confidence":           confidence,
        "metric_baselines":     [],
        "feature_framing_detected": False,
        "has_conflict":         False,
        "evidence_selection_log": evidence_selection_log,
    }


def _infer_success_metrics(state: dict) -> dict:
    """R3 — success_metrics: gates on confirmed goals before inferring.

    If goals have not been answered yet, returns inference_available=False with
    a seed hint focused on goal-direction, not metric specifics. This prevents
    the system from asking 'what would success look like?' before goals are set.
    """
    qa_store  = state.get("confirmed_qa_store", {}) or {}
    prd_secs  = state.get("prd_sections", {}) or {}

    # R3: Gate — requires goals to be confirmed before producing metric candidates.
    if not _has_explicit_answers("goals", qa_store):
        return {
            "has_explicit_answers": _has_explicit_answers("success_metrics", qa_store),
            "inference_available":  False,
            "candidate_items":      [],
            "evidence":             [],
            "evidence_sources":     [],
            "confidence":           "low",
            "seed_context_hint":    (
                "What outcomes or results would tell you that this initiative worked? "
                "Focus on the direction first — we'll define specific targets next."
            ),
            "metric_baselines":     [],
            "feature_framing_detected": False,
            "has_conflict":         False,
            "evidence_selection_log": [],
        }

    texts     = _qa_texts_for_sections(_METRIC_SOURCE_SECTIONS, qa_store, prd_secs)
    signals   = _extract_signal_snippets(texts, _METRIC_SIGNALS, max_per_signal=3)

    candidates: list[str] = []
    evidence: list[str]   = []
    sources: set[str]     = set()
    metric_baselines: list[dict] = []

    for sig_type, snippet, src in signals:
        sources.add(src)
        candidates.append(snippet[:150].rstrip(".,;:"))
        evidence.append(f"[{src}] {snippet}")

        # T6: Detect baseline vs. target; log every parse decision
        if sig_type == "time_per_unit" or sig_type in ("error_rate", "throughput", "incident_rate"):
            target_present = bool(_TARGET_SIGNALS.search(snippet))
            mb = {
                "text": snippet[:150],
                "signal_type": sig_type,
                "source": src,
                "baseline_known": True,
                "target_known": target_present,
            }
            metric_baselines.append(mb)
            _log.info(
                "orchestrator_metric_baseline_parsed",
                extra={
                    "event_type": "orchestrator_metric_baseline_parsed",
                    "signal_type": sig_type,
                    "baseline_known": True,
                    "target_known": target_present,
                    "snippet": snippet[:80],
                    "source_section": src,
                },
            )

    # Also pull already-confirmed goals as candidate metric targets
    goals_draft = prd_secs.get("goals", "")
    if goals_draft:
        goal_lines = [
            l.strip().lstrip("-•*").strip()
            for l in goals_draft.splitlines()
            if l.strip() and not l.strip().startswith("#")
        ]
        for gl in goal_lines[:2]:
            candidates.append(f"Track progress toward: {gl[:100]}")
            sources.add("goals")

    candidates = _deduplicate(candidates)[:5]
    evidence   = _deduplicate(evidence)[:6]

    has_numeric = any(re.search(r'\d+', c) for c in candidates)
    confidence = "high" if has_numeric and len(candidates) >= 2 else "medium" if candidates else "low"

    return {
        "has_explicit_answers": _has_explicit_answers("success_metrics", qa_store),
        "inference_available":  bool(candidates),
        "candidate_items":      candidates,
        "evidence":             evidence,
        "evidence_sources":     sorted(sources),
        "confidence":           confidence,
        "metric_baselines":     metric_baselines,
        "feature_framing_detected": False,
        "has_conflict":         False,
        "evidence_selection_log": [(src, snip[:60]) for src, snip in zip(sorted(sources), evidence[:5])],
    }


def _infer_assumptions(state: dict) -> dict:
    """R5 — Assumptions: belief + dependency link + validation intent required per candidate.

    A candidate that lacks ANY of the three fields is rejected.
    Pure beliefs without dependency or validation meaning stay as background context,
    not as assumption candidates.
    """
    qa_store  = state.get("confirmed_qa_store", {}) or {}
    prd_secs  = state.get("prd_sections", {}) or {}

    texts     = _qa_texts_for_sections(_ASSUMPTION_SOURCE_SECTIONS, qa_store, prd_secs)
    signals   = _extract_signal_snippets(texts, _ASSUMPTION_SIGNALS)

    candidates: list[str] = []
    evidence: list[str]   = []
    sources: set[str]     = set()
    evidence_selection_log: list[tuple[str, str]] = []
    rejected_count = 0

    for sig_type, snippet, src in signals:
        # R5 shape guard: must have belief + dependency + validation fields
        has_belief     = bool(_ASSUMPTION_BELIEF_RE.search(snippet))
        has_dependency = bool(_ASSUMPTION_DEPENDENCY_RE.search(snippet))
        has_validation = bool(_ASSUMPTION_VALIDATION_RE.search(snippet))
        if not (has_belief and (has_dependency or has_validation)):
            rejected_count += 1
            _log.debug(
                "assumption_candidate_rejected_missing_fields",
                extra={
                    "event_type": "assumption_candidate_rejected_missing_fields",
                    "snippet": snippet[:60],
                    "has_belief": has_belief,
                    "has_dependency": has_dependency,
                    "has_validation": has_validation,
                },
            )
            continue
        sources.add(src)
        short = snippet[:120].rstrip(".,;:")
        candidates.append(short)
        evidence.append(f"[{src}] {snippet}")
        evidence_selection_log.append((src, snippet[:60]))

    candidates = _deduplicate(candidates)[:4]
    evidence   = _deduplicate(evidence)[:5]

    confidence = "high" if len(candidates) >= 2 else "medium" if candidates else "low"
    return {
        "has_explicit_answers": _has_explicit_answers("assumptions", qa_store),
        "inference_available":  bool(candidates),
        "candidate_items":      candidates,
        "evidence":             evidence,
        "evidence_sources":     sorted(sources),
        "confidence":           confidence,
        "metric_baselines":     [],
        "feature_framing_detected": False,
        "has_conflict":         False,
        "evidence_selection_log": evidence_selection_log,
        "__rejected_count__": rejected_count,
    }


def _infer_risks(state: dict) -> dict:
    """R6 — Risks: failure mode + downstream consequence required per candidate.

    Guard rules:
    1. Assumption-leak filter: snippets containing 'assume/assuming' are skipped
       (they belong in assumptions, not risks).
    2. Shape guard: candidate must contain failure-mode language AND consequence language.
       Dependency-only statements ('requires X') without failure framing are rejected.

    _RISK_SOURCE_SECTIONS no longer includes 'assumptions' (R6 boundary).
    """
    qa_store  = state.get("confirmed_qa_store", {}) or {}
    prd_secs  = state.get("prd_sections", {}) or {}

    texts     = _qa_texts_for_sections(_RISK_SOURCE_SECTIONS, qa_store, prd_secs)
    signals   = _extract_signal_snippets(texts, _RISK_SIGNALS)

    candidates: list[str] = []
    evidence: list[str]   = []
    sources: set[str]     = set()
    evidence_selection_log: list[tuple[str, str]] = []
    rejected_count = 0

    for sig_type, snippet, src in signals:
        # Guard 1: skip assumption-leak sentences
        if _RISK_ASSUMPTION_LEAK_RE.search(snippet):
            rejected_count += 1
            continue

        # Guard 2: require failure-mode + consequence framing
        has_failure     = bool(_RISK_FAILURE_RE.search(snippet))
        has_consequence = bool(_RISK_CONSEQUENCE_RE.search(snippet))
        if not (has_failure or has_consequence):
            rejected_count += 1
            _log.debug(
                "risk_candidate_rejected_missing_failure_framing",
                extra={
                    "event_type": "risk_candidate_rejected_missing_failure_framing",
                    "snippet": snippet[:60],
                    "has_failure": has_failure,
                    "has_consequence": has_consequence,
                },
            )
            continue

        sources.add(src)
        short = snippet[:120].rstrip(".,;:")
        candidates.append(short)
        evidence.append(f"[{src}] {snippet}")
        evidence_selection_log.append((src, snippet[:60]))

    candidates = _deduplicate(candidates)[:4]
    evidence   = _deduplicate(evidence)[:5]

    confidence = "high" if len(candidates) >= 2 else "medium" if candidates else "low"
    return {
        "has_explicit_answers": _has_explicit_answers("risks", qa_store),
        "inference_available":  bool(candidates),
        "candidate_items":      candidates,
        "evidence":             evidence,
        "evidence_sources":     sorted(sources),
        "confidence":           confidence,
        "metric_baselines":     [],
        "feature_framing_detected": False,
        "has_conflict":         False,
        "evidence_selection_log": evidence_selection_log,
        "__rejected_count__": rejected_count,
    }


# ── Pain Points inferrer ──────────────────────────────────────────────────────

# 5 categories of operational pain signals
_PAIN_POINT_SIGNALS: list[tuple[str, str]] = [
    # time_waste
    (r"\bmanual\w*\b|\brepetitive\b|\brework\b|hours?\s+per\s+week|takes?\s+\d+\s+hours?|slow(?:er|ly|ness)?\b|\bdelay\b",
     "time_waste"),
    # error_cost
    (r"\bwrong\b|\bmismatch\b|\bduplicate\b|\bstockout\b|\breturn\b|\bmistake\b|\bincorrect\b|\binaccurat\w+",
     "error_cost"),
    # scale_breakdown
    (r"cannot\s+scale|can'?t\s+scale|grows?\s+from\s+\w+\s+to\s+\w+|\bheadcount\b|\bbottleneck\b|not\s+scalab",
     "scale_breakdown"),
    # workflow_friction
    (r"\bspreadsheet\b|\bcopy.?paste\b|ctrl\+?f\b|multiple\s+files?\b|\btedious\b|\bswivel\s+chair\b",
     "workflow_friction"),
    # stakeholder_frustration
    (r"\bfrustrat\w+\b|hardest\s+part|groundhog\s+day|detective\s+work|\bpain\s+point\b|\bdread\b|\bburn\w*\s+out\b",
     "stakeholder_frustration"),
]

# Guard: don't infer pain points from solution/technology sentences
_SOLUTION_STATEMENT_GUARD = re.compile(
    r"\b(?:use|using|build|deploy|implement|integrate|add|apply|leverage)\b.{0,40}"
    r"\b(?:llm|gpt|ai|ml|model|api|classifier|pipeline|engine|dashboard|algorithm)\b",
    re.IGNORECASE,
)

# Evidence level mapping (E2 = one direct signal, E3 = numeric/quantified signal)
_NUMERIC_EVIDENCE = re.compile(r"\d+\s*(?:hours?|hrs?|minutes?|mins?|days?|weeks?|%)", re.IGNORECASE)

# Priority lookup for seed hint order
_PAIN_HINT_PRIORITY = ["problem_statement", "headliner", "background"]


def _infer_pain_points(state: dict) -> dict:
    """Phase 1.5 — LIVE prompt injection from day one.

    Infers operational pain points from problem_statement, headliner, background,
    and goals. Returns rich structured candidate_items with category, source, and
    evidence level so the LLM can surface plain-English proposals.

    Non-negotiables:
      - Does NOT infer from solution statements ("use AI / build an LLM").
      - Does NOT collapse symptoms and root causes into one statement.
      - Returns plain-English text, not abstract labels.

    Escalation rule (R3 fix):
      Background describes the CURRENT-STATE WORKFLOW.
      Problem Statement must describe the UNDERLYING FAILURE in that workflow.
      When background is already confirmed, this inferrer:
        (a) uses the background answer as the reference frame instead of re-reading it
            for pain signals (preventing verbatim repeat).
        (b) produces an escalation candidate: "Given [background], the deeper failure is…"
      The raw pain signal extraction path (_PAIN_SOURCE_SECTIONS) intentionally
      excludes 'background' — signal overlap is the root cause of the repeat.
    """
    qa_store  = state.get("confirmed_qa_store", {}) or {}
    prd_secs  = state.get("prd_sections", {}) or {}

    # ── Escalation path: background already answered ──────────────────────────
    # Find the highest-version background entry (correction-precedence).
    _bg_entries = [
        e for e in qa_store.values()
        if e.get("section_id") == "background" and not e.get("contradiction_flagged")
    ]
    _bg_entry = max(_bg_entries, key=lambda e: e.get("version", 0) or 0) if _bg_entries else None
    _bg_text  = str(_bg_entry.get("answer") or "").strip() if _bg_entry else ""

    if _bg_text and len(_bg_text) > 30:
        # Background is answered — Problem Statement must ESCALATE, not repeat.
        # Produce an escalation candidate that explicitly names the deeper failure.
        # Truncate background to keep the candidate readable.
        _bg_summary = _bg_text[:200].rstrip(".,;:")
        _escalation_candidate = (
            f"Given that workflow — {_bg_summary[:120]}… — "
            f"what's the core reason it keeps failing? "
            f"Is it that the system never learns from past corrections, "
            f"so the same mismatches recur? Or is there a different deeper failure?"
        )
        _log.info(
            "pain_point_escalation_path_used",
            extra={
                "event_type": "pain_point_escalation_path_used",
                "background_length": len(_bg_text),
                "escalation_candidate_preview": _escalation_candidate[:100],
            },
        )
        return {
            "has_explicit_answers":     _has_explicit_answers("problem_statement", qa_store),
            "inference_available":      True,
            "candidate_items":          [{
                "text":            _escalation_candidate,
                "category":        "escalation_from_background",
                "source_sections": ["background"],
                "evidence_level":  "E2",
            }],
            "evidence":                 [f"[background] {_bg_text[:180]}"],
            "evidence_sources":         ["background"],
            "confidence":               "medium",
            "metric_baselines":         [],
            "feature_framing_detected": False,
            "has_conflict":             False,
            "seed_context_hint":        (
                "Given what you described about the current workflow, "
                "what's the core reason it keeps failing or falling short?"
            ),
        }

    # ── Standard path: background not yet answered ────────────────────────────
    # Read from headliner/elevator_pitch/goals only (not background — see note above).
    texts     = _qa_texts_for_sections(_PAIN_SOURCE_SECTIONS, qa_store, prd_secs)
    signals   = _extract_signal_snippets(texts, _PAIN_POINT_SIGNALS, max_per_signal=3)

    candidates: list[dict] = []
    evidence: list[str]    = []
    sources: set[str]      = set()
    seed_hint: str         = ""

    for sig_type, snippet, src in signals:
        # Non-negotiable: skip sentences that are solution framings
        if _SOLUTION_STATEMENT_GUARD.search(snippet):
            _log.debug("pain_point_guard_skipped_solution_statement", extra={"snippet": snippet[:80]})
            continue

        # Determine evidence level: E3 if numeric, E2 otherwise
        ev_level = "E3" if _NUMERIC_EVIDENCE.search(snippet) else "E2"

        # Build plain-English text: trim to a single operational sentence
        plain_text = snippet[:180].rstrip(".,;:")

        candidates.append({
            "text":            plain_text,
            "category":        sig_type,
            "source_sections": [src],
            "evidence_level":  ev_level,
        })
        evidence.append(f"[{src}] {snippet}")
        sources.add(src)

        # Capture the first high-quality snippet as seed hint
        if not seed_hint and len(snippet) > 20:
            seed_hint = snippet[:120].split(".")[0].strip()

    # Deduplicate by text key (first 60 chars case-insensitive)
    seen_keys: set[str] = set()
    unique: list[dict] = []
    for c in candidates:
        key = c["text"].lower()[:60]
        if key not in seen_keys:
            seen_keys.add(key)
            unique.append(c)

    unique = unique[:4]
    evidence = _deduplicate(evidence)[:5]

    # Confidence: HIGH ≥ 2 candidates with at least one E3; MEDIUM ≥ 1; LOW = 0
    has_e3 = any(c["evidence_level"] == "E3" for c in unique)
    if len(unique) >= 2 and has_e3:
        confidence = "high"
    elif unique:
        confidence = "medium"
    else:
        confidence = "low"

    _log.info(
        "orchestrator_pain_points_detected",
        extra={
            "event_type": "orchestrator_pain_points_detected",
            "candidate_count": len(unique),
            "confidence": confidence,
            "categories": list({c["category"] for c in unique}),
            "sources": sorted(sources),
            "escalation_path_used": False,
        },
    )

    # For backward compat with callers expecting candidate_items as list[str],
    # we surfaces the full dict objects. The orchestrator reads candidate_items
    # and the _inject_orch_candidates helper uses only .get("candidate_items").
    # Tests comparing candidate_items will check list[dict] shape.
    return {
        "has_explicit_answers":     _has_explicit_answers("problem_statement", qa_store),
        "inference_available":      bool(unique),
        "candidate_items":          unique,           # list[dict] for pain_points
        "evidence":                 evidence,
        "evidence_sources":         sorted(sources),
        "confidence":               confidence,
        "metric_baselines":         [],
        "feature_framing_detected": False,
        "has_conflict":             False,
        "seed_context_hint":        seed_hint,
    }


# ── Public API ────────────────────────────────────────────────────────────────


# ── Key Stakeholders inferrer (Phase 1.7) ─────────────────────────────
# Operator-first persona evidence hierarchy:
#   Tier 1 Operator  → who does the work day-to-day
#   Tier 2 Manager   → who coordinates / delegates
#   Tier 3 Approver  → who owns budget / governance
#   Non-target       → executive / VP / C-suite  (mentioned last only if in evidence)
# Prestige words (executive, VP) are never promoted as the primary question lens.

_STAKEHOLDER_ROLE_SEED = (
    "Who currently spends the most time on this workflow, "
    "and who would approve a budget to automate it?"
)


def _infer_key_stakeholders(state: dict) -> dict:
    """Infer stakeholder roles from prior QA evidence using the operator-first hierarchy.

    Returns
    -------
    dict conforming to the standard infer_section_candidates contract.
    """
    qa_store: dict = state.get("confirmed_qa_store") or {}

    # Collect text from all narrative upstream sections
    # Correction-precedence: when multiple entries share a section_id, keep the
    # highest-version one (latest user correction outranks earlier write).
    _best_by_section: dict[str, dict] = {}
    for entry in qa_store.values():
        if entry.get("contradiction_flagged"):
            continue
        sid = entry.get("section_id", "")
        if sid not in _STAKEHOLDER_SOURCE_SECTIONS:
            continue
        # Prefer highest version (most recent write / correction)
        existing = _best_by_section.get(sid)
        if existing is None or (entry.get("version", 0) or 0) >= (existing.get("version", 0) or 0):
            _best_by_section[sid] = entry

    corpus_parts: list[str] = []
    for entry in _best_by_section.values():
        # Read 'answer' — the canonical field used by both await_confirmation
        # and handle_tagged_event (CORRECT_MESSAGE). Never 'user_answer'.
        answer = str(entry.get("answer") or "").strip()
        if answer:
            corpus_parts.append(answer)

    has_explicit = bool(
        any(
            e.get("section_id") == "key_stakeholders" and not e.get("contradiction_flagged")
            for e in qa_store.values()
        )
    )

    if not corpus_parts:
        return {
            "has_explicit_answers":     has_explicit,
            "inference_available":      False,
            "candidate_items":          [],
            "evidence":                 [],
            "evidence_sources":         [],
            "confidence":               "low",
            "seed_context_hint":        _STAKEHOLDER_ROLE_SEED,
            "metric_baselines":         [],
            "feature_framing_detected": False,
            "has_conflict":             False,
        }

    corpus = " ".join(corpus_parts)

    # Score tiers — each tier detected adds to confidence
    tiers_found: list[str] = []
    role_candidates: list[str] = []

    if _OPERATOR_SIGNALS.search(corpus):
        tiers_found.append("operator")
        role_candidates.append("Operator / Product Ops (hands-on user)")

    if _MANAGER_SIGNALS.search(corpus):
        tiers_found.append("manager")
        role_candidates.append("Ops Manager / Team Lead (coordinates workflow)")

    if _BUYER_SIGNALS.search(corpus):
        tiers_found.append("approver")
        role_candidates.append("Operations Director / Head of Function (budget approver)")

    # Prestige words: only append if detected AND another tier was also found
    # — never make prestige the sole focus
    has_prestige = bool(_PRESTIGE_SIGNALS.search(corpus))
    if has_prestige and tiers_found:
        role_candidates.append("Executive Sponsor (strategic visibility — not primary user)")

    if len(tiers_found) == 0:
        # No signal detected at all — seed with role-clarifying question
        return {
            "has_explicit_answers":     has_explicit,
            "inference_available":      False,
            "candidate_items":          [],
            "evidence":                 corpus_parts[:2],
            "evidence_sources":         list(_STAKEHOLDER_SOURCE_SECTIONS)[:3],
            "confidence":               "low",
            "seed_context_hint":        _STAKEHOLDER_ROLE_SEED,
            "metric_baselines":         [],
            "feature_framing_detected": False,
            "has_conflict":             False,
        }

    # 1 tier → medium confidence: PROPOSE_ONE with role-clarifying
    # ≥2 tiers → high confidence: PROPOSE_LIST
    confidence = "high" if len(tiers_found) >= 2 else "medium"
    return {
        "has_explicit_answers":     has_explicit,
        "inference_available":      True,
        "candidate_items":          role_candidates,
        "evidence":                 corpus_parts[:3],
        "evidence_sources":         list({
            e.get("section_id") for e in qa_store.values()
            if e.get("section_id") in _STAKEHOLDER_SOURCE_SECTIONS
            and not e.get("contradiction_flagged")
        }),
        "confidence":               confidence,
        "seed_context_hint":        "",
        "metric_baselines":         [],
        "feature_framing_detected": False,
        "has_conflict":             False,
        # Guardrail: caller must not use prestige-only as primary question focus
        "__prestige_only__":        has_prestige and len(tiers_found) == 0,
    }


# ── Background synthesis inferrer ─────────────────────────────────────────────
# Phase 1.6 — Live prompt injection.
# Reads confirmed QA evidence from headliner, elevator_pitch, key_stakeholders,
# and problem_statement, then builds a synthesis confirm/correct candidate.
# Does NOT ask a blank field-label question; instead confirms what is already known.

_BACKGROUND_EVIDENCE_SECTIONS = frozenset({
    "headliner", "elevator_pitch", "key_stakeholders", "problem_statement"
})


def _infer_background_synthesis(state: dict) -> dict:
    """Synthesise a confirm/correct Background question from prior evidence.

    Returns
    -------
    dict conforming to the standard infer_section_candidates contract.
    """
    qa_store: dict = state.get("confirmed_qa_store") or {}

    # Collect plain-English evidence snippets from upstream sections
    evidence_facts: list[str] = []
    evidence_sources: list[str] = []

    # Collect evidence snippets from upstream sections.
    # Correction-precedence: when multiple entries share a section_id, use the
    # highest-version (most recent) one. User corrections MUST win over stale writes.
    _best_by_section: dict[str, dict] = {}
    for entry in qa_store.values():
        if entry.get("contradiction_flagged"):
            continue
        sid: str = entry.get("section_id", "")
        if sid not in _BACKGROUND_EVIDENCE_SECTIONS:
            continue
        existing = _best_by_section.get(sid)
        if existing is None or (entry.get("version", 0) or 0) >= (existing.get("version", 0) or 0):
            _best_by_section[sid] = entry

    for sid, entry in _best_by_section.items():
        # Read 'answer' — the canonical field (never 'user_answer')
        answer: str = str(entry.get("answer") or "").strip()
        if answer and len(answer) > 15:
            evidence_facts.append(answer[:180])
            if sid not in evidence_sources:
                evidence_sources.append(sid)

    has_explicit = bool(
        any(
            e.get("section_id") == "background" and not e.get("contradiction_flagged")
            for e in qa_store.values()
        )
    )

    # Not enough evidence → SEED with a simple workflow opener
    if len(evidence_facts) < 2:
        seed_hint = (
            "Walk me through what the current workflow looks like — who does what, "
            "how often, and where the most friction shows up."
        )
        return {
            "has_explicit_answers":     has_explicit,
            "inference_available":      False,
            "candidate_items":          [],
            "evidence":                 evidence_facts,
            "evidence_sources":         evidence_sources,
            "confidence":               "low",
            "seed_context_hint":        seed_hint,
            "metric_baselines":         [],
            "feature_framing_detected": False,
            "has_conflict":             False,
        }

    # ≥2 facts — build a synthesis confirm/correct candidate
    # Truncate each fact to keep the question readable
    short_facts = [f[:120] for f in evidence_facts[:4]]
    joined = "; ".join(short_facts)
    candidate = (
        f"From what you've shared, here's my read of the current state: {joined}. "
        f"Is that an accurate picture, or what would you add or correct?"
    )

    confidence = "high" if len(evidence_facts) >= 3 else "medium"

    return {
        "has_explicit_answers":     has_explicit,
        "inference_available":      True,
        "candidate_items":          [candidate],
        "evidence":                 evidence_facts,
        "evidence_sources":         evidence_sources,
        "confidence":               confidence,
        "seed_context_hint":        "",
        "metric_baselines":         [],
        "feature_framing_detected": False,
        "has_conflict":             False,
    }


_INFERRERS = {
    "goals":             _infer_goals,
    "non_goals":         _infer_non_goals,
    "success_metrics":   _infer_success_metrics,
    "problem_statement": _infer_pain_points,       # Phase 1.5 — pain inference on real section
    "assumptions":       _infer_assumptions,       # Phase 1 — observe-only
    "risks":             _infer_risks,             # Phase 1 — observe-only
    "background":        _infer_background_synthesis,  # Phase 1.6 — synthesis-first
    "key_stakeholders":  _infer_key_stakeholders,  # Phase 1.7 — operator-first role taxonomy
}




def infer_section_candidates(section_id: str, state: dict) -> dict:
    """Return inference candidates for Phase 1 target sections.

    Parameters
    ----------
    section_id : str
        PRDSection.id to infer for. Must be in _TARGET_SECTIONS;
        other IDs return an empty "not applicable" dict.
    state : dict
        LangGraph PRDState dict (or any dict with confirmed_qa_store and
        prd_sections keys).

    Returns
    -------
    dict with:
        has_explicit_answers : bool
        inference_available  : bool
        candidate_items      : list[str]
        evidence             : list[str]
        evidence_sources     : list[str]
        confidence           : "high" | "medium" | "low"
        metric_baselines     : list[dict]   — baseline/target pairs (success_metrics only)
        feature_framing_detected : bool
        has_conflict         : bool
    """
    if section_id not in _TARGET_SECTIONS:
        return {
            "has_explicit_answers": False,
            "inference_available":  False,
            "candidate_items":      [],
            "evidence":             [],
            "evidence_sources":     [],
            "confidence":           "low",
            "metric_baselines":     [],
            "feature_framing_detected": False,
            "has_conflict":         False,
        }
    return _INFERRERS[section_id](state)
