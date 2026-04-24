"""utils/progress_rail.py — pure computation helper for the sticky progress rail.

This module is deliberately isolated from Streamlit so it can be unit-tested
without any UI imports.  The render layer (`_render_progress_rail` in app.py)
calls `compute_progress_data` and then does all the Streamlit work.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


# ── Display helpers ───────────────────────────────────────────────────────────

def _fmt_section_title(raw: str) -> str:
    """Minimal user-facing label conversion (mirrors app.py _display_section_title)."""
    if not raw:
        return raw
    low = raw.strip().lower()
    if low in {"headliner", "headliner paragraph", "tldr", "tl;dr"}:
        return "Summary"
    return raw.strip()


# ── Core data contract ────────────────────────────────────────────────────────

def compute_progress_data(
    sv: dict,
    prd_sections: list,  # PRD_SECTIONS from config.sections
) -> dict:
    """Compute all display-ready progress values from graph session state.

    Parameters
    ----------
    sv : dict
        Raw graph state values dict (gstate.values).
    prd_sections : list
        Ordered list of PRDSection objects (imported from config.sections).

    Returns
    -------
    dict with keys:
        pct           : int   — 0-100 overall completion percentage
        completed     : int   — number of completed sections
        total         : int   — total sections
        current_id    : str   — ID of the active section
        current_title : str   — human-readable title of the active section
        checklist     : list[dict]  — [{id, title, status}]
                        status ∈ {complete, current, partial, pending}
                        partial = has answers captured but no PASS verdict yet
        still_needed  : list[str]  — ≤3 plain-English items missing in the current section
    """
    if not isinstance(sv, dict):
        sv = {}

    section_scores: dict = sv.get("section_scores") or {}
    confirmed_qa: dict   = sv.get("confirmed_qa_store") or {}
    section_index: int   = sv.get("section_index") or 0

    total = len(prd_sections)
    if total == 0:
        return {
            "pct": 0, "completed": 0, "total": 0,
            "current_id": "", "current_title": "",
            "checklist": [], "still_needed": [],
        }

    # Clamp index
    idx = max(0, min(int(section_index), total - 1))
    current_section = prd_sections[idx]

    # ── Completed sections ────────────────────────────────────────────────────
    # Two canonical completion signals, either is sufficient:
    #   1. Explicit PASS verdict written to section_scores by the scoring node.
    #   2. section_index has advanced PAST this position (i < idx) — this is the
    #      authoritative signal written by advance_section_node regardless of
    #      whether the advance was via PASS or a forced cap (ITER_CAP/RECOVERY_CAP).
    #
    # The old code also required _is_current_section_incomplete to be False for
    # path 2 — which demanded a PASS verdict — defeating the entire heuristic for
    # ITER_CAP advances.  That guard is REMOVED.
    completed_count = 0
    checklist: list[dict] = []

    for i, sec in enumerate(prd_sections):
        score_entry = section_scores.get(sec.id, {})
        verdict = (score_entry.get("verdict", "") if isinstance(score_entry, dict) else "")
        is_complete = (verdict == "PASS") or (i < idx)

        if is_complete:
            completed_count += 1

        # Has the user captured any answer for this section (without PASS)?
        is_partial = (
            not is_complete
            and sec.id != current_section.id
            and any(
                isinstance(v, dict) and v.get("section_id") == sec.id
                for v in confirmed_qa.values()
            )
        )

        is_current = sec.id == current_section.id
        if is_current:
            status = "current"
        elif is_complete:
            status = "complete"
        elif is_partial:
            status = "partial"
        else:
            status = "pending"

        checklist.append({
            "id": sec.id,
            "title": _fmt_section_title(sec.title),
            "status": status,
        })

    pct = round(completed_count / total * 100)

    # ── Still needed for the active section ──────────────────────────────────
    # Source: expected_components not covered by confirmed answers for this section.
    still_needed = _derive_still_needed(current_section, confirmed_qa, limit=3)

    return {
        "pct": pct,
        "completed": completed_count,
        "total": total,
        "current_id": current_section.id,
        "current_title": _fmt_section_title(current_section.title),
        "checklist": checklist,
        "still_needed": still_needed,
    }


# ── Internal helpers ──────────────────────────────────────────────────────────
# _is_current_section_incomplete intentionally removed:
# completed-section detection now uses `i < idx` as the sole positional signal,
# which is always set by advance_section_node regardless of PASS vs forced cap.


def _derive_still_needed(section, confirmed_qa: dict, limit: int = 3) -> list[str]:
    """Return ≤ `limit` expected_components of `section` not yet covered.

    Coverage check: any keyword from the first 3 words of the component
    appears in any confirmed answer for this section.
    """
    expected: list = getattr(section, "expected_components", None) or []
    if not expected:
        return []

    # Gather confirmed answers for this section
    section_answers = " ".join(
        str(v.get("answer", ""))
        for v in confirmed_qa.values()
        if isinstance(v, dict) and v.get("section_id") == section.id
        and not v.get("contradiction_flagged", False)
    ).lower()

    missing: list[str] = []
    for comp in expected:
        keywords = comp.lower().split()[:3]
        if not any(kw in section_answers for kw in keywords):
            missing.append(comp)
        if len(missing) >= limit:
            break

    return missing


# ── PDF download gate ─────────────────────────────────────────────────────────

def get_pdf_download_state(pct: int) -> dict:
    """Return the canonical PDF button state for a given completion percentage.

    Returns
    -------
    dict with keys:
        enabled  : bool   — whether the download button should be active
        label    : str    — button label text (with emoji)
        btn_type : str    — Streamlit button type: "primary" | "secondary"
        badge    : str    — short pill label: "" | "Draft" | "Complete"
        hint     : str    — tooltip / caption text shown near the button
    """
    if pct < 80:
        return {
            "enabled": False,
            "label": "🔒 Download PDF",
            "btn_type": "secondary",
            "badge": "",
            "hint": f"{pct}% complete — reach 80% to enable PDF export",
        }
    if pct < 100:
        return {
            "enabled": True,
            "label": "📥 Download PDF",
            "btn_type": "secondary",
            "badge": "Draft",
            "hint": f"{pct}% complete — draft export available",
        }
    return {
        "enabled": True,
        "label": "📥 Download PDF",
        "btn_type": "primary",
        "badge": "Complete",
        "hint": "100% complete — final report ready",
    }
