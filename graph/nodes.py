'''
Parses OVERALL SCORE with a format-agnostic regex
If score ≥ 0 (parseable): overrides PASS → REWORK when score < 8.5; forces TRIAGE: ENTER RECOVERY MODE when score < 5.0
Stores overall_score in state and chat_history dict (useful for the future test set)
Stores SCORING_INTERPRETATION_BLOCK forwarded to REFLECTOR_SYSTEM.format()
advance_section_node resets overall_score to -1.0

Threshold behaviour table:
OVERALL SCORE	LLM says PASS	System enforces
≥ 8.5	PASS	PASS
5.0–8.4	PASS	REWORK (override)
< 5.0	anything	REWORK + ENTER RECOVERY MODE
-1.0 (parse fail)	PASS	PASS (no override)
'''
import os
import re
import time

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.types import interrupt

from config.sections import PRD_SECTIONS, get_section_by_index
from graph.state import PRDState
from utils.logger import log_event
from prompts.templates import (
    DECISION_ENFORCEMENT_BLOCK,
    DEFAULT_MAX_RECOVERY_MODE_CONSECUTIVE_ITERATIONS,
    DEFAULT_MAX_SECTION_ITERATIONS,
    DRAFTER_CONTEXT_DOC_BLOCK,
    DRAFTER_PRD_CONTEXT_BLOCK,
    DRAFTER_SYSTEM,
    ELICITOR_CONTEXT_BLOCK,
    ELICITOR_ITERATION_BLOCK,
    ELICITOR_PRD_BLOCK,
    ELICITOR_SYSTEM,
    GLOBAL_RIGOR_BLOCK,
    HUMAN_TRUST_BLOCK,
    ITERATION_DISCIPLINE_BLOCK,
    PASS_SCORE_THRESHOLD,
    RECOVERY_MODE_SCORE_THRESHOLD,
    REFLECTOR_PRIOR_SECTIONS_BLOCK,
    REFLECTOR_SYSTEM,
    SCORING_INTERPRETATION_BLOCK,
)


# ── Shared helpers ────────────────────────────────────────────────────────────

def _get_llm() -> ChatGoogleGenerativeAI:
    model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    return ChatGoogleGenerativeAI(model=model, temperature=0)


def _format_prd_so_far(prd_sections: dict) -> str:
    """Render accumulated PRD sections as readable markdown."""
    if not prd_sections:
        return ""
    parts = []
    for section in PRD_SECTIONS:
        if section.id in prd_sections:
            parts.append(f"## {section.title}\n{prd_sections[section.id]}")
    return "\n\n".join(parts)


def _parse_rubric_score(text: str, rubric: str) -> float:
    """Extract a single rubric score from reflector output. Returns -1.0 on failure.

    Tolerates varied separator formats: em-dash, colon, space, markdown bold, etc.
    Restricts to the same line as the rubric name to avoid false matches.
    """
    m = re.search(
        rf"{re.escape(rubric)}[^\d\n]*(\d+\.?\d*)\s*/\s*10",
        text,
        re.IGNORECASE,
    )
    return float(m.group(1)) if m else -1.0


def _log_ctx(state: PRDState, node_name: str) -> dict:
    """Extract the standard context fields required by every log_event call."""
    section_idx = state.get("section_index", 0)
    section_name = (
        PRD_SECTIONS[section_idx].title
        if 0 <= section_idx < len(PRD_SECTIONS)
        else ""
    )
    return {
        "thread_id": state.get("thread_id", ""),
        "run_id": state.get("run_id", ""),
        "node_name": node_name,
        "section_name": section_name,
        "section_index": section_idx,
        "iteration": state.get("iteration", 0),
    }


# ── Node: load_context ────────────────────────────────────────────────────────

