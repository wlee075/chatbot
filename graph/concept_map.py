"""
Rule-based concept-to-sections impact mapping.

Used by detect_impact_node to find which already-drafted sections a newly
confirmed fact is likely to affect.  Keyword matching is intentionally broad
so we err on the side of over-detection; the material_change_threshold guard
in draft_node filters out spurious rewrites.

Keyword lookup is O(n) over a short table — no vector search required for
Phase 1.  LLM fallback is available when zero rule matches are found.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Concept → section mapping
# Keys: lowercase substrings matched against the concatenated Q&A text.
# Values: ordered list of section IDs (most broadly impacted first).
# ---------------------------------------------------------------------------
CONCEPT_SECTION_MAP: dict[str, list[str]] = {
    # ── Target users / personas ─────────────────────────────────────────────
    "target user":    ["elevator_pitch", "problem_statement", "proposed_solution", "success_metrics"],
    "user persona":   ["elevator_pitch", "problem_statement", "proposed_solution"],
    "customer":       ["elevator_pitch", "problem_statement", "proposed_solution"],
    "audience":       ["elevator_pitch", "problem_statement"],
    "end user":       ["elevator_pitch", "problem_statement", "proposed_solution"],
    "end-user":       ["elevator_pitch", "problem_statement", "proposed_solution"],
    "primary user":   ["elevator_pitch", "problem_statement"],

    # ── Problem / pain point ────────────────────────────────────────────────
    "pain point":     ["problem_statement", "background", "elevator_pitch"],
    "problem":        ["headliner", "elevator_pitch", "problem_statement", "background"],
    "issue":          ["problem_statement", "background"],
    "challenge":      ["problem_statement", "background"],
    "frustration":    ["problem_statement", "background"],
    "incident":       ["background", "problem_statement"],
    "bottleneck":     ["problem_statement", "background"],

    # ── Goals / success ─────────────────────────────────────────────────────
    "success metric": ["success_metrics", "goals"],
    "kpi":            ["success_metrics", "goals"],
    "goal":           ["goals", "success_metrics", "non_goals"],
    "outcome":        ["goals", "success_metrics", "headliner"],
    "objective":      ["goals", "success_metrics"],
    "measure":        ["success_metrics", "goals"],
    "metric":         ["success_metrics", "goals"],
    "target value":   ["success_metrics", "goals"],

    # ── Timeline / dates ────────────────────────────────────────────────────
    "timeline":       ["timeline", "goals", "success_metrics"],
    "deadline":       ["timeline", "goals"],
    "quarter":        ["timeline", "goals"],
    "sprint":         ["timeline"],
    "milestone":      ["timeline"],
    "launch date":    ["timeline", "proposed_solution"],
    "launch":         ["timeline", "proposed_solution"],
    "release":        ["timeline", "proposed_solution"],
    "go live":        ["timeline", "proposed_solution"],

    # ── Compliance / legal constraints ──────────────────────────────────────
    "compliance":     ["risks", "assumptions", "out_of_scope", "timeline"],
    "regulation":     ["risks", "assumptions", "out_of_scope"],
    "regulatory":     ["risks", "assumptions", "out_of_scope"],
    "gdpr":           ["risks", "assumptions", "out_of_scope"],
    "hipaa":          ["risks", "assumptions", "out_of_scope"],
    "sox":            ["risks", "assumptions"],
    "legal":          ["risks", "assumptions"],
    "constraint":     ["risks", "assumptions", "out_of_scope"],
    "policy":         ["risks", "assumptions", "proposed_solution"],
    "audit":          ["risks", "assumptions"],

    # ── Stakeholders / team ─────────────────────────────────────────────────
    "stakeholder":    ["key_stakeholders", "timeline", "risks"],
    "approver":       ["key_stakeholders"],
    "sign-off":       ["key_stakeholders"],
    "sign off":       ["key_stakeholders"],
    "executive":      ["key_stakeholders", "elevator_pitch"],
    "sponsor":        ["key_stakeholders"],
    "owner":          ["key_stakeholders", "timeline"],
    "responsible":    ["key_stakeholders"],

    # ── Solution / features ─────────────────────────────────────────────────
    "feature":        ["proposed_solution", "headliner", "elevator_pitch"],
    "solution":       ["proposed_solution", "headliner", "elevator_pitch"],
    "approach":       ["proposed_solution", "background"],
    "implementation": ["proposed_solution", "timeline", "risks"],
    "integration":    ["proposed_solution", "risks", "assumptions"],
    "api":            ["proposed_solution", "risks"],
    "architecture":   ["proposed_solution", "risks"],
    "capability":     ["proposed_solution", "elevator_pitch"],
    "workflow":       ["proposed_solution", "problem_statement"],
    "automation":     ["proposed_solution", "goals"],

    # ── Risks / dependencies ────────────────────────────────────────────────
    "risk":           ["risks", "assumptions", "timeline"],
    "dependency":     ["risks", "assumptions", "timeline"],
    "blocker":        ["risks", "timeline"],
    "assumption":     ["assumptions", "risks"],
    "dependency on":  ["risks", "timeline"],
    "third party":    ["risks", "assumptions", "proposed_solution"],
    "vendor":         ["risks", "assumptions", "proposed_solution"],

    # ── Scope ───────────────────────────────────────────────────────────────
    "out of scope":   ["out_of_scope", "non_goals"],
    "not in scope":   ["out_of_scope", "non_goals"],
    "non-goal":       ["non_goals", "out_of_scope"],
    "non goal":       ["non_goals", "out_of_scope"],
    "exclude":        ["non_goals", "out_of_scope"],
    "deferred":       ["non_goals", "out_of_scope"],
    "phase 2":        ["non_goals", "out_of_scope", "timeline"],

    # ── Background / context ────────────────────────────────────────────────
    "background":     ["background", "problem_statement"],
    "history":        ["background"],
    "prior work":     ["background"],
    "previously":     ["background"],
    "legacy":         ["background", "proposed_solution"],
    "root cause":     ["background", "problem_statement"],
}

# Canonical section order — used to break scoring ties (earlier = higher priority).
_SECTION_ORDER: list[str] = [
    "headliner",
    "elevator_pitch",
    "key_stakeholders",
    "background",
    "problem_statement",
    "goals",
    "success_metrics",
    "non_goals",
    "assumptions",
    "out_of_scope",
    "proposed_solution",
    "risks",
    "timeline",
]


def get_impacted_sections(
    question: str,
    answer: str,
    already_drafted: set[str],
    current_section_id: str,
    max_sections: int = 2,
) -> list[str]:
    """
    Rule-based impact lookup.

    Scans the concatenated question + answer text for known keyword fragments.
    Each keyword hit contributes 1 point to each of its listed target sections.
    Returns up to `max_sections` section IDs that:
      - are already in `already_drafted` (have an existing draft worth updating)
      - are not the `current_section_id` (that one is always re-drafted normally)

    Sorted: highest score first; ties broken by PRD section order (earlier
    sections are more foundational and rewarded with higher priority).
    """
    text = (question + " " + answer).lower()
    scored: dict[str, int] = {}

    for keyword, sections in CONCEPT_SECTION_MAP.items():
        if keyword in text:
            for sec_id in sections:
                if sec_id in already_drafted and sec_id != current_section_id:
                    scored[sec_id] = scored.get(sec_id, 0) + 1

    if not scored:
        return []

    def _sort_key(sec_id: str) -> tuple[int, int]:
        order = _SECTION_ORDER.index(sec_id) if sec_id in _SECTION_ORDER else 99
        return (-scored[sec_id], order)  # score desc, then PRD order asc

    return sorted(scored.keys(), key=_sort_key)[:max_sections]
