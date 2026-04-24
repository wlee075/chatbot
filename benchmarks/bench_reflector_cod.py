"""
Chain-of-Draft (CoD) variant — Reflector latency + parse-accuracy benchmark.

Applies the Chain-of-Draft technique to REFLECTOR_SYSTEM:
  • Global instruction: think step by step but keep each reasoning step ≤5 words.
  • Per-rubric prose guidance compressed from "Write 1–2 sentences" → "≤5 words".
  • REQUIREMENT STATUS / GAPS / TRIAGE / VERDICT sections left structurally intact
    so all regex parsers still work.

Results are written to benchmarks/results/reflector_cod.csv.
Run bench_reflector.py first to produce the baseline CSV, then compare.

Usage:
    python -m benchmarks.bench_reflector_cod          # N_RUNS=3 (default)
    N_RUNS=5 python -m benchmarks.bench_reflector_cod

Metrics are identical to bench_reflector.py so the two CSVs can be concatenated
and compared directly.
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
OUTPUT_CSV = RESULTS_DIR / "reflector_cod.csv"

# ── CoD prompt patch ──────────────────────────────────────────────────────────
# We apply two changes to the baseline REFLECTOR_SYSTEM text:
#   1. Insert a Chain-of-Draft instruction block immediately before "Output format:"
#   2. Replace each "Write 1–2 sentences." with "≤5 words."
# This preserves all score/verdict/triage regex anchors unchanged.

_COD_INSTRUCTION_BLOCK = """\
Chain-of-Draft instruction:
Before scoring each rubric, reason briefly.
Keep EVERY reasoning step to ≤5 words.
No lengthy explanations — concise keywords only.

