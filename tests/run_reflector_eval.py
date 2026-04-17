"""
PRD Reflector evaluation runner.

Invokes the Reflector LLM directly against cases from eval_cases.py,
logs raw results to a CSV, and writes a per-invocation summary CSV.

Usage
─────
  # Single run per case (default)
  .venv/bin/python -m tests.run_reflector_eval

  # Three runs per case for consistency measurement
  .venv/bin/python -m tests.run_reflector_eval --runs 3

  # Filter to specific case IDs
  .venv/bin/python -m tests.run_reflector_eval --cases headliner_strong_01 goals_very_poor_01

  # Override default output paths
  .venv/bin/python -m tests.run_reflector_eval \\
      --runs-csv path/to/runs.csv \\
      --summary-csv path/to/summary.csv

Output files
────────────
  reflector_eval_runs.csv    — raw log, appended on every invocation
  reflector_eval_summary.csv — summary for this run set (overwritten each invocation)
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import statistics
import sys
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI

# ── Path setup ─────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.sections import get_section_by_id
from prompts.templates import (
    GLOBAL_RIGOR_BLOCK,
    REFLECTOR_SYSTEM,
    SCORING_INTERPRETATION_BLOCK,
)
from tests.eval_cases import CASES

# ── Defaults ───────────────────────────────────────────────────────────────────
DEFAULT_RUNS_CSV = PROJECT_ROOT / "reflector_eval_runs.csv"
DEFAULT_SUMMARY_CSV = PROJECT_ROOT / "reflector_eval_summary.csv"

_TRIAGE_NORMAL = "TRIAGE: NORMAL ITERATION"
_TRIAGE_RECOVERY = "TRIAGE: ENTER RECOVERY MODE"

# Full strings that eval_cases.py shorthand maps to
TRIAGE_EXPAND = {
    "NORMAL": _TRIAGE_NORMAL,
    "RECOVERY": _TRIAGE_RECOVERY,
}

# ── CSV column headers ──────────────────────────────────────────────────────────

RUNS_FIELDNAMES = [
    "timestamp",
    "run_set_id",
    "case_id",
    "label",
    "section_id",
    "run_index",
    "model_version",
    "overall_score",
    "completeness_score",
    "specificity_score",
    "internal_consistency_score",
    "implementability_score",
    "llm_verdict",
    "enforced_verdict",
    "triage_decision",
    "resolved_count",
    "unresolved_count",
    "parse_error",
    "raw_output",
]

SUMMARY_FIELDNAMES = [
    "run_set_id",
    "case_id",
    "label",
    "section_id",
    "expected_score_min",
    "expected_score_max",
    "expected_verdict",
    "expected_triage",
    "runs",
    "score_mean",
    "score_std",
    "score_min",
    "score_max",
    "completeness_mean",
    "specificity_mean",
    "internal_consistency_mean",
    "implementability_mean",
    "verdict_mode",
    "verdict_consistency",
    "triage_mode",
    "triage_consistency",
    "recovery_trigger_rate",
    "resolved_mean",
    "unresolved_mean",
    "ci_status",
]

# ── Rubric parser ──────────────────────────────────────────────────────────────

# Matches "RUBRIC NAME — X.X/10" with any dash variant and optional markdown bold.
_RUBRIC_PATTERNS: dict[str, str] = {
    "completeness_score":         r"COMPLETENESS\s*[—–\-]+\s*(\d+\.?\d*)\s*/\s*10",
    "specificity_score":          r"SPECIFICITY\s*[—–\-]+\s*(\d+\.?\d*)\s*/\s*10",
    "internal_consistency_score": r"INTERNAL\s+CONSISTENCY\s*[—–\-]+\s*(\d+\.?\d*)\s*/\s*10",
    "implementability_score":     r"IMPLEMENTABILITY\s*[—–\-]+\s*(\d+\.?\d*)\s*/\s*10",
    "overall_score":              r"OVERALL\s+SCORE\s*[—–\-]+\s*(\d+\.?\d*)\s*/\s*10",
}


def _parse_reflector_output(text: str) -> dict:
    """
    Extract structured fields from a Reflector LLM response.

    Returns a dict with keys matching RUNS_FIELDNAMES (minus the header fields
    like timestamp, run_set_id, etc.):
      overall_score, completeness_score, specificity_score,
      internal_consistency_score, implementability_score,
      llm_verdict, triage_decision, resolved_count, unresolved_count, parse_error
    """
    result: dict = {k: -1.0 for k in _RUBRIC_PATTERNS}
    parse_error = False

    # Per-rubric scores
    for field, pattern in _RUBRIC_PATTERNS.items():
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            result[field] = float(m.group(1))
        else:
            parse_error = True

    # LLM verdict — scan lines in reverse; first match wins
    llm_verdict = "REWORK"
    for line in reversed(text.splitlines()):
        clean = line.strip().lstrip("*# \t")
        if clean.upper().startswith("VERDICT: PASS"):
            llm_verdict = "PASS"
            break
        if clean.upper().startswith("VERDICT: REWORK"):
            llm_verdict = "REWORK"
            break

    # Triage — scan forward; first match wins; default NORMAL on parse failure
    triage_decision = _TRIAGE_NORMAL
    for line in text.splitlines():
        clean = line.strip().lstrip("*# \t")
        if "TRIAGE: ENTER RECOVERY MODE" in clean.upper():
            triage_decision = _TRIAGE_RECOVERY
            break
        if "TRIAGE: NORMAL ITERATION" in clean.upper():
            triage_decision = _TRIAGE_NORMAL
            break

    # Resolved / unresolved requirement counts (section 6)
    resolved_count = len(re.findall(r"[-•*]\s*RESOLVED:", text, re.IGNORECASE))
    unresolved_count = len(re.findall(r"[-•*]\s*UNRESOLVED:", text, re.IGNORECASE))

    result["llm_verdict"] = llm_verdict
    result["triage_decision"] = triage_decision
    result["resolved_count"] = resolved_count
    result["unresolved_count"] = unresolved_count
    result["parse_error"] = parse_error

    return result


# ── LLM invocation ─────────────────────────────────────────────────────────────

def _build_llm(model_version: str) -> ChatGoogleGenerativeAI:
    load_dotenv(PROJECT_ROOT / ".env")
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise EnvironmentError("GOOGLE_API_KEY not set — add it to .env or the environment.")
    return ChatGoogleGenerativeAI(
        model=model_version,
        google_api_key=api_key,
        temperature=0.0,
    )


def _run_case_once(
    case: dict,
    run_index: int,
    run_set_id: str,
    model_version: str,
    llm: ChatGoogleGenerativeAI,
) -> dict:
    """Invoke the Reflector for one case and return a raw-log row dict."""
    section = get_section_by_id(case["section_id"])

    expected_components_list = "\n".join(
        f"  • {c}" for c in section.expected_components
    )

    # Prior sections block — use case["prior_sections"] if provided
    prior_sections_block = (
        f"Prior PRD sections (check for consistency):\n---\n{case['prior_sections']}"
        if case.get("prior_sections")
        else "No prior sections yet."
    )

    system_prompt = REFLECTOR_SYSTEM.format(
        section_title=section.title,
        prior_sections_block=prior_sections_block,
        expected_components_list=expected_components_list,
        specificity_guidance=section.specificity_guidance,
        global_rigor_block=GLOBAL_RIGOR_BLOCK,
        scoring_interpretation_block=SCORING_INTERPRETATION_BLOCK,
    )

    response = llm.invoke(
        [
            SystemMessage(content=system_prompt),
            HumanMessage(
                content=(
                    f"Review this draft for the '{section.title}' section:\n\n"
                    f"{case['draft_text']}"
                )
            ),
        ]
    )
    raw_output = response.content.strip()

    parsed = _parse_reflector_output(raw_output)

    # Apply the same programmatic threshold enforcement as reflect_node
    from prompts.templates import PASS_SCORE_THRESHOLD, RECOVERY_MODE_SCORE_THRESHOLD

    overall_score = parsed["overall_score"]
    llm_verdict = parsed["llm_verdict"]
    triage_decision = parsed["triage_decision"]

    enforced_verdict = llm_verdict
    if overall_score >= 0.0:
        if llm_verdict == "PASS" and overall_score < PASS_SCORE_THRESHOLD:
            enforced_verdict = "REWORK"
        if overall_score < RECOVERY_MODE_SCORE_THRESHOLD:
            triage_decision = _TRIAGE_RECOVERY

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "run_set_id": run_set_id,
        "case_id": case["case_id"],
        "label": case["label"],
        "section_id": case["section_id"],
        "run_index": run_index,
        "model_version": model_version,
        "overall_score": overall_score,
        "completeness_score": parsed["completeness_score"],
        "specificity_score": parsed["specificity_score"],
        "internal_consistency_score": parsed["internal_consistency_score"],
        "implementability_score": parsed["implementability_score"],
        "llm_verdict": llm_verdict,
        "enforced_verdict": enforced_verdict,
        "triage_decision": triage_decision,
        "resolved_count": parsed["resolved_count"],
        "unresolved_count": parsed["unresolved_count"],
        "parse_error": parsed["parse_error"],
        "raw_output": raw_output,
    }


# ── CSV I/O ─────────────────────────────────────────────────────────────────────

def _append_runs_csv(rows: list[dict], path: Path) -> None:
    """Append rows to the runs CSV, writing the header only if the file is new."""
    write_header = not path.exists() or path.stat().st_size == 0
    with open(path, "a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=RUNS_FIELDNAMES, extrasaction="ignore")
        if write_header:
            writer.writeheader()
        writer.writerows(rows)


def _write_summary_csv(summary_rows: list[dict], path: Path) -> None:
    """Overwrite the summary CSV with the current run set's aggregated results."""
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=SUMMARY_FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(summary_rows)