def load_context_node(state: PRDState) -> dict:
    """
    Passthrough — context doc is already in state.
    Emits a welcome message to seed the chat history.
    """
    ctx = _log_ctx(state, "load_context_node")
    t0 = time.monotonic()
    context_len = len(state.get("context_doc", ""))
    log_event(
        **ctx, level="INFO", event_type="node_start",
        message="load_context_node started",
        context_doc_present=bool(state.get("context_doc")),
        context_len=context_len,
    )
    first_section = get_section_by_index(0)
    section_list = ", ".join(s.title for s in PRD_SECTIONS)

    doc_note = (
        "\n\n📎 _Context document loaded — I'll use it to ask sharper questions._"
        if state.get("context_doc")
        else ""
    )

    welcome = (
        f"👋 Welcome! I'll guide you through building a PRD **section by section** "
        f"using the reflection pattern.\n\n"
        f"**{len(PRD_SECTIONS)} sections to complete:** {section_list}\n\n"
        f"For each section I will:\n"
        f"1. Ask you targeted questions\n"
        f"2. Draft the section from your answers\n"
        f"3. Review it against **4 rubrics** (Completeness, Specificity, "
        f"Internal Consistency, Implementability)\n"
        f"4. Loop with sharper follow-ups if needed "
        f"(max {state.get('max_iterations', DEFAULT_MAX_SECTION_ITERATIONS)} iterations)\n\n"
        f"Let's start with **{first_section.title}**.{doc_note}"
    )

    duration_ms = int((time.monotonic() - t0) * 1000)
    log_event(
        **ctx, level="INFO", event_type="node_end",
        message="load_context_node finished",
        duration_ms=duration_ms,
        context_doc_present=bool(state.get("context_doc")),
        context_len=context_len,
    )
    return {
        "chat_history": [
            {"role": "assistant", "type": "system", "content": welcome}
        ]
    }


# ── Node: generate_questions ──────────────────────────────────────────────────

def generate_questions_node(state: PRDState) -> dict:
    """
    Elicitor — generates targeted questions for the current PRD section.
    Questions are added to chat_history so the PM sees them immediately.
    """
    ctx = _log_ctx(state, "generate_questions_node")
    t0 = time.monotonic()
    section = get_section_by_index(state["section_index"])
    iteration = state.get("iteration", 0)
    llm = _get_llm()

    # ── Build each named block separately ────────────────────────────────────
    prd_so_far = _format_prd_so_far(state.get("prd_sections", {}))
    prd_block = ELICITOR_PRD_BLOCK.format(prd_so_far=prd_so_far) if prd_so_far else ""

    context_block = (
        ELICITOR_CONTEXT_BLOCK.format(context_doc=state["context_doc"])
        if state.get("context_doc")
        else ""
    )

    if iteration > 0 and state.get("reflection"):
        raw_gaps = state.get("requirement_gaps", "")
        iteration_block = ELICITOR_ITERATION_BLOCK.format(
            iteration=iteration + 1,
            max_iterations=state.get("max_iterations", DEFAULT_MAX_SECTION_ITERATIONS),
            reflection=state["reflection"],
            requirement_gaps=(
                raw_gaps if raw_gaps
                else "None identified. Refer to reflection feedback above."
            ),
            triage_decision=state.get("triage_decision", "TRIAGE: NORMAL ITERATION"),
        )
    else:
        iteration_block = ""

    expected_components_list = "\n".join(
        f"  \u2022 {c}" for c in section.expected_components
    )

    system_prompt = ELICITOR_SYSTEM.format(
        section_title=section.title,
        section_description=section.description,
        expected_components_list=expected_components_list,
        context_block=context_block,
        prd_block=prd_block,
        iteration_block=iteration_block,
        global_rigor_block=GLOBAL_RIGOR_BLOCK,
        decision_enforcement_block=DECISION_ENFORCEMENT_BLOCK,
        iteration_discipline_block=ITERATION_DISCIPLINE_BLOCK,
        human_trust_block=HUMAN_TRUST_BLOCK,
    )

    response = llm.invoke(
        [
            SystemMessage(content=system_prompt),
            HumanMessage(
                content=f"Generate questions for the '{section.title}' section."
            ),
        ]
    )
    questions = response.content.strip()

    triage = state.get("triage_decision", "")
    gaps_count = len([l for l in state.get("requirement_gaps", "").splitlines() if l.strip()])
    question_count = len([
        l for l in questions.splitlines()
        if l.strip() and (l.strip()[0:1].isdigit() or l.strip()[0:1] in ("-", "•", "*"))
    ])
    log_event(
        **ctx, level="INFO", event_type="node_start",
        message="generate_questions_node started",
        is_follow_up=(iteration > 0),
        triage="RECOVERY" if "RECOVERY MODE" in triage else "NORMAL",
        gaps_count=gaps_count,
    )
    if not questions:
        log_event(
            **ctx, level="WARNING", event_type="elicitor_empty_output",
            message="generate_questions_node produced empty output",
        )
    log_event(
        **ctx, level="INFO", event_type="elicitor_output",
        message="Questions generated",
        is_follow_up=(iteration > 0),
        triage="RECOVERY" if "RECOVERY MODE" in triage else "NORMAL",
        gaps_count=gaps_count, question_count=question_count, output_len=len(questions),
    )
    log_event(**ctx, level="DEBUG", event_type="elicitor_prompt",
              message="Elicitor system prompt", system_prompt=system_prompt)
    log_event(**ctx, level="DEBUG", event_type="elicitor_raw_output",
              message="Elicitor raw LLM response", raw_output=questions)
    duration_ms = int((time.monotonic() - t0) * 1000)
    log_event(
        **ctx, level="INFO", event_type="node_end",
        message="generate_questions_node finished",
        duration_ms=duration_ms, question_count=question_count,
    )

    # Build display header
    header = (
        f"**Section {state['section_index'] + 1}/{len(PRD_SECTIONS)}: "
        f"{section.title}**"
    )
    if iteration > 0:
        header += (
            f" _(follow-up \u00b7 iteration {iteration + 1}/"
            f"{state.get('max_iterations', DEFAULT_MAX_SECTION_ITERATIONS)})_"
        )

    return {
        "current_questions": questions,
        "chat_history": [
            {
                "role": "assistant",
                "type": "elicit",
                "section": section.title,
                "section_index": state["section_index"],
                "iteration": iteration,
                "content": f"{header}\n\n{questions}",
            }
        ],
    }


