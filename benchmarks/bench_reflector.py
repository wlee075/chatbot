"""
Reflector latency + parse-accuracy benchmark.

Runs the current REFLECTOR_SYSTEM prompt against 5 synthetic tl;dr drafts
of varying quality (very_poor → excellent). Each draft is run N_RUNS times
and results are written to benchmarks/results/reflector_baseline.csv.

Usage:
    python -m benchmarks.bench_reflector              # N_RUNS=3 (default)
    N_RUNS=5 python -m benchmarks.bench_reflector

Metrics per run:
    - latency_ms          wall-clock time for one LLM call
    - input_tokens        prompt token count (from usage metadata)
    - output_tokens       completion token count
    - parse_verdict       1 if VERDICT parsed, else 0
    - parse_triage        1 if TRIAGE parsed, else 0
    - parse_overall       1 if OVERALL SCORE parsed (≥0), else 0
    - parse_completeness  1 if rubric score parsed, else 0
    - parse_specificity   1 if rubric score parsed, else 0
    - parse_consistency   1 if rubric score parsed, else 0
    - parse_implementability 1 if rubric score parsed, else 0
    - parse_score         composite: mean of all 7 parse flags
    - overall_score       parsed value or -1.0
    - verdict             PASS | REWORK
    - triage              RECOVERY | NORMAL
"""

from __future__ import annotations

import csv
import os
import re
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI

# ── path bootstrap so we can import project modules without install ───────────
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

load_dotenv()

from config.sections import get_section_by_index
from prompts.templates import (
    GLOBAL_RIGOR_BLOCK,
    REFLECTOR_PRIOR_SECTIONS_BLOCK,
    REFLECTOR_SYSTEM,
    SCORING_INTERPRETATION_BLOCK,
)

# ── config ────────────────────────────────────────────────────────────────────
N_RUNS: int = int(os.getenv("N_RUNS", "3"))
RESULTS_DIR = Path(__file__).parent / "results"
OUTPUT_CSV = RESULTS_DIR / "reflector_baseline.csv"

# ── synthetic drafts (tl;dr section) ─────────────────────────────────────────
# Five quality levels so we can verify verdict accuracy and parse robustness
# across a range of outputs, not just edge cases.
SYNTHETIC_DRAFTS: list[dict] = [
    {
        "label": "very_poor",
        "expected_verdict": "REWORK",
        "text": "We want to improve user acquisition.",
    },
    {
        "label": "poor",
        "expected_verdict": "REWORK",
        "text": (
            "This initiative aims to increase user acquisition for internal teams. "
            "[NEEDS CLARIFICATION: Who is primarily affected by this problem?] "
            "[NEEDS CLARIFICATION: What is the proposed solution?] "
            "[NEEDS CLARIFICATION: What is the expected impact?]"
        ),
    },
    {
        "label": "medium",
        "expected_verdict": "REWORK",
        "text": (
            "This initiative addresses low new-user conversion rates on the mobile "
            "onboarding flow. The proposed solution is a redesigned onboarding "
            "sequence that reduces required steps from 7 to 3. "
            "[NEEDS CLARIFICATION: What is the quantified expected impact on "
            "conversion rate or revenue?]"
        ),
    },
    {
        "label": "good",
        "expected_verdict": "REWORK",
        "text": (
            "New users on mobile are failing to complete onboarding (current "
            "completion rate: 42%). This initiative redesigns the onboarding flow "
            "to reduce required steps from 7 to 3, targeting a completion rate of "
            "65% within 90 days of launch. The primary beneficiaries are "
            "first-time mobile users in the 18–34 segment. "
            "[NEEDS CLARIFICATION: What is the estimated revenue impact of a "
            "23-point lift in onboarding completion?]"
        ),
    },
    {
        "label": "excellent",
        "expected_verdict": "PASS",
        "text": (
            "New users on mobile are failing to complete onboarding at an "
            "unacceptable rate (current completion: 42%, industry benchmark: 68%). "
            "This initiative redesigns the onboarding flow to reduce required steps "
            "from 7 to 3 by removing account-linking and permission prompts from "
            "the critical path. The primary beneficiaries are first-time mobile "
            "users (iOS + Android), estimated at 120,000 new installs per month. "
            "Success is defined as: onboarding completion ≥ 65% within 90 days "
            "of launch, translating to an estimated $420K incremental ARR based "
            "on current ARPU of $18."
        ),
    },
]