# ── Summary aggregation ─────────────────────────────────────────────────────────

def _safe_mean(values: list[float]) -> float:
    valid = [v for v in values if v >= 0]
    return round(statistics.mean(valid), 3) if valid else -1.0


def _safe_stdev(values: list[float]) -> float:
    valid = [v for v in values if v >= 0]
    return round(statistics.stdev(valid), 3) if len(valid) >= 2 else 0.0


def _triage_shorthand(full_string: str) -> str:
    return "RECOVERY" if "RECOVERY MODE" in full_string.upper() else "NORMAL"


def _ci_status_for_row(row: dict) -> str:
    """
    Compute CI status for a single summary row.

    FAIL  — score_mean is >1.0 outside expected range, OR verdict_mode != expected_verdict
    WARN  — score_mean is 0–1.0 outside expected range, OR triage_mode != expected_triage,
            OR parse_errors detected (score_mean == -1.0)
    PASS  — all checks pass
    """
    score_mean = float(row["score_mean"])
    exp_min = float(row["expected_score_min"])
    exp_max = float(row["expected_score_max"])

    if score_mean < 0:
        # -1.0 sentinel means parse failure — cannot assert anything
        return "WARN"

    # Score range check with tolerance bands
    if score_mean < exp_min - 1.0 or score_mean > exp_max + 1.0:
        return "FAIL"
    if score_mean < exp_min - 0.0 or score_mean > exp_max + 0.0:
        # Inside the 1-point tolerance buffer but outside strict range
        return "WARN"

    # Verdict match — the primary correctness gate
    if row["verdict_mode"] != row["expected_verdict"]:
        return "FAIL"

    # Triage match — softer; wrong triage = WARN not FAIL
    if _triage_shorthand(row["triage_mode"]) != row["expected_triage"]:
        return "WARN"

    return "PASS"