# ── Node: await_answer ────────────────────────────────────────────────────────

def await_answer_node(state: PRDState) -> dict:
    """
    Human-in-the-loop interrupt — pauses the graph until the PM submits
    their answer via the Streamlit chat input.

    The questions are already visible in chat_history (added by
    generate_questions_node), so the interrupt value only carries metadata.
    """
    section = get_section_by_index(state["section_index"])

    pm_answer: str = interrupt(
        {
            "type": "waiting_for_answer",
            "section": section.title,
            "section_index": state["section_index"],
        }
    )

    # Append this Q&A pair to the current section's history
    existing_qa = list(state.get("section_qa_pairs", []))
    new_qa = existing_qa + [
        {
            "questions": state.get("current_questions", ""),
            "answer": pm_answer,
        }
    ]

    return {
        "section_qa_pairs": new_qa,
        "chat_history": [{"role": "user", "content": pm_answer}],
    }


# ── Node: draft ───────────────────────────────────────────────────────────────

def draft_node(state: PRDState) -> dict:
    """
    Drafter — synthesises all Q&A pairs for the current section into a draft.
    """
    ctx = _log_ctx(state, "draft_node")
    t0 = time.monotonic()
    qa_rounds = len(state.get("section_qa_pairs", []))
    log_event(
        **ctx, level="INFO", event_type="node_start",
        message="draft_node started", qa_rounds=qa_rounds,
    )
    section = get_section_by_index(state["section_index"])
    llm = _get_llm()

    prd_so_far = _format_prd_so_far(state.get("prd_sections", {}))

    prd_context_block = (
        DRAFTER_PRD_CONTEXT_BLOCK.format(prd_so_far=prd_so_far)
        if prd_so_far
        else ""
    )
    context_doc_block = (
        DRAFTER_CONTEXT_DOC_BLOCK.format(context_doc=state["context_doc"])
        if state.get("context_doc")
        else ""
    )

    # Format accumulated Q&A for this section
    qa_parts = []
    for i, qa in enumerate(state.get("section_qa_pairs", []), 1):
        qa_parts.append(
            f"--- Round {i} ---\n"
            f"Questions:\n{qa['questions']}\n\n"
            f"PM's answer:\n{qa['answer']}"
        )
    qa_context = "\n\n".join(qa_parts)

    expected_components_list = "\n".join(
        f"  \u2022 {c}" for c in section.expected_components
    )

    system_prompt = DRAFTER_SYSTEM.format(
        section_title=section.title,
        section_description=section.description,
        expected_components_list=expected_components_list,
        prd_context_block=prd_context_block,
        context_doc_block=context_doc_block,
        global_rigor_block=GLOBAL_RIGOR_BLOCK,
    )

    response = llm.invoke(
        [
            SystemMessage(content=system_prompt),
            HumanMessage(
                content=(
                    f"Based on the Q&A below, write the '{section.title}' section:\n\n"
                    f"{qa_context}"
                )
            ),
        ]
    )
    draft = response.content.strip()

    assumption_count = draft.upper().count("[ASSUMPTION]")
    if not draft:
        log_event(**ctx, level="WARNING", event_type="drafter_empty_output",
                  message="draft_node produced an empty draft")
    if assumption_count > 3:
        log_event(**ctx, level="WARNING", event_type="drafter_high_assumptions",
                  message=f"Draft contains {assumption_count} [ASSUMPTION] markers",
                  assumption_count=assumption_count)
    log_event(
        **ctx, level="INFO", event_type="drafter_output",
        message="Draft produced",
        qa_rounds=qa_rounds, draft_len=len(draft), assumption_count=assumption_count,
    )
    log_event(**ctx, level="DEBUG", event_type="drafter_prompt",
              message="Drafter system prompt", system_prompt=system_prompt)
    log_event(**ctx, level="DEBUG", event_type="drafter_raw_output",
              message="Drafter raw LLM response", raw_output=draft)
    duration_ms = int((time.monotonic() - t0) * 1000)
    log_event(
        **ctx, level="INFO", event_type="node_end",
        message="draft_node finished",
        duration_ms=duration_ms, draft_len=len(draft), assumption_count=assumption_count,
    )

    return {
        "current_draft": draft,
        "chat_history": [
            {
                "role": "assistant",
                "type": "draft",
                "section": section.title,
                "content": draft,
            }
        ],
    }