"""

_COD_PER_RUBRIC_GUIDANCE = "≤5 words."


def _apply_cod_patch(template: str) -> str:
    """Inject CoD instruction and compress per-rubric prose guidance."""
    # 1. Insert CoD block before "Output format:"
    patched = template.replace("Output format:", _COD_INSTRUCTION_BLOCK + "Output format:", 1)

    # 2. Replace each "Write 1–2 sentences." with "≤5 words."
    patched = patched.replace("Write 1–2 sentences.", _COD_PER_RUBRIC_GUIDANCE)
    patched = patched.replace("Write 1 sentence explaining the overall score.", _COD_PER_RUBRIC_GUIDANCE)

    return patched


# ── synthetic drafts (same as bench_reflector.py) ────────────────────────────
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
    return ChatGoogleGenerativeAI(model=model, temperature=0, thinking_budget=0 )


def _parse_rubric_score(text: str, rubric: str) -> float:
    m = re.search(
        rf"{re.escape(rubric)}[^\d\n]*(\d+\.?\d*)\s*/\s*10",
        text,
        re.IGNORECASE,
    )
    return float(m.group(1)) if m else -1.0


def _build_cod_system_prompt() -> str:
    section = get_section_by_index(0)  # tl;dr
    expected_components_list = "\n".join(f"  • {c}" for c in section.expected_components)
    base = REFLECTOR_SYSTEM.format(
        section_title=section.title,
        prior_sections_block="No prior sections yet.",
        expected_components_list=expected_components_list,
        specificity_guidance=section.specificity_guidance,
        global_rigor_block=GLOBAL_RIGOR_BLOCK,
        scoring_interpretation_block=SCORING_INTERPRETATION_BLOCK,
    )
    return _apply_cod_patch(base)


def run_single(llm: ChatGoogleGenerativeAI, system_prompt: str, draft: str) -> dict:
    t0 = time.monotonic()
    response = llm.invoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=f"Review this draft for the 'tl;dr' section:\n\n{draft}"),
    ])
    latency_ms = int((time.monotonic() - t0) * 1000)
    text = response.content.strip()

    usage = getattr(response, "usage_metadata", None) or {}
    input_tokens  = usage.get("input_tokens", -1)
    output_tokens = usage.get("output_tokens", -1)

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

    m = re.search(r"OVERALL\s+SCORE[^\d]*(\d+\.?\d*)\s*/\s*10", text, re.IGNORECASE)
    overall = float(m.group(1)) if m else -1.0

    completeness     = _parse_rubric_score(text, "COMPLETENESS")
    specificity      = _parse_rubric_score(text, "SPECIFICITY")
    consistency      = _parse_rubric_score(text, "INTERNAL CONSISTENCY")
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
    system_prompt = _build_cod_system_prompt()

    # Show a snippet of the CoD patch so we can verify it applied correctly
    print("━" * 70)
    print("CoD instruction injected — prompt excerpt around 'Output format:':")
    idx = system_prompt.find("Chain-of-Draft instruction:")
    if idx >= 0:
        print(system_prompt[idx:idx + 200])
    print("━" * 70)
    print(f"Benchmarking CoD variant — {N_RUNS} run(s) × {len(SYNTHETIC_DRAFTS)} drafts")
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
                "prompt_variant":   "cod",
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
    print(f"{'CoD SUMMARY':^70}")
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

    all_lat  = [r["latency_ms"]   for r in rows]
    all_tok  = [r["output_tokens"] for r in rows]
    all_parse = [r["parse_score"] for r in rows]
    all_vacc = [
        1 if r["verdict"] == next(d["expected_verdict"] for d in SYNTHETIC_DRAFTS if d["label"] == r["draft_label"]) else 0
        for r in rows
    ]
    print(f"{'─'*70}")
    print(
        f"{'OVERALL':<20} {int(sum(all_lat)/len(all_lat)):>14} "
        f"{int(sum(all_tok)/len(all_tok)):>11} "
        f"{round(sum(all_parse)/len(all_parse), 3):>10} "
        f"{round(sum(all_vacc)/len(all_vacc), 2):>12}"
    )
    print(f"{'─'*70}")

    # ── baseline comparison (if available) ───────────────────────────────────
    baseline_csv = RESULTS_DIR / "reflector_baseline.csv"
    if baseline_csv.exists():
        print("\n── Comparison vs baseline ───────────────────────────────────────────")
        print(f"{'draft_label':<20} {'Δ latency_ms':>14} {'Δ out_tok':>11} {'Δ parse':>10}")
        print(f"{'─'*57}")
        import csv as _csv
        with open(baseline_csv, newline="", encoding="utf-8") as f:
            baseline_rows = list(_csv.DictReader(f))

        from collections import defaultdict as _dd
        b_grouped: dict[str, list] = _dd(list)
        for r in baseline_rows:
            b_grouped[r["draft_label"]].append(r)

        for draft in SYNTHETIC_DRAFTS:
            label = draft["label"]
            if label not in b_grouped:
                continue
            b_grp = b_grouped[label]
            c_grp = grouped[label]
            b_lat  = sum(int(r["latency_ms"])    for r in b_grp) / len(b_grp)
            c_lat  = sum(r["latency_ms"]          for r in c_grp) / len(c_grp)
            b_tok  = sum(int(r["output_tokens"])  for r in b_grp) / len(b_grp)
            c_tok  = sum(r["output_tokens"]        for r in c_grp) / len(c_grp)
            b_par  = sum(float(r["parse_score"])  for r in b_grp) / len(b_grp)
            c_par  = sum(r["parse_score"]          for r in c_grp) / len(c_grp)
            delta_lat = int(c_lat - b_lat)
            delta_tok = int(c_tok - b_tok)
            delta_par = round(c_par - b_par, 3)
            sign_lat = "+" if delta_lat >= 0 else ""
            sign_tok = "+" if delta_tok >= 0 else ""
            sign_par = "+" if delta_par >= 0 else ""
            print(
                f"{label:<20} {sign_lat}{delta_lat:>13} "
                f"{sign_tok}{delta_tok:>10} "
                f"{sign_par}{delta_par:>9}"
            )
    print()


if __name__ == "__main__":
    main()