def _compute_summary(
    rows: list[dict],
    cases: list[dict],
    run_set_id: str,
) -> list[dict]:
    """Aggregate per-run rows into one summary row per case."""
    # Group runs by case_id
    by_case: dict[str, list[dict]] = {}
    for row in rows:
        by_case.setdefault(row["case_id"], []).append(row)

    case_lookup = {c["case_id"]: c for c in cases}
    summary_rows = []

    for case_id, case_rows in by_case.items():
        case = case_lookup[case_id]

        scores = [float(r["overall_score"]) for r in case_rows]
        completeness = [float(r["completeness_score"]) for r in case_rows]
        specificity = [float(r["specificity_score"]) for r in case_rows]
        i_consistency = [float(r["internal_consistency_score"]) for r in case_rows]
        implementability = [float(r["implementability_score"]) for r in case_rows]
        resolved = [int(r["resolved_count"]) for r in case_rows]
        unresolved = [int(r["unresolved_count"]) for r in case_rows]

        verdicts = [r["enforced_verdict"] for r in case_rows]
        triages = [r["triage_decision"] for r in case_rows]

        verdict_counter = Counter(verdicts)
        triage_counter = Counter(triages)

        verdict_mode = verdict_counter.most_common(1)[0][0]
        triage_mode = triage_counter.most_common(1)[0][0]
        n = len(case_rows)

        verdict_consistency = round(verdict_counter[verdict_mode] / n, 3)
        triage_consistency = round(triage_counter[triage_mode] / n, 3)
        recovery_trigger_rate = round(
            sum(1 for t in triages if _TRIAGE_RECOVERY in t) / n, 3
        )

        summary_row = {
            "run_set_id": run_set_id,
            "case_id": case_id,
            "label": case["label"],
            "section_id": case["section_id"],
            "expected_score_min": case["expected_score_min"],
            "expected_score_max": case["expected_score_max"],
            "expected_verdict": case["expected_verdict"],
            "expected_triage": case["expected_triage"],
            "runs": n,
            "score_mean": _safe_mean(scores),
            "score_std": _safe_stdev(scores),
            "score_min": min((s for s in scores if s >= 0), default=-1.0),
            "score_max": max((s for s in scores if s >= 0), default=-1.0),
            "completeness_mean": _safe_mean(completeness),
            "specificity_mean": _safe_mean(specificity),
            "internal_consistency_mean": _safe_mean(i_consistency),
            "implementability_mean": _safe_mean(implementability),
            "verdict_mode": verdict_mode,
            "verdict_consistency": verdict_consistency,
            "triage_mode": triage_mode,
            "triage_consistency": triage_consistency,
            "recovery_trigger_rate": recovery_trigger_rate,
            "resolved_mean": round(statistics.mean(resolved), 2) if resolved else 0.0,
            "unresolved_mean": round(statistics.mean(unresolved), 2) if unresolved else 0.0,
        }
        summary_row["ci_status"] = _ci_status_for_row(summary_row)
        summary_rows.append(summary_row)

    return summary_rows