# ── Node: reflect ─────────────────────────────────────────────────────────────

def reflect_node(state: PRDState) -> dict:
    """
    Reflector — evaluates the current draft against the 3-rubric framework
    and emits either VERDICT: PASS or VERDICT: REWORK.
    """
    ctx = _log_ctx(state, "reflect_node")
    t0 = time.monotonic()
    log_event(
        **ctx, level="INFO", event_type="node_start",
        message="reflect_node started",
        draft_len=len(state.get("current_draft", "")),
    )
    section = get_section_by_index(state["section_index"])
    llm = _get_llm()

    prd_so_far = _format_prd_so_far(state.get("prd_sections", {}))
    prior_sections_block = (
        REFLECTOR_PRIOR_SECTIONS_BLOCK.format(prd_so_far=prd_so_far)
        if prd_so_far
        else "No prior sections yet."
    )

    expected_components_list = "\n".join(
        f"  • {c}" for c in section.expected_components
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
                    f"{state['current_draft']}"
                )
            ),
        ]
    )
    reflection_text = response.content.strip()
    log_event(**ctx, level="DEBUG", event_type="reflector_prompt",
              message="Reflector system prompt", system_prompt=system_prompt)
    log_event(**ctx, level="DEBUG", event_type="reflector_raw_output",
              message="Reflector raw LLM response", raw_output=reflection_text)

    # Parse verdict from final line (VERDICT appears after TRIAGE in output)
    verdict = "REWORK"
    for line in reversed(reflection_text.splitlines()):
        clean = line.strip().lstrip("*# \t")
        if clean.upper().startswith("VERDICT: PASS"):
            verdict = "PASS"
            break
        if clean.upper().startswith("VERDICT: REWORK"):
            verdict = "REWORK"
            break

    # Parse triage decision (appears before VERDICT; scan forward)
    # Default to NORMAL ITERATION on parse failure — no spurious escalation.
    triage_decision = "TRIAGE: NORMAL ITERATION"
    for line in reflection_text.splitlines():
        clean = line.strip().lstrip("*# \t")
        if "TRIAGE: ENTER RECOVERY MODE" in clean.upper():
            triage_decision = "TRIAGE: ENTER RECOVERY MODE"
            break
        if "TRIAGE: NORMAL ITERATION" in clean.upper():
            triage_decision = "TRIAGE: NORMAL ITERATION"
            break

    # Extract requirement gaps (section 7 of reflector output)
    # Regex is format-agnostic: tolerates markdown bold, varying numbering style.
    gaps_match = re.search(
        r"REQUIREMENT GAPS\b.*?\n(.*?)(?=TRIAGE DECISION|\Z)",
        reflection_text,
        re.DOTALL | re.IGNORECASE,
    )
    requirement_gaps = gaps_match.group(1).strip() if gaps_match else ""

    # Parse OVERALL SCORE from "5. OVERALL SCORE — X.X/10"
    # Format-agnostic: tolerates markdown bold and em-dash/hyphen variants.
    score_match = re.search(
        r"OVERALL\s+SCORE[^\d]*(\d+\.?\d*)\s*/\s*10",
        reflection_text,
        re.IGNORECASE,
    )
    overall_score = float(score_match.group(1)) if score_match else -1.0

    # Parse per-rubric scores and log full parsed state before any override
    completeness_score = _parse_rubric_score(reflection_text, "COMPLETENESS")
    specificity_score = _parse_rubric_score(reflection_text, "SPECIFICITY")
    consistency_score = _parse_rubric_score(reflection_text, "INTERNAL CONSISTENCY")
    implementability_score = _parse_rubric_score(reflection_text, "IMPLEMENTABILITY")
    gaps_count = len([l for l in requirement_gaps.splitlines() if l.strip()])

    log_event(
        **ctx, level="INFO", event_type="reflect_parsed",
        message="Reflector output parsed",
        overall_score=overall_score,
        completeness_score=completeness_score, specificity_score=specificity_score,
        internal_consistency_score=consistency_score, implementability_score=implementability_score,
        llm_verdict=verdict,
        llm_triage="RECOVERY" if "RECOVERY MODE" in triage_decision else "NORMAL",
        resolved_count=len(re.findall(r"[-•*]\s*RESOLVED:", reflection_text, re.IGNORECASE)),
        unresolved_count=len(re.findall(r"[-•*]\s*UNRESOLVED:", reflection_text, re.IGNORECASE)),
        gaps_count=gaps_count,
    )
    if overall_score < 0:
        log_event(**ctx, level="WARNING", event_type="reflect_parse_warning",
                  message="Failed to parse OVERALL SCORE", field="overall_score")
    for _rubric, _score in (
        ("COMPLETENESS", completeness_score), ("SPECIFICITY", specificity_score),
        ("INTERNAL CONSISTENCY", consistency_score), ("IMPLEMENTABILITY", implementability_score),
    ):
        if _score < 0:
            log_event(**ctx, level="WARNING", event_type="reflect_parse_warning",
                      message=f"Failed to parse {_rubric} score",
                      field=_rubric.lower().replace(" ", "_"))

    # Capture LLM values before any programmatic override
    llm_verdict = verdict
    llm_triage = triage_decision

    # Programmatic threshold enforcement — deterministic contract independent
    # of LLM prompt-following reliability. Only applies when score parsed.
    if overall_score >= 0.0:
        # Downgrade a spurious PASS if score is below the pass threshold.
        if verdict == "PASS" and overall_score < PASS_SCORE_THRESHOLD:
            log_event(
                **ctx, level="WARNING", event_type="reflect_override",
                message="Programmatic override: verdict PASS→REWORK (score below pass threshold)",
                field="verdict", llm_value="PASS", enforced_value="REWORK",
                overall_score=overall_score, threshold=PASS_SCORE_THRESHOLD,
            )
            verdict = "REWORK"
        # Force recovery mode if score is below the recovery threshold.
        if overall_score < RECOVERY_MODE_SCORE_THRESHOLD:
            enforced_triage = "TRIAGE: ENTER RECOVERY MODE"
            if triage_decision != enforced_triage:
                log_event(
                    **ctx, level="WARNING", event_type="reflect_override",
                    message="Programmatic override: triage→RECOVERY (score below recovery threshold)",
                    field="triage_decision", llm_value="NORMAL", enforced_value="RECOVERY",
                    overall_score=overall_score, threshold=RECOVERY_MODE_SCORE_THRESHOLD,
                )
            triage_decision = enforced_triage

    # Warn when REWORK has no requirement gaps to drive follow-up questions
    if verdict == "REWORK" and not requirement_gaps.strip():
        log_event(
            **ctx, level="WARNING", event_type="reflect_missing_gaps",
            message="REWORK verdict but no requirement gaps extracted — follow-up may be generic",
        )

    # Update counters
    prev_iteration = state.get("iteration", 0)
    current_recovery_count = state.get("recovery_mode_consecutive_count", 0)
    new_iteration = prev_iteration
    new_recovery_count = current_recovery_count

    if verdict == "PASS":
        # PASS always resets the recovery count regardless of triage output.
        new_recovery_count = 0
    else:
        new_iteration += 1
        if triage_decision == "TRIAGE: ENTER RECOVERY MODE":
            new_recovery_count = current_recovery_count + 1
        else:
            new_recovery_count = 0  # NORMAL ITERATION resets the streak

    # Log state mutations (only fields that actually changed)
    _changes: dict = {}
    if overall_score != state.get("overall_score", -1.0):
        _changes["overall_score"] = f"{state.get('overall_score', -1.0):.1f} -> {overall_score:.1f}"
    if new_iteration != prev_iteration:
        _changes["iteration_change"] = f"{prev_iteration} -> {new_iteration}"
    if new_recovery_count != current_recovery_count:
        _changes["recovery_mode_consecutive_count"] = f"{current_recovery_count} -> {new_recovery_count}"
    if llm_verdict != verdict:
        _changes["verdict_override"] = f"{llm_verdict} -> {verdict}"
    if llm_triage != triage_decision:
        _changes["triage_override"] = (
            f"{'RECOVERY' if 'RECOVERY MODE' in llm_triage else 'NORMAL'}"
            f" -> {'RECOVERY' if 'RECOVERY MODE' in triage_decision else 'NORMAL'}"
        )
    if _changes:
        log_event(**ctx, level="INFO", event_type="state_update",
                  message="State mutations in reflect_node", **_changes)

    duration_ms = int((time.monotonic() - t0) * 1000)
    log_event(
        **ctx, level="INFO", event_type="node_end",
        message="reflect_node finished",
        duration_ms=duration_ms, overall_score=overall_score,
        llm_verdict=llm_verdict, enforced_verdict=verdict,
        enforced_triage="RECOVERY" if "RECOVERY MODE" in triage_decision else "NORMAL",
        new_iteration=new_iteration, new_recovery_count=new_recovery_count,
    )

    return {
        "reflection": reflection_text,
        "verdict": verdict,
        "triage_decision": triage_decision,
        "requirement_gaps": requirement_gaps,
        "overall_score": overall_score,
        "iteration": new_iteration,
        "recovery_mode_consecutive_count": new_recovery_count,
        "chat_history": [
            {
                "role": "assistant",
                "type": "reflect",
                "section": section.title,
                "verdict": verdict,
                "triage": triage_decision,
                "overall_score": overall_score,
                "content": reflection_text,
                "qa_pairs": list(state.get("section_qa_pairs", [])),
                "requirement_gaps": requirement_gaps,
                "rubric_scores": {
                    "completeness": completeness_score,
                    "specificity": specificity_score,
                    "consistency": consistency_score,
                    "implementability": implementability_score,
                    "overall": overall_score,
                },
            }
        ],
    }


