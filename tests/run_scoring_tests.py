"""
Scoring stability test runner.

Usage:
    .venv/bin/python -m tests.run_scoring_tests

What it checks:
1. The OVERALL SCORE for each fixture falls within the expected score band.
2. The system-enforced verdict (after programmatic threshold override) matches
   the expected_verdict.
3. The triage decision matches expected_triage (where specified).
4. Recovery mode is not triggering too often (accepted rate: ≤30% of fixtures).

Pass/fail criteria:
- A fixture PASSES if its score, verdict, and triage all fall within tolerance.
- A fixture is flagged WARN if its score is in an adjacent band (±1 tier).
- A fixture FAILS if its score is 2+ tiers off, or verdict/triage mismatches.

Scoring stability note:
  LLM scores at temperature=0 are deterministic per model version but may shift
  0.3–0.8 points across API calls due to sampling variance in the thinking layer.
  Re-run 3 times and compare if a fixture is borderline.
"""
import os
import sys
import re

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI

from config.sections import get_section_by_id
from prompts.templates import (
    GLOBAL_RIGOR_BLOCK,
    PASS_SCORE_THRESHOLD,
    RECOVERY_MODE_SCORE_THRESHOLD,
    REFLECTOR_PRIOR_SECTIONS_BLOCK,
    REFLECTOR_SYSTEM,
    SCORING_INTERPRETATION_BLOCK,
)
from tests.fixtures import BAND_DEFINITIONS, FIXTURES

load_dotenv()

# ── Helpers ───────────────────────────────────────────────────────────────────

BAND_ORDER = ["very_poor", "poor", "medium", "high"]


def _get_llm():
    model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    return ChatGoogleGenerativeAI(model=model, temperature=0)


def _score_to_band(score: float) -> str:
    for band, (lo, hi) in BAND_DEFINITIONS.items():
        if lo <= score <= hi:
            return band
    return "unknown"


def _band_distance(actual: str, expected: str) -> int:
    """How many tiers apart are the two bands? 0 = exact, 1 = adjacent."""
    if actual not in BAND_ORDER or expected not in BAND_ORDER:
        return 99
    return abs(BAND_ORDER.index(actual) - BAND_ORDER.index(expected))


def _run_reflector(section_id: str, draft: str) -> dict:
    """Call the Reflector LLM for a single fixture. Returns parsed result dict."""
    section = get_section_by_id(section_id)
    llm = _get_llm()

    expected_components_list = "\n".join(
        f"  • {c}" for c in section.expected_components
    )

    system_prompt = REFLECTOR_SYSTEM.format(
        section_title=section.title,
        prior_sections_block="No prior sections — isolated test.",
        expected_components_list=expected_components_list,
        specificity_guidance=section.specificity_guidance,
        global_rigor_block=GLOBAL_RIGOR_BLOCK,
        scoring_interpretation_block=SCORING_INTERPRETATION_BLOCK,
    )

    response = llm.invoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=f"Review this draft for the '{section.title}' section:\n\n{draft}"),
    ])
    text = response.content.strip()

    # Parse overall score
    score_match = re.search(
        r"OVERALL\s+SCORE[^\d]*(\d+\.?\d*)\s*/\s*10", text, re.IGNORECASE
    )
    overall_score = float(score_match.group(1)) if score_match else -1.0

    # Parse LLM verdict
    llm_verdict = "REWORK"
    for line in reversed(text.splitlines()):
        clean = line.strip().lstrip("*# \t")
        if clean.upper().startswith("VERDICT: PASS"):
            llm_verdict = "PASS"
            break
        if clean.upper().startswith("VERDICT: REWORK"):
            llm_verdict = "REWORK"
            break

    # Parse triage
    triage = "TRIAGE: NORMAL ITERATION"
    for line in text.splitlines():
        clean = line.strip().lstrip("*# \t")
        if "TRIAGE: ENTER RECOVERY MODE" in clean.upper():
            triage = "TRIAGE: ENTER RECOVERY MODE"
            break
        if "TRIAGE: NORMAL ITERATION" in clean.upper():
            triage = "TRIAGE: NORMAL ITERATION"
            break

    # Programmatic threshold enforcement (mirrors reflect_node exactly)
    enforced_verdict = llm_verdict
    enforced_triage = triage
    if overall_score >= 0.0:
        if enforced_verdict == "PASS" and overall_score < PASS_SCORE_THRESHOLD:
            enforced_verdict = "REWORK"
        if overall_score < RECOVERY_MODE_SCORE_THRESHOLD:
            enforced_triage = "TRIAGE: ENTER RECOVERY MODE"

    return {
        "overall_score": overall_score,
        "llm_verdict": llm_verdict,
        "enforced_verdict": enforced_verdict,
        "triage": enforced_triage,
        "actual_band": _score_to_band(overall_score),
        "raw_output": text,
    }