# ── Console reporting ───────────────────────────────────────────────────────────

def _print_progress(case_id: str, run_index: int, total_runs: int, row: dict) -> None:
    status = "✓" if row["enforced_verdict"] == "PASS" else "✗"
    parse_flag = " [parse_error]" if row["parse_error"] else ""
    print(
        f"  {status} {case_id}  run {run_index}/{total_runs}"
        f"  score={row['overall_score']:.1f}"
        f"  verdict={row['enforced_verdict']}"
        f"  triage={_triage_shorthand(row['triage_decision'])}"
        f"{parse_flag}"
    )


def _print_summary_table(summary_rows: list[dict]) -> None:
    print("\n" + "─" * 80)
    print(
        f"{'CASE':<28} {'EXP':>5} {'MEAN':>5} {'STD':>4} "
        f"{'VERDICT':>7} {'TRIAGE':>9} {'CI':>5}"
    )
    print("─" * 80)
    for row in summary_rows:
        exp_range = f"{row['expected_score_min']:.1f}–{row['expected_score_max']:.1f}"
        score_mean = float(row["score_mean"])
        mean_str = f"{score_mean:.2f}" if score_mean >= 0 else " N/A"
        std_str = f"{float(row['score_std']):.2f}"
        triage_short = _triage_shorthand(row["triage_mode"])
        ci = row["ci_status"]
        print(
            f"  {row['case_id']:<26} {exp_range:>8} {mean_str:>6} {std_str:>5} "
            f"{row['verdict_mode']:>8} {triage_short:>9} {ci:>6}"
        )
    print("─" * 80)

    fails = [r for r in summary_rows if r["ci_status"] == "FAIL"]
    warns = [r for r in summary_rows if r["ci_status"] == "WARN"]
    passes = [r for r in summary_rows if r["ci_status"] == "PASS"]
    print(f"  PASS: {len(passes)}   WARN: {len(warns)}   FAIL: {len(fails)}")
    print("─" * 80 + "\n")


