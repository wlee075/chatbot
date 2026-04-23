"""utils/report_composer.py — PRD Report Brain.

Governs derived report section synthesis. Completely separate from orchestrator.

Invariants:
- Composer never routes conversation.
- Composer never calls inference_first_prd_orchestrator.
- PDF renderer receives only what the composer already prepared.
- Composer output is cached by content hash; stale state triggers recompute.

Public API
----------
compose_report(state, trigger) -> ComposedReport              # sync
compose_report_async(state, trigger) -> ComposedReport        # async entrypoint

ComposedReport shape
--------------------
{
    "executive_summary":  str,
    "section_summaries":  list[dict],   # from _build_section_summaries
    "open_questions":     list[str],    # derived
    "next_steps":         list[str],    # derived
    "report_title":       str,
    "completion_pct":     int,
    "trigger":            str,
    "generated_at":       str,
    "source_hash":        str,
}
"""
from __future__ import annotations

import asyncio
import datetime
import hashlib
import json
import logging
from typing import Any

_log = logging.getLogger("orchestrator_metrics")

# ── Section ordered list (imported lazily to avoid circular import) ──────────
def _get_prd_sections():
    from config.sections import PRD_SECTIONS  # noqa: PLC0415
    return PRD_SECTIONS


# ─────────────────────────────────────────────────────────────────────────────
# Cache — module-level singleton
# Keyed by source_hash → ComposedReport dict
# Only one artifact is kept (the most recent).
# ─────────────────────────────────────────────────────────────────────────────
_cache: dict[str, dict] = {}  # {source_hash: ComposedReport}


# ─────────────────────────────────────────────────────────────────────────────
# Content hash
# ─────────────────────────────────────────────────────────────────────────────

def _make_source_hash(state: dict) -> str:
    """Stable hash of the state fields that drive report content.

    Changing confirmed_qa_store, prd_sections, section_scores, or pct
    invalidates the cache. Transient fields (e.g. chat_history, run_id)
    are intentionally excluded.
    """
    try:
        from utils.progress_rail import compute_progress_data
        pct = compute_progress_data(state, _get_prd_sections()).get("pct", 0)
    except Exception:
        pct = 0

    payload = {
        "qa":     state.get("confirmed_qa_store") or {},
        "secs":   state.get("prd_sections") or {},
        "scores": state.get("section_scores") or {},
        "pct":    pct,
    }
    serialised = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(serialised.encode()).hexdigest()[:20]


# ─────────────────────────────────────────────────────────────────────────────
# Derived section builders
# ─────────────────────────────────────────────────────────────────────────────

def _derive_open_questions(section_summaries: list[dict], qa_store: dict) -> list[str]:
    """Return actionable open questions from empty/partial sections and detected gaps.

    Rules:
    - status==empty → "Still need: {title}"
    - status==partial, still_needed → "{title}: {component} not yet confirmed"
    - Baseline known but no target in qa_store metrics → "Target not yet defined for {title}"
    - Contradiction-flagged entries → "Conflicting answers on {topic}"

    Never generates questions for derived sections (summary, exec summary, etc.).
    """
    DERIVED_IDS = {"summary", "executive_summary", "report_title",
                   "open_questions", "next_steps", "cross_section_highlights"}
    questions: list[str] = []

    for s in section_summaries:
        sid = s.get("id", "")
        title = s.get("title", sid)
        if sid in DERIVED_IDS:
            continue

        status = s.get("status", "empty")
        if status == "empty":
            questions.append(f"Still need: {title}")
        elif status == "partial":
            for comp in (s.get("still_needed") or []):
                questions.append(f"{title}: {comp} not yet confirmed")

    # Contradiction flags in qa_store
    for entry in (qa_store or {}).values():
        if not isinstance(entry, dict):
            continue
        if entry.get("contradiction_flagged"):
            topic = entry.get("section_id", "unknown topic")
            questions.append(f"Conflicting answers on {topic.replace('_', ' ')}")

    return questions[:10]  # cap to avoid overwhelming the PDF


def _derive_next_steps(section_summaries: list[dict], qa_store: dict) -> list[str]:
    """Return short, actionable next steps from detected gaps.

    Rules:
    - Empty sections → "Complete {title} section"
    - assumptions partial with no validation signal → "Add validation plan for each assumption"
    - success_metrics partial, no target → "Confirm success targets for Success Metrics"
    - key_stakeholders empty → "Confirm sign-off requirements with stakeholders"
    - risks empty → "Identify and prioritise project risks"

    Caps at 8 steps.
    """
    DERIVED_IDS = {"summary", "executive_summary", "report_title",
                   "open_questions", "next_steps", "cross_section_highlights"}
    steps: list[str] = []
    seen_sids: set[str] = set()

    for s in section_summaries:
        sid = s.get("id", "")
        title = s.get("title", sid)
        if sid in DERIVED_IDS or sid in seen_sids:
            continue
        seen_sids.add(sid)
        status = s.get("status", "empty")

        if status == "empty":
            # Special-cased wording for high-value sections
            if sid == "key_stakeholders":
                steps.append("Confirm sign-off requirements with stakeholders")
            elif sid == "risks":
                steps.append("Identify and prioritise project risks")
            elif sid == "assumptions":
                steps.append("List and validate key project assumptions")
            else:
                steps.append(f"Complete {title} section")
        elif status == "partial":
            if sid == "assumptions":
                steps.append("Add validation plan for each assumption")
            elif sid == "success_metrics":
                steps.append("Confirm success targets for Success Metrics")

    return steps[:8]