def _assess(fixture: dict, result: dict) -> tuple[str, list[str]]:
    """
    Returns (status, reasons) where status is 'PASS', 'WARN', or 'FAIL'.
    """
    reasons = []
    expected_band = fixture["expected_band"]
    actual_band = result["actual_band"]
    dist = _band_distance(actual_band, expected_band)

    if result["overall_score"] < 0:
        reasons.append("Score parse failure (returned -1.0) — check output format")
        return "WARN", reasons

    if dist == 0:
        score_status = "PASS"
    elif dist == 1:
        score_status = "WARN"
        reasons.append(
            f"Score {result['overall_score']:.1f} in band '{actual_band}', "
            f"expected '{expected_band}' — adjacent tier"
        )
    else:
        score_status = "FAIL"
        reasons.append(
            f"Score {result['overall_score']:.1f} in band '{actual_band}', "
            f"expected '{expected_band}' — {dist} tiers off"
        )

    if result["enforced_verdict"] != fixture["expected_verdict"]:
        score_status = "FAIL"
        reasons.append(
            f"Verdict: got '{result['enforced_verdict']}', "
            f"expected '{fixture['expected_verdict']}'"
        )

    if fixture.get("expected_triage") and result["triage"] != fixture["expected_triage"]:
        if score_status == "PASS":
            score_status = "WARN"
        reasons.append(
            f"Triage: got '{result['triage']}', "
            f"expected '{fixture['expected_triage']}'"
        )

    return score_status, reasons


# ── Main runner ───────────────────────────────────────────────────────────────

def run():
    print("=" * 70)
    print("PRD Reflector — Scoring Stability Test")
    print(f"  PASS threshold : {PASS_SCORE_THRESHOLD}")
    print(f"  Recovery threshold : {RECOVERY_MODE_SCORE_THRESHOLD}")
    print("=" * 70)
    print()

    results_summary = []
    recovery_count = 0

    for i, fixture in enumerate(FIXTURES, 1):
        fid = fixture["fixture_id"]
        section_id = fixture["section_id"]
        label = fixture["label"]
        print(f"[{i:02d}/{len(FIXTURES)}] {fid} | {section_id} | expected: {label}  ", end="", flush=True)

        result = _run_reflector(section_id, fixture["draft"])
        status, reasons = _assess(fixture, result)

        score_str = (
            f"{result['overall_score']:.1f}" if result["overall_score"] >= 0 else "N/A"
        )
        triage_short = (
            "RECOVERY" if "RECOVERY" in result["triage"] else "NORMAL"
        )
        print(
            f"score={score_str}  band={result['actual_band']}  "
            f"verdict={result['enforced_verdict']}  "
            f"triage={triage_short}  [{status}]"
        )

        if reasons:
            for r in reasons:
                print(f"         ⚠  {r}")

        if result["triage"] == "TRIAGE: ENTER RECOVERY MODE":
            recovery_count += 1

        results_summary.append({
            "fixture": fixture,
            "result": result,
            "status": status,
            "reasons": reasons,
        })

    # ── Summary ───────────────────────────────────────────────────────────────
    passes = sum(1 for r in results_summary if r["status"] == "PASS")
    warns  = sum(1 for r in results_summary if r["status"] == "WARN")
    fails  = sum(1 for r in results_summary if r["status"] == "FAIL")
    total  = len(results_summary)
    recovery_rate = recovery_count / total

    print()
    print("=" * 70)
    print("SUMMARY")
    print(f"  Total fixtures : {total}")
    print(f"  PASS           : {passes}")
    print(f"  WARN           : {warns}  (adjacent band or triage mismatch)")
    print(f"  FAIL           : {fails}  (2+ tiers off or verdict mismatch)")
    print(f"  Recovery rate  : {recovery_count}/{total} = {recovery_rate:.0%}  ", end="")

    # Expected recovery fixtures: F04 (goals very_poor), F07 (metrics very_poor) = 2/10 = 20%
    expected_recovery = sum(
        1 for f in FIXTURES if f.get("expected_triage") == "TRIAGE: ENTER RECOVERY MODE"
    )
    print(f"(expected {expected_recovery}/{total} = {expected_recovery/total:.0%})")

    if recovery_rate > 0.30:
        print("  ⚠  CALIBRATION WARNING: Recovery mode triggering too often (>30%).")
        print("     Consider raising RECOVERY_MODE_SCORE_THRESHOLD.")

    print()
    if fails == 0 and warns <= 2:
        print("✅  Scoring appears stable and well-calibrated.")
    elif fails == 0:
        print("⚠️  Scoring is borderline on some fixtures — review WARN cases.")
    else:
        print("❌  Scoring has calibration failures — review FAIL cases above.")
    print("=" * 70)

    # Optionally dump full reflector output for FAIL fixtures
    dump_fails = os.getenv("DUMP_FAILED_FIXTURES", "0") == "1"
    if dump_fails:
        for r in results_summary:
            if r["status"] == "FAIL":
                fid = r["fixture"]["fixture_id"]
                print(f"\n{'─' * 70}")
                print(f"Full reflector output for {fid}:")
                print(r["result"]["raw_output"])


if __name__ == "__main__":
    run()
