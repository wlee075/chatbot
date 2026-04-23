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
        checklist     : list[dict]  — [{id, title, status}], status ∈ {complete,current,pending}
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
    # Primary signal: PASS verdict from section_scores.
    # Secondary heuristic: section is before the current index (was advanced over).
    completed_count = 0
    checklist: list[dict] = []

    for i, sec in enumerate(prd_sections):
        score_entry = section_scores.get(sec.id, {})
        verdict = (score_entry.get("verdict", "") if isinstance(score_entry, dict) else "")
        is_complete = (verdict == "PASS") or (
            i < idx and not _is_current_section_incomplete(sec, section_scores, confirmed_qa)
        )

        if is_complete:
            completed_count += 1

        is_current = sec.id == current_section.id
        if is_current:
            status = "current"
        elif is_complete:
            status = "complete"
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

def _is_current_section_incomplete(sec, section_scores: dict, confirmed_qa: dict) -> bool:
    """Returns True if this section clearly has NOT been completed (for heuristic guard)."""
    score_entry = section_scores.get(sec.id, {})
    if not isinstance(score_entry, dict):
        return True
    verdict = score_entry.get("verdict", "")
    return verdict not in ("PASS",)


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