# ─────────────────────────────────────────────────────────────────────────────
# Core composer
# ─────────────────────────────────────────────────────────────────────────────

def _compose_report_internal(state: dict, trigger: str, source_hash: str) -> dict:
    """Build the ComposedReport dict from confirmed state.

    Steps:
    1. Build section summaries (deterministic).
    2. Build executive summary (deterministic + optional LLM enrichment).
    3. Derive open_questions.
    4. Derive next_steps.
    5. Assemble report_title and completion_pct.
    """
    from graph.nodes import _build_section_summaries, _build_executive_summary

    prd_sections = state.get("prd_sections") or {}
    qa_store = state.get("confirmed_qa_store") or {}

    _log.info("composer_refresh_started", extra={
        "event_type": "composer_refresh_started",
        "trigger": trigger,
        "source_hash": source_hash,
    })

    section_summaries = _build_section_summaries(prd_sections, qa_store)
    _log.info("composer_summary_regenerated", extra={
        "event_type": "composer_summary_regenerated",
        "section_count": len(section_summaries),
    })

    executive_summary = _build_executive_summary(section_summaries, state)

    open_questions = _derive_open_questions(section_summaries, qa_store)
    next_steps = _derive_next_steps(section_summaries, qa_store)

    # Report title
    report_title: str = state.get("prd_report_title") or ""
    if not report_title.strip():
        try:
            from config.sections import PRD_SECTIONS
            from utils.progress_rail import compute_progress_data
            pct = compute_progress_data(state, PRD_SECTIONS).get("pct", 0)
        except Exception:
            pct = 0
        status_label = "Draft" if pct < 100 else "Final"
        headliner_qa = next(
            (v for v in qa_store.values()
             if isinstance(v, dict) and v.get("section_id") == "headliner"),
            {}
        )
        hint = str(headliner_qa.get("answer", ""))[:40].strip()
        report_title = f"{status_label} Requirements Report" + (f" — {hint}" if hint else "")

    # Completion pct
    try:
        from config.sections import PRD_SECTIONS
        from utils.progress_rail import compute_progress_data
        completion_pct = compute_progress_data(state, PRD_SECTIONS).get("pct", 0)
    except Exception:
        completion_pct = 0

    generated_at = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")

    artifact: dict[str, Any] = {
        "executive_summary":  executive_summary,
        "section_summaries":  section_summaries,
        "open_questions":     open_questions,
        "next_steps":         next_steps,
        "report_title":       report_title,
        "completion_pct":     completion_pct,
        "trigger":            trigger,
        "generated_at":       generated_at,
        "source_hash":        source_hash,
    }

    _log.info("composer_refresh_finished", extra={
        "event_type": "composer_refresh_finished",
        "trigger": trigger,
        "source_hash": source_hash,
        "open_questions_count": len(open_questions),
        "next_steps_count": len(next_steps),
        "completion_pct": completion_pct,
    })

    return artifact


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def compose_report(state: dict, trigger: str) -> dict:
    """Synchronous composer entrypoint.

    Returns a cached artifact if state has not changed meaningfully.
    Otherwise recomputes and updates cache.

    Args:
        state:   The current PRDState values dict.
        trigger: One of "80pct" | "view_draft" | "download" | "session_end" | "major_update"

    Returns:
        ComposedReport dict with all derived fields.
    """
    source_hash = _make_source_hash(state)

    if source_hash in _cache:
        _log.info("composer_cache_hit", extra={
            "event_type": "composer_cache_hit",
            "trigger": trigger,
            "source_hash": source_hash,
        })
        return _cache[source_hash]

    _log.info("composer_cache_miss", extra={
        "event_type": "composer_cache_miss",
        "trigger": trigger,
        "source_hash": source_hash,
    })

    artifact = _compose_report_internal(state, trigger, source_hash)
    _cache.clear()          # keep memory bounded — only latest artifact
    _cache[source_hash] = artifact
    return artifact


async def compose_report_async(state: dict, trigger: str) -> dict:
    """Async-first composer entrypoint.

    Runs the synchronous composition work inside an executor thread so it does
    not block the event loop. Caching logic is identical to the sync version.

    Args:
        state:   The current PRDState values dict.
        trigger: One of "80pct" | "view_draft" | "download" | "session_end" | "major_update"

    Returns:
        ComposedReport dict with all derived fields.
    """
    source_hash = _make_source_hash(state)

    if source_hash in _cache:
        _log.info("composer_cache_hit", extra={
            "event_type": "composer_cache_hit",
            "trigger": trigger,
            "source_hash": source_hash,
        })
        return _cache[source_hash]

    _log.info("composer_cache_miss", extra={
        "event_type": "composer_cache_miss",
        "trigger": trigger,
        "source_hash": source_hash,
    })

    loop = asyncio.get_running_loop()
    artifact = await loop.run_in_executor(
        None,
        lambda: _compose_report_internal(state, trigger, source_hash),
    )
    _cache.clear()
    _cache[source_hash] = artifact
    return artifact
