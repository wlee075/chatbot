"""
Evaluation cases for the PRD Reflector scoring harness.

Schema per case
───────────────
  case_id            : "{section_id_short}_{label}_{nn}" — unique key used in CSV logs
  section_id         : matches PRDSection.id in config/sections.py (config lookup at runtime)
  label              : human tier — "strong" | "medium" | "poor" | "very_poor"
  prior_sections     : context passed as prior completed sections (empty = isolated test)
  draft_text         : section text submitted to the Reflector
  expected_score_min : lower bound of expected OVERALL SCORE (inclusive)
  expected_score_max : upper bound of expected OVERALL SCORE (inclusive)
  expected_verdict   : "PASS" | "REWORK"
  expected_triage    : "NORMAL" | "RECOVERY"  (runner expands to full strings)
  notes              : what this case is probing

Mapping from legacy fixtures
─────────────────────────────
  F01 → tldr_strong_01
  F02 → tldr_poor_01
  F03 → goals_strong_01
  F04 → goals_very_poor_01
  F05 → metrics_strong_01
  F06 → metrics_medium_01
  F07 → metrics_very_poor_01
  F08 → risks_strong_01
  F09 → risks_poor_01
  F10 → non_goals_medium_01
"""

CASES = [
    # ─────────────────────────────────────────────────────────────────────────
    # tl;dr — strong
    # ─────────────────────────────────────────────────────────────────────────
    {
        "case_id": "tldr_strong_01",
        "section_id": "tldr",
        "label": "strong",
        "prior_sections": "",
        "draft_text": (
            "The INCA content moderation system currently applies a single uniform "
            "review threshold to all merchant types (RMT, CMT, COI), resulting in a "
            "17% false positive rate for RMT merchants and a 6-week average review "
            "backlog. This initiative introduces risk-differentiated moderation "
            "thresholds: RMT merchants above a 90-day GMV threshold of $50,000 USD "
            "will receive a fast-track review SLA of 24 hours; CMT and COI merchants "
            "retain the current 5-business-day SLA. Expected outcome: reduce RMT "
            "false positive rate from 17% to ≤8% and reduce average backlog from 6 "
            "weeks to 2 weeks by Q4 2026."
        ),
        "expected_score_min": 8.5,
        "expected_score_max": 10.0,
        "expected_verdict": "PASS",
        "expected_triage": "NORMAL",
        "notes": (
            "Complete, specific, no vague language, quantified problem and outcome. "
            "Should consistently PASS."
        ),
    },
    # ─────────────────────────────────────────────────────────────────────────
    # tl;dr — poor
    # ─────────────────────────────────────────────────────────────────────────
    {
        "case_id": "tldr_poor_01",
        "section_id": "tldr",
        "label": "poor",
        "prior_sections": "",
        "draft_text": (
            "The current content moderation process has some issues that affect "
            "merchant experience. We want to improve the process to make it better "
            "and faster for merchants while reducing errors. The new system will use "
            "smarter logic to differentiate between merchant types and apply "
            "appropriate review workflows. This will improve outcomes for the "
            "business and for merchants going forward."
        ),
        "expected_score_min": 5.0,
        "expected_score_max": 6.9,
        "expected_verdict": "REWORK",
        "expected_triage": "NORMAL",
        "notes": (
            "Vague language, no metrics, solution mentioned but undefined. "
            "Should REWORK but not reach recovery threshold."
        ),
    },
    # ─────────────────────────────────────────────────────────────────────────
    # Goals — strong
    # ─────────────────────────────────────────────────────────────────────────
    {
        "case_id": "goals_strong_01",
        "section_id": "goals",
        "label": "strong",
        "prior_sections": "",
        "draft_text": (
            "1. Reduce the false positive rate for RMT merchants from the current "
            "17% to ≤8% as measured by the weekly INCA moderation dashboard by "
            "Q4 2026.\n"
            "2. Reduce the average merchant review backlog from 6 weeks to ≤2 weeks "
            "for all merchant tiers by Q3 2026, measured by the p90 queue age "
            "metric in the ops reporting tool.\n"
            "3. Ensure that 95% of RMT merchants with monthly GMV above $50,000 USD "
            "receive a first-touch moderation decision within 24 hours of submission, "
            "starting from the Q3 2026 rollout date.\n"
            "4. Maintain the existing 5-business-day SLA compliance rate of ≥98% for "
            "CMT and COI merchants throughout the transition period."
        ),
        "expected_score_min": 8.5,
        "expected_score_max": 10.0,
        "expected_verdict": "PASS",
        "expected_triage": "NORMAL",
        "notes": "SMART goals — each measurable, distinct, time-bound. Should consistently PASS.",
    },
    # ─────────────────────────────────────────────────────────────────────────
    # Goals — very poor
    # ─────────────────────────────────────────────────────────────────────────
    {
        "case_id": "goals_very_poor_01",
        "section_id": "goals",
        "label": "very_poor",
        "prior_sections": "",
        "draft_text": (
            "1. Improve the moderation process for merchants.\n"
            "2. Make the system smarter and more efficient.\n"
            "3. Reduce errors where possible.\n"
            "4. Improve merchant satisfaction with the review process.\n"
            "5. Support the business in growing faster."
        ),
        "expected_score_min": 0.0,
        "expected_score_max": 4.9,
        "expected_verdict": "REWORK",
        "expected_triage": "RECOVERY",
        "notes": (
            "No targets, no timeline, no measurement method, multiple vague "
            "qualifiers. Should trigger ENTER RECOVERY MODE."
        ),
    },
    # ─────────────────────────────────────────────────────────────────────────
    # Success metrics — strong
    # ─────────────────────────────────────────────────────────────────────────
    {
        "case_id": "metrics_strong_01",
        "section_id": "success_metrics",
        "label": "strong",
        "prior_sections": "",
        "draft_text": (
            "1. RMT False Positive Rate\n"
            "   - Baseline: 17% (measured via INCA ops dashboard, April 2026)\n"
            "   - Target: ≤8%\n"
            "   - Measurement method: Weekly automated export from INCA moderation "
            "dashboard, defined as: (incorrectly blocked RMT submissions / total "
            "RMT submissions) × 100\n"
            "   - Evaluation timeline: First measurement at 30 days post-launch; "
            "target must be sustained for 2 consecutive months by Q4 2026.\n\n"
            "2. Merchant Review Queue Age (p90)\n"
            "   - Baseline: 6 weeks (measured from ops reporting, March 2026)\n"
            "   - Target: ≤2 weeks\n"
            "   - Measurement method: p90 of queue age field in the ops reporting "
            "tool, extracted weekly\n"
            "   - Evaluation timeline: Target achieved and sustained for 4 "
            "consecutive weeks by Q3 2026.\n\n"
            "3. RMT Fast-Track SLA Compliance\n"
            "   - Baseline: N/A (new SLA)\n"
            "   - Target: ≥95% of eligible RMT merchants (GMV > $50,000/month) "
            "receive first-touch decision within 24 hours\n"
            "   - Measurement method: Automated SLA tracking in INCA, logged per "
            "submission timestamp\n"
            "   - Evaluation timeline: Measured weekly from launch; must reach "
            "target within 60 days of Q3 2026 launch."
        ),
        "expected_score_min": 8.5,
        "expected_score_max": 10.0,
        "expected_verdict": "PASS",
        "expected_triage": "NORMAL",
        "notes": (
            "All 5 expected components present per metric: name, baseline, target, "
            "method, timeline. Should consistently PASS."
        ),
    },
    # ─────────────────────────────────────────────────────────────────────────
    # Success metrics — medium (missing baselines)
    # ─────────────────────────────────────────────────────────────────────────
    {
        "case_id": "metrics_medium_01",
        "section_id": "success_metrics",
        "label": "medium",
        "prior_sections": "",
        "draft_text": (
            "1. RMT False Positive Rate\n"
            "   - Target: ≤8%\n"
            "   - Measurement method: Weekly export from INCA moderation dashboard\n"
            "   - Evaluation timeline: Q4 2026\n\n"
            "2. Merchant Review Queue Age\n"
            "   - Target: ≤2 weeks (p90)\n"
            "   - Measurement method: Ops reporting tool, weekly extraction\n"
            "   - Evaluation timeline: Q3 2026\n\n"
            "3. RMT Fast-Track SLA Compliance\n"
            "   - Target: ≥95% of eligible merchants\n"
            "   - Measurement method: Automated SLA tracking in INCA\n"
            "   - Evaluation timeline: 60 days post-launch"
        ),
        "expected_score_min": 6.0,
        "expected_score_max": 8.4,
        "expected_verdict": "REWORK",
        "expected_triage": "NORMAL",
        "notes": (
            "Targets and methods present but all baselines missing. "
            "Medium tier — gaps present but recoverable, not recovery mode."
        ),
    },
    # ─────────────────────────────────────────────────────────────────────────
    # Success metrics — very poor
    # ─────────────────────────────────────────────────────────────────────────
    {
        "case_id": "metrics_very_poor_01",
        "section_id": "success_metrics",
        "label": "very_poor",
        "prior_sections": "",
        "draft_text": (
            "Success will be measured by improvements in the moderation workflow. "
            "We will track how well the system performs over time and gather feedback "
            "from merchants. If the process is working better, we will consider it a "
            "success. Operations teams will monitor the dashboard and escalate if "
            "there are problems. Merchant satisfaction surveys will also be reviewed "
            "on a quarterly basis to assess the overall impact."
        ),
        "expected_score_min": 0.0,
        "expected_score_max": 4.9,
        "expected_verdict": "REWORK",
        "expected_triage": "RECOVERY",
        "notes": (
            "No numeric targets, no baselines, no measurement method. "
            "Core components entirely absent — should trigger ENTER RECOVERY MODE."
        ),
    },
    # ─────────────────────────────────────────────────────────────────────────
    # Risks — strong
    # ─────────────────────────────────────────────────────────────────────────
    {
        "case_id": "risks_strong_01",
        "section_id": "risks",
        "label": "strong",
        "prior_sections": "",
        "draft_text": (
            "1. GMV threshold miscalibration\n"
            "   - Description: The $50,000/month GMV threshold for RMT fast-track "
            "eligibility may exclude merchants who should qualify or include those "
            "who should not, skewing false positive rates.\n"
            "   - Likelihood: Medium\n"
            "   - Impact: High — incorrect threshold affects SLA compliance and "
            "false positive metrics directly\n"
            "   - Mitigation: Run a retrospective calibration analysis on 90 days "
            "of historical RMT data before launch (owner: Data Science, due: 2 "
            "weeks before Q3 launch). Threshold to be validated and signed off by "
            "Trust & Safety Lead.\n\n"
            "2. Ops team capacity constraint\n"
            "   - Description: Reducing the 24-hour SLA for RMT fast-track requires "
            "sufficient ops staffing; if headcount is not adjusted, SLA will be "
            "breached.\n"
            "   - Likelihood: High\n"
            "   - Impact: High — SLA breach directly violates the primary success "
            "metric (F05-3)\n"
            "   - Mitigation: Ops capacity plan (including headcount delta) must be "
            "approved before Q2 2026 planning close. If headcount is not approved, "
            "the 24-hour SLA will be revised to 48 hours before launch.\n\n"
            "3. INCA system latency under increased rule complexity\n"
            "   - Description: Risk-differentiated routing rules may increase INCA "
            "decision latency, degrading the SLA even if ops capacity is sufficient.\n"
            "   - Likelihood: Low\n"
            "   - Impact: Medium\n"
            "   - Mitigation: Load test the updated routing logic against 2× current "
            "peak traffic in staging before Q3 launch. If p95 latency exceeds 500ms, "
            "the routing logic must be refactored before release."
        ),
        "expected_score_min": 8.5,
        "expected_score_max": 10.0,
        "expected_verdict": "PASS",
        "expected_triage": "NORMAL",
        "notes": (
            "All 4 components present per risk: description, likelihood, impact, "
            "mitigation with owner and deadline. Should consistently PASS."
        ),
    },
    # ─────────────────────────────────────────────────────────────────────────
    # Risks — poor (mitigations are 'monitor' or absent)
    # ─────────────────────────────────────────────────────────────────────────
    {
        "case_id": "risks_poor_01",
        "section_id": "risks",
        "label": "poor",
        "prior_sections": "",
        "draft_text": (
            "1. GMV threshold miscalibration\n"
            "   - Likelihood: Medium\n"
            "   - Impact: High\n"
            "   - Mitigation: Monitor post-launch and adjust if needed.\n\n"
            "2. Ops team capacity constraint\n"
            "   - Likelihood: High\n"
            "   - Impact: High\n"
            "   - Mitigation: TBD — to be discussed with ops leadership.\n\n"
            "3. INCA system latency\n"
            "   - Likelihood: Low\n"
            "   - Impact: Medium\n"
            "   - Mitigation: We will keep an eye on performance metrics."
        ),
        "expected_score_min": 5.0,
        "expected_score_max": 6.9,
        "expected_verdict": "REWORK",
        "expected_triage": "NORMAL",
        "notes": (
            "Risks identified with likelihood/impact but all mitigations are vague "
            "('monitor', 'TBD'). Poor tier, not recovery mode."
        ),
    },
    # ─────────────────────────────────────────────────────────────────────────
    # Non-goals — medium (missing exclusion reasons)
    # ─────────────────────────────────────────────────────────────────────────
    {
        "case_id": "non_goals_medium_01",
        "section_id": "non_goals",
        "label": "medium",
        "prior_sections": "",
        "draft_text": (
            "The following are explicitly out of scope for this initiative:\n\n"
            "1. Manual review workflow redesign — the interface and tooling used by "
            "human reviewers will not be changed.\n"
            "2. Moderation rule changes for COI merchants.\n"
            "3. Real-time (sub-second) moderation decisions.\n"
            "4. Integration with third-party fraud signal providers.\n"
            "5. Mobile or app-based merchant onboarding flows."
        ),
        "expected_score_min": 6.0,
        "expected_score_max": 8.4,
        "expected_verdict": "REWORK",
        "expected_triage": "NORMAL",
        "notes": (
            "Non-goals are specific enough but reasons for exclusion are absent. "
            "Medium tier — gaps present but not recovery-level."
        ),
    },
]

# ── Convenience lookup ─────────────────────────────────────────────────────────

CASES_BY_ID: dict[str, dict] = {c["case_id"]: c for c in CASES}


def get_case(case_id: str) -> dict:
    """Return a case dict by its case_id. Raises KeyError if not found."""
    return CASES_BY_ID[case_id]
