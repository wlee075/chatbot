"""
Scoring stability test fixtures.

Each fixture is a dict with:
  section_id      : matches config/sections.py PRDSection.id
  fixture_id      : unique label for reporting
  label           : human-readable quality tier
  draft           : the section text submitted to the Reflector
  expected_band   : one of 'high' / 'medium' / 'poor' / 'very_poor'
  expected_verdict: 'PASS' | 'REWORK'
  expected_triage : 'TRIAGE: NORMAL ITERATION' | 'TRIAGE: ENTER RECOVERY MODE' | None
  notes           : what this fixture is probing
"""

FIXTURES = [
    # ─────────────────────────────────────────────────────────────────────────
    # tl;dr — F01: High quality
    # ─────────────────────────────────────────────────────────────────────────
    {
        "fixture_id": "F01",
        "section_id": "tldr",
        "label": "high",
        "expected_band": "high",
        "expected_verdict": "PASS",
        "expected_triage": "TRIAGE: NORMAL ITERATION",
        "notes": "Complete, specific, no vague language. Should consistently PASS.",
        "draft": (
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
    },
    # ─────────────────────────────────────────────────────────────────────────
    # tl;dr — F02: Poor quality
    # ─────────────────────────────────────────────────────────────────────────
    {
        "fixture_id": "F02",
        "section_id": "tldr",
        "label": "poor",
        "expected_band": "poor",
        "expected_verdict": "REWORK",
        "expected_triage": "TRIAGE: NORMAL ITERATION",
        "notes": "Vague language, no metrics, solution mentioned but undefined. Should REWORK, not recovery.",
        "draft": (
            "The current content moderation process has some issues that affect "
            "merchant experience. We want to improve the process to make it better "
            "and faster for merchants while reducing errors. The new system will use "
            "smarter logic to differentiate between merchant types and apply "
            "appropriate review workflows. This will improve outcomes for the "
            "business and for merchants going forward."
        ),
    },
    # ─────────────────────────────────────────────────────────────────────────
    # Goals — F03: High quality
    # ─────────────────────────────────────────────────────────────────────────
    {
        "fixture_id": "F03",
        "section_id": "goals",
        "label": "high",
        "expected_band": "high",
        "expected_verdict": "PASS",
        "expected_triage": "TRIAGE: NORMAL ITERATION",
        "notes": "SMART goals, each measurable, distinct, time-bound.",
        "draft": (
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
    },
    # ─────────────────────────────────────────────────────────────────────────
    # Goals — F04: Very poor quality
    # ─────────────────────────────────────────────────────────────────────────
    {
        "fixture_id": "F04",
        "section_id": "goals",
        "label": "very_poor",
        "expected_band": "very_poor",
        "expected_verdict": "REWORK",
        "expected_triage": "TRIAGE: ENTER RECOVERY MODE",
        "notes": "No targets, no timeline, no measurement, multiple vague qualifiers. Should trigger recovery mode.",
        "draft": (
            "1. Improve the moderation process for merchants.\n"
            "2. Make the system smarter and more efficient.\n"
            "3. Reduce errors where possible.\n"
            "4. Improve merchant satisfaction with the review process.\n"
            "5. Support the business in growing faster."
        ),
    },
    # ─────────────────────────────────────────────────────────────────────────
    # Success Metrics — F05: High quality
    # ─────────────────────────────────────────────────────────────────────────
    {
        "fixture_id": "F05",
        "section_id": "success_metrics",
        "label": "high",
        "expected_band": "high",
        "expected_verdict": "PASS",
        "expected_triage": "TRIAGE: NORMAL ITERATION",
        "notes": "All 5 expected components present per metric: name, baseline, target, method, timeline.",
        "draft": (
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
    },
    # ─────────────────────────────────────────────────────────────────────────
    # Success Metrics — F06: Medium quality (missing baselines)
    # ─────────────────────────────────────────────────────────────────────────
    {
        "fixture_id": "F06",
        "section_id": "success_metrics",
        "label": "medium",
        "expected_band": "medium",
        "expected_verdict": "REWORK",
        "expected_triage": "TRIAGE: NORMAL ITERATION",
        "notes": "Targets and methods present but baselines missing. Medium band, not recovery.",
        "draft": (
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
    },
    # ─────────────────────────────────────────────────────────────────────────
    # Success Metrics — F07: Very poor quality
    # ─────────────────────────────────────────────────────────────────────────
    {
        "fixture_id": "F07",
        "section_id": "success_metrics",
        "label": "very_poor",
        "expected_band": "very_poor",
        "expected_verdict": "REWORK",
        "expected_triage": "TRIAGE: ENTER RECOVERY MODE",
        "notes": "No numeric targets, no baselines, no measurement methods. Core components missing.",
        "draft": (
            "Success will be measured by improvements in the moderation workflow. "
            "We will track how well the system performs over time and gather feedback "
            "from merchants. If the process is working better, we will consider it a "
            "success. Operations teams will monitor the dashboard and escalate if "
            "there are problems. Merchant satisfaction surveys will also be reviewed "
            "on a quarterly basis to assess the overall impact."
        ),
    },
    # ─────────────────────────────────────────────────────────────────────────
    # Risks — F08: High quality
    # ─────────────────────────────────────────────────────────────────────────
    {
        "fixture_id": "F08",
        "section_id": "risks",
        "label": "high",
        "expected_band": "high",
        "expected_verdict": "PASS",
        "expected_triage": "TRIAGE: NORMAL ITERATION",
        "notes": "All 4 components present for each risk: description, likelihood, impact, mitigation.",
        "draft": (
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
    },
    # ─────────────────────────────────────────────────────────────────────────
    # Risks — F09: Poor quality (no mitigations)
    # ─────────────────────────────────────────────────────────────────────────
    {
        "fixture_id": "F09",
        "section_id": "risks",
        "label": "poor",
        "expected_band": "poor",
        "expected_verdict": "REWORK",
        "expected_triage": "TRIAGE: NORMAL ITERATION",
        "notes": "Risks identified with likelihood/impact but all mitigations are 'monitor' or absent.",
        "draft": (
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
    },
    # ─────────────────────────────────────────────────────────────────────────
    # Non-goals — F10: Medium quality (missing exclusion reasons)
    # ─────────────────────────────────────────────────────────────────────────
    {
        "fixture_id": "F10",
        "section_id": "non_goals",
        "label": "medium",
        "expected_band": "medium",
        "expected_verdict": "REWORK",
        "expected_triage": "TRIAGE: NORMAL ITERATION",
        "notes": "Non-goals are specific enough but reasons for exclusion are absent.",
        "draft": (
            "The following are explicitly out of scope for this initiative:\n\n"
            "1. Manual review workflow redesign — the interface and tooling used by "
            "human reviewers will not be changed.\n"
            "2. Moderation rule changes for COI merchants.\n"
            "3. Real-time (sub-second) moderation decisions.\n"
            "4. Integration with third-party fraud signal providers.\n"
            "5. Mobile or app-based merchant onboarding flows."
        ),
    },
]

# ── Score band definitions (must match thresholds in templates.py) ────────────

BAND_DEFINITIONS = {
    "high":      (8.5, 10.0),   # PASS expected
    "medium":    (6.0,  8.4),   # REWORK, NORMAL ITERATION
    "poor":      (5.0,  6.9),   # REWORK, NORMAL ITERATION
    "very_poor": (0.0,  4.9),   # REWORK, ENTER RECOVERY MODE
}