# ── Node: advance_section ─────────────────────────────────────────────────────

def advance_section_node(state: PRDState) -> dict:
    """
    Saves the approved draft, resets per-section state, and advances the
    section index. Sets is_complete when all sections are done.
    """
    ctx = _log_ctx(state, "advance_section_node")
    t0 = time.monotonic()
    section = get_section_by_index(state["section_index"])
    next_index = state["section_index"] + 1
    is_complete = next_index >= len(PRD_SECTIONS)

    # Determine why this node was reached (PASS vs forced by cap)
    verdict = state.get("verdict", "")
    iterations_used = state.get("iteration", 0)
    recovery_count = state.get("recovery_mode_consecutive_count", 0)
    final_score = state.get("overall_score", -1.0)
    next_section_name = PRD_SECTIONS[next_index].title if not is_complete else "END"

    if verdict == "PASS":
        advance_event = "advance_section_pass"
        advance_reason = "PASS"
    elif recovery_count >= DEFAULT_MAX_RECOVERY_MODE_CONSECUTIVE_ITERATIONS:
        advance_event = "advance_section_forced_recovery_cap"
        advance_reason = "RECOVERY_CAP"
    else:
        advance_event = "advance_section_forced_iter_cap"
        advance_reason = "ITER_CAP"

    log_event(
        **ctx, level="INFO", event_type="node_start",
        message="advance_section_node started", advance_reason=advance_reason,
    )
    log_event(
        **ctx, level="INFO", event_type=advance_event,
        message=f"Section advancing: {advance_reason}",
        section_saved=section.title, final_score=final_score, final_verdict=verdict,
        iterations_used=iterations_used, recovery_count=recovery_count,
        next_section=next_section_name, is_complete=is_complete,
    )
    if advance_reason != "PASS":
        log_event(
            **ctx, level="WARNING", event_type="forced_progression",
            message=f"Section forced forward without PASS: reason={advance_reason}",
            section_saved=section.title, final_score=final_score, advance_reason=advance_reason,
        )

    msg = f"✅ **{section.title}** completed!"
    if not is_complete:
        next_section = get_section_by_index(next_index)
        msg += f" Moving to **{next_section.title}**…"

    duration_ms = int((time.monotonic() - t0) * 1000)
    log_event(
        **ctx, level="INFO", event_type="node_end",
        message="advance_section_node finished",
        duration_ms=duration_ms, advance_reason=advance_reason,
        next_section=next_section_name, is_complete=is_complete,
    )

    return {
        # Persist the approved draft (merge reducer adds it to the dict)
        "prd_sections": {section.id: state["current_draft"]},
        # Advance navigation
        "section_index": next_index,
        "is_complete": is_complete,
        # Reset per-section state
        "iteration": 0,
        "verdict": "",
        "reflection": "",
        "current_draft": "",
        "current_questions": "",
        "section_qa_pairs": [],
        "requirement_gaps": "",
        "triage_decision": "",
        "recovery_mode_consecutive_count": 0,
        "overall_score": -1.0,
        "chat_history": [
            {
                "role": "assistant",
                "type": "advance",
                "section": section.title,
                "content": msg,
            }
        ],
    }