# ── helpers ───────────────────────────────────────────────────────────────────

def _get_llm() -> ChatGoogleGenerativeAI:
    model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    return ChatGoogleGenerativeAI(model=model, temperature=0)


def _parse_rubric_score(text: str, rubric: str) -> float:
    m = re.search(
        rf"{re.escape(rubric)}[^\d\n]*(\d+\.?\d*)\s*/\s*10",
        text,
        re.IGNORECASE,
    )
    return float(m.group(1)) if m else -1.0


def _build_system_prompt(section_title: str = "tl;dr") -> str:
    section = get_section_by_index(0)  # tl;dr is index 0
    expected_components_list = "\n".join(f"  • {c}" for c in section.expected_components)
    return REFLECTOR_SYSTEM.format(
        section_title=section.title,
        prior_sections_block="No prior sections yet.",
        expected_components_list=expected_components_list,
        specificity_guidance=section.specificity_guidance,
        global_rigor_block=GLOBAL_RIGOR_BLOCK,
        scoring_interpretation_block=SCORING_INTERPRETATION_BLOCK,
    )


def run_single(llm: ChatGoogleGenerativeAI, system_prompt: str, draft: str) -> dict:
    """Run one reflector call and return parsed metrics."""
    t0 = time.monotonic()
    response = llm.invoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=f"Review this draft for the 'tl;dr' section:\n\n{draft}"),
    ])
    latency_ms = int((time.monotonic() - t0) * 1000)
    text = response.content.strip()

    # ── token counts (best-effort from usage_metadata) ────────────────────────
    usage = getattr(response, "usage_metadata", None) or {}
    input_tokens = usage.get("input_tokens", -1)
    output_tokens = usage.get("output_tokens", -1)

    # ── parse fields ──────────────────────────────────────────────────────────
    verdict = ""
    for line in reversed(text.splitlines()):
        clean = line.strip().lstrip("*# \t")
        if clean.upper().startswith("VERDICT: PASS"):
            verdict = "PASS"
            break
        if clean.upper().startswith("VERDICT: REWORK"):
            verdict = "REWORK"
            break

    triage = ""
    for line in text.splitlines():
        clean = line.strip().lstrip("*# \t")
        if "TRIAGE: ENTER RECOVERY MODE" in clean.upper():
            triage = "RECOVERY"
            break
        if "TRIAGE: NORMAL ITERATION" in clean.upper():
            triage = "NORMAL"
            break

    overall = _parse_rubric_score(text, "OVERALL SCORE")
    # Special-case: OVERALL SCORE regex is slightly different
    m = re.search(r"OVERALL\s+SCORE[^\d]*(\d+\.?\d*)\s*/\s*10", text, re.IGNORECASE)
    overall = float(m.group(1)) if m else -1.0

    completeness   = _parse_rubric_score(text, "COMPLETENESS")
    specificity    = _parse_rubric_score(text, "SPECIFICITY")
    consistency    = _parse_rubric_score(text, "INTERNAL CONSISTENCY")
    implementability = _parse_rubric_score(text, "IMPLEMENTABILITY")

    flags = {
        "parse_verdict":          int(bool(verdict)),
        "parse_triage":           int(bool(triage)),
        "parse_overall":          int(overall >= 0),
        "parse_completeness":     int(completeness >= 0),
        "parse_specificity":      int(specificity >= 0),
        "parse_consistency":      int(consistency >= 0),
        "parse_implementability": int(implementability >= 0),
    }
    parse_score = round(sum(flags.values()) / len(flags), 3)

    return {
        "latency_ms":          latency_ms,
        "input_tokens":        input_tokens,
        "output_tokens":       output_tokens,
        **flags,
        "parse_score":         parse_score,
        "overall_score":       overall,
        "verdict":             verdict,
        "triage":              triage,
    }


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    llm = _get_llm()
    system_prompt = _build_system_prompt()

    print(f"Benchmarking REFLECTOR_SYSTEM — {N_RUNS} run(s) × {len(SYNTHETIC_DRAFTS)} drafts")
    print(f"Model: {os.getenv('GEMINI_MODEL', 'gemini-2.5-flash')}")
    print(f"Output: {OUTPUT_CSV}\n")

    fieldnames = [
        "prompt_variant", "draft_label", "expected_verdict", "run",
        "latency_ms", "input_tokens", "output_tokens",
        "parse_verdict", "parse_triage", "parse_overall",
        "parse_completeness", "parse_specificity",
        "parse_consistency", "parse_implementability",
        "parse_score", "overall_score", "verdict", "triage",
    ]

    rows: list[dict] = []

    for draft in SYNTHETIC_DRAFTS:
        for run_idx in range(1, N_RUNS + 1):
            print(f"  [{draft['label']}] run {run_idx}/{N_RUNS} ...", end=" ", flush=True)
            metrics = run_single(llm, system_prompt, draft["text"])
            row = {
                "prompt_variant":   "baseline",
                "draft_label":      draft["label"],
                "expected_verdict": draft["expected_verdict"],
                "run":              run_idx,
                **metrics,
            }
            rows.append(row)
            verdict_ok = "✓" if metrics["verdict"] == draft["expected_verdict"] else "✗"
            print(
                f"{metrics['latency_ms']}ms  "
                f"out={metrics['output_tokens']}tok  "
                f"score={metrics['overall_score']}  "
                f"verdict={metrics['verdict']} {verdict_ok}  "
                f"parse={metrics['parse_score']}"
            )

    # ── write CSV ─────────────────────────────────────────────────────────────
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    # ── summary table ─────────────────────────────────────────────────────────
    print(f"\n{'─'*70}")
    print(f"{'SUMMARY':^70}")
    print(f"{'─'*70}")
    print(f"{'draft_label':<20} {'avg_latency_ms':>14} {'avg_out_tok':>11} "
          f"{'avg_parse':>10} {'verdict_acc':>12}")
    print(f"{'─'*70}")

    from collections import defaultdict
    grouped: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        grouped[r["draft_label"]].append(r)

    for draft in SYNTHETIC_DRAFTS:
        label = draft["label"]
        grp = grouped[label]
        avg_lat = int(sum(r["latency_ms"] for r in grp) / len(grp))
        avg_tok = int(sum(r["output_tokens"] for r in grp) / len(grp))
        avg_parse = round(sum(r["parse_score"] for r in grp) / len(grp), 3)
        acc = round(
            sum(1 for r in grp if r["verdict"] == draft["expected_verdict"]) / len(grp), 2
        )
        print(f"{label:<20} {avg_lat:>14} {avg_tok:>11} {avg_parse:>10} {acc:>12}")

    print(f"{'─'*70}")
    all_lat = [r["latency_ms"] for r in rows]
    all_tok = [r["output_tokens"] for r in rows]
    all_parse = [r["parse_score"] for r in rows]
    all_correct = [r for r in rows if r["verdict"] == r["expected_verdict"]]
    print(f"{'OVERALL':<20} {int(sum(all_lat)/len(all_lat)):>14} "
          f"{int(sum(all_tok)/len(all_tok)):>11} "
          f"{round(sum(all_parse)/len(all_parse),3):>10} "
          f"{round(len(all_correct)/len(rows),2):>12}")
    print(f"\nFull results saved to: {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