# ── Main ────────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run PRD Reflector scoring evaluation cases."
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=1,
        metavar="N",
        help="Number of times to run each case (default: 1). Use ≥3 for consistency metrics.",
    )
    parser.add_argument(
        "--cases",
        nargs="+",
        metavar="CASE_ID",
        help="Run only these case IDs (default: all cases).",
    )
    parser.add_argument(
        "--runs-csv",
        type=Path,
        default=DEFAULT_RUNS_CSV,
        metavar="PATH",
        help=f"Path to the append-only raw log CSV (default: {DEFAULT_RUNS_CSV}).",
    )
    parser.add_argument(
        "--summary-csv",
        type=Path,
        default=DEFAULT_SUMMARY_CSV,
        metavar="PATH",
        help=f"Path to the summary CSV (overwritten each run; default: {DEFAULT_SUMMARY_CSV}).",
    )
    parser.add_argument(
        "--model",
        default=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
        metavar="MODEL",
        help="Gemini model name (default: $GEMINI_MODEL or gemini-2.5-flash).",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    # Filter cases
    active_cases = CASES
    if args.cases:
        case_ids = set(args.cases)
        active_cases = [c for c in CASES if c["case_id"] in case_ids]
        unknown = case_ids - {c["case_id"] for c in active_cases}
        if unknown:
            print(f"[warn] Unknown case IDs: {', '.join(sorted(unknown))}", file=sys.stderr)

    if not active_cases:
        print("[error] No cases to run.", file=sys.stderr)
        sys.exit(1)

    total_calls = len(active_cases) * args.runs
    run_set_id = str(uuid.uuid4())

    print(f"\nRun set  : {run_set_id}")
    print(f"Model    : {args.model}")
    print(f"Cases    : {len(active_cases)}  |  runs each: {args.runs}  |  total calls: {total_calls}")
    print(f"Runs CSV : {args.runs_csv}")
    print(f"Summary  : {args.summary_csv}\n")

    llm = _build_llm(args.model)
    all_rows: list[dict] = []

    for case in active_cases:
        print(f"  Case: {case['case_id']}  ({case['label']})")
        for run_index in range(1, args.runs + 1):
            row = _run_case_once(
                case=case,
                run_index=run_index,
                run_set_id=run_set_id,
                model_version=args.model,
                llm=llm,
            )
            _print_progress(case["case_id"], run_index, args.runs, row)
            all_rows.append(row)

    # Persist
    _append_runs_csv(all_rows, args.runs_csv)
    summary_rows = _compute_summary(all_rows, active_cases, run_set_id)
    _write_summary_csv(summary_rows, args.summary_csv)

    _print_summary_table(summary_rows)
    print(f"Appended {len(all_rows)} row(s) to {args.runs_csv}")
    print(f"Wrote summary to {args.summary_csv}\n")


if __name__ == "__main__":
    main()