# ── Node: finalize ────────────────────────────────────────────────────────────

def finalize_node(state: PRDState) -> dict:
    """
    Compiles all approved section drafts into a single Markdown PRD document.
    """
    ctx = _log_ctx(state, "finalize_node")
    t0 = time.monotonic()
    sections_completed = len(state.get("prd_sections", {}))
    log_event(
        **ctx, level="INFO", event_type="node_start",
        message="finalize_node started",
        sections_completed=sections_completed, total_sections=len(PRD_SECTIONS),
    )

    from datetime import date

    lines = [
        "# Product Requirements Document",
        f"_Generated: {date.today().isoformat()}_",
        "",
    ]

    for section in PRD_SECTIONS:
        if section.id in state.get("prd_sections", {}):
            lines += [
                f"## {section.title}",
                "",
                state["prd_sections"][section.id],
                "",
            ]

    prd_markdown = "\n".join(lines)

    duration_ms = int((time.monotonic() - t0) * 1000)
    log_event(
        **ctx, level="INFO", event_type="node_end",
        message="finalize_node finished — PRD generation complete",
        duration_ms=duration_ms, sections_completed=sections_completed,
        total_sections=len(PRD_SECTIONS), prd_len=len(prd_markdown),
    )

    return {
        "prd_markdown": prd_markdown,
        "chat_history": [
            {
                "role": "assistant",
                "type": "complete",
                "content": (
                    "🎉 **Your PRD is complete!** All sections have been reviewed "
                    "and approved. Download it using the button in the sidebar."
                ),
            }
        ],
    }
