"""
CI gate for the PRD Reflector evaluation harness.

Reads the summary CSV produced by run_reflector_eval.py and asserts that
each case meets its expected score range, verdict, and (optionally) triage.
No LLM calls are made.

Usage
─────
  # Default: reads reflector_eval_summary.csv in the project root
  .venv/bin/python -m tests.ci_checks

  # Explicit path
  .venv/bin/python -m tests.ci_checks --summary path/to/summary.csv

  # Fail on warnings as well
  .venv/bin/python -m tests.ci_checks --strict

Exit codes
──────────
  0  — all checks passed (or only warnings when --strict is not set)
  1  — at least one FAIL
  2  — summary file not found
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

# ── Defaults ───────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SUMMARY_CSV = PROJECT_ROOT / "reflector_eval_summary.csv"

# Threshold: fraction of NORMAL-expected cases that may accidentally trigger
# RECOVERY mode before we emit a global warning.
RECOVERY_BLEED_THRESHOLD = 0.30

# ── Helpers ─────────────────────────────────────────────────────────────────────

def _triage_shorthand(full_string: str) -> str:
    return "RECOVERY" if "RECOVERY MODE" in full_string.upper() else "NORMAL"


def _fmt(label: str, case_id: str, detail: str) -> str:
    return f"  [{label}] {case_id}: {detail}"


# ── Per-row checks ──────────────────────────────────────────────────────────────

def _check_row(row: dict, min_consistency: float, max_score_std: float) -> list[tuple[str, str]]:
    """
    Return a list of (severity, message) tuples for this summary row.
    severity is "FAIL" or "WARN".
    """
    issues: list[tuple[str, str]] = []
    case_id = row["case_id"]
    n = int(row["runs"])

    score_mean = float(row["score_mean"])
    exp_min = float(row["expected_score_min"])
    exp_max = float(row["expected_score_max"])

    # ── Score range ──
    if score_mean < 0:
        issues.append(("WARN", _fmt("WARN", case_id, "score_mean=-1 (parse failure — check raw_output)")))
    else:
        tolerance = 0.5
        if score_mean < exp_min - tolerance or score_mean > exp_max + tolerance:
            issues.append((
                "FAIL",
                _fmt(
                    "FAIL", case_id,
                    f"score_mean={score_mean:.2f} outside expected [{exp_min}–{exp_max}] "
                    f"(tolerance ±{tolerance})",
                ),
            ))
        elif score_mean < exp_min or score_mean > exp_max:
            issues.append((
                "WARN",
                _fmt(
                    "WARN", case_id,
                    f"score_mean={score_mean:.2f} narrowly outside expected [{exp_min}–{exp_max}]",
                ),
            ))

    # ── Score stability (only meaningful with multiple runs) ──
    if n > 1:
        score_std = float(row["score_std"])
        if score_std > max_score_std:
            issues.append((
                "WARN",
                _fmt("WARN", case_id, f"score_std={score_std:.2f} > max allowed {max_score_std}"),
            ))

    # ── Verdict match — primary correctness gate ──
    verdict_mode = row["verdict_mode"]
    expected_verdict = row["expected_verdict"]
    if verdict_mode != expected_verdict:
        issues.append((
            "FAIL",
            _fmt(
                "FAIL", case_id,
                f"verdict_mode={verdict_mode!r} != expected {expected_verdict!r}",
            ),
        ))

    # ── Verdict consistency (only meaningful with multiple runs) ──
    if n > 1:
        verdict_consistency = float(row["verdict_consistency"])
        if verdict_consistency < min_consistency:
            issues.append((
                "WARN",
                _fmt(
                    "WARN", case_id,
                    f"verdict_consistency={verdict_consistency:.2f} < threshold {min_consistency}",
                ),
            ))

    # ── Triage match — softer gate (WARN not FAIL) ──
    triage_mode_short = _triage_shorthand(row["triage_mode"])
    expected_triage = row["expected_triage"]  # "NORMAL" or "RECOVERY"
    if triage_mode_short != expected_triage:
        issues.append((
            "WARN",
            _fmt(
                "WARN", case_id,
                f"triage_mode={triage_mode_short!r} != expected {expected_triage!r} "
                f"(recovery_trigger_rate={float(row['recovery_trigger_rate']):.2f})",
            ),
        ))

    # ── Triage consistency (only meaningful with multiple runs) ──
    if n > 1:
        triage_consistency = float(row["triage_consistency"])
        if triage_consistency < min_consistency:
            issues.append((
                "WARN",
                _fmt(
                    "WARN", case_id,
                    f"triage_consistency={triage_consistency:.2f} < threshold {min_consistency}",
                ),
            ))

    return issues


# ── Global checks ───────────────────────────────────────────────────────────────

def _check_global(rows: list[dict]) -> list[tuple[str, str]]:
    """
    Cross-case checks that only make sense when looking at the full run set.
    """
    issues: list[tuple[str, str]] = []

    # Recovery bleed: NORMAL-expected cases that accidentally triggered recovery
    normal_cases = [r for r in rows if r["expected_triage"] == "NORMAL"]
    if normal_cases:
        bleed_count = sum(
            1 for r in normal_cases
            if _triage_shorthand(r["triage_mode"]) == "RECOVERY"
        )
        bleed_rate = bleed_count / len(normal_cases)
        if bleed_rate > RECOVERY_BLEED_THRESHOLD:
            issues.append((
                "WARN",
                f"  [WARN] GLOBAL: {bleed_count}/{len(normal_cases)} NORMAL-expected cases "
                f"triggered RECOVERY mode ({bleed_rate:.0%} > {RECOVERY_BLEED_THRESHOLD:.0%} threshold)",
            ))

    return issues


# ── Main ────────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="CI gate: assert Reflector scoring meets expected thresholds."
    )
    parser.add_argument(
        "--summary",
        type=Path,
        default=DEFAULT_SUMMARY_CSV,
        metavar="PATH",
        help=f"Path to the summary CSV (default: {DEFAULT_SUMMARY_CSV}).",
    )
    parser.add_argument(
        "--min-consistency",
        type=float,
        default=0.67,
        metavar="FRAC",
        help="Minimum verdict/triage consistency fraction for multi-run cases (default: 0.67).",
    )
    parser.add_argument(
        "--max-score-std",
        type=float,
        default=1.5,
        metavar="STD",
        help="Maximum acceptable score standard deviation for multi-run cases (default: 1.5).",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit 1 on WARN as well as FAIL.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    if not args.summary.exists():
        print(f"[error] Summary file not found: {args.summary}", file=sys.stderr)
        print(
            "Run `python -m tests.run_reflector_eval` first to generate it.",
            file=sys.stderr,
        )
        sys.exit(2)

    with open(args.summary, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))

    if not rows:
        print("[error] Summary file is empty.", file=sys.stderr)
        sys.exit(2)

    print(f"\nCI checks on: {args.summary}")
    print(f"Cases       : {len(rows)}")
    print(f"Consistency : ≥{args.min_consistency}  Score STD: ≤{args.max_score_std}  Strict: {args.strict}\n")

    all_issues: list[tuple[str, str]] = []

    for row in rows:
        all_issues.extend(
            _check_row(row, args.min_consistency, args.max_score_std)
        )
    all_issues.extend(_check_global(rows))

    fails = [msg for sev, msg in all_issues if sev == "FAIL"]
    warns = [msg for sev, msg in all_issues if sev == "WARN"]

    if fails:
        print("FAILURES")
        print("────────")
        for msg in fails:
            print(msg)
        print()

    if warns:
        print("WARNINGS")
        print("────────")
        for msg in warns:
            print(msg)
        print()

    total_cases = len(rows)
    fail_cases = len({msg.split(":")[0] for msg in fails})
    warn_cases = len({msg.split(":")[0] for msg in warns})

    if not all_issues:
        print(f"All {total_cases} case(s) passed.\n")
        sys.exit(0)

    print(
        f"Result: {total_cases} case(s) checked — "
        f"{fail_cases} FAIL, {warn_cases} WARN"
    )

    if fails:
        print("Exit: 1 (FAIL)\n")
        sys.exit(1)

    if warns and args.strict:
        print("Exit: 1 (WARN with --strict)\n")
        sys.exit(1)

    print("Exit: 0 (warnings only)\n")
    sys.exit(0)


if __name__ == "__main__":
    main()
