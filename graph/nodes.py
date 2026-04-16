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

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.types import interrupt

from config.sections import PRD_SECTIONS, get_section_by_index
from graph.state import PRDState
from prompts.templates import (
    DECISION_ENFORCEMENT_BLOCK,
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


# ── Node: load_context ────────────────────────────────────────────────────────

def load_context_node(state: PRDState) -> dict:
    """
    Passthrough — context doc is already in state.
    Emits a welcome message to seed the chat history.
    """
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

    # Programmatic threshold enforcement — deterministic contract independent
    # of LLM prompt-following reliability. Only applies when score parsed.
    if overall_score >= 0.0:
        # Downgrade a spurious PASS if score is below the pass threshold.
        if verdict == "PASS" and overall_score < PASS_SCORE_THRESHOLD:
            verdict = "REWORK"
        # Force recovery mode if score is below the recovery threshold.
        if overall_score < RECOVERY_MODE_SCORE_THRESHOLD:
            triage_decision = "TRIAGE: ENTER RECOVERY MODE"

    # Update counters
    new_iteration = state.get("iteration", 0)
    current_recovery_count = state.get("recovery_mode_consecutive_count", 0)

    if verdict == "PASS":
        # PASS always resets the recovery count regardless of triage output.
        new_recovery_count = 0
    else:
        new_iteration += 1
        if triage_decision == "TRIAGE: ENTER RECOVERY MODE":
            new_recovery_count = current_recovery_count + 1
        else:
            new_recovery_count = 0  # NORMAL ITERATION resets the streak

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
            }
        ],
    }


# ── Node: advance_section ─────────────────────────────────────────────────────

def advance_section_node(state: PRDState) -> dict:
    """
    Saves the approved draft, resets per-section state, and advances the
    section index. Sets is_complete when all sections are done.
    """
    section = get_section_by_index(state["section_index"])
    next_index = state["section_index"] + 1
    is_complete = next_index >= len(PRD_SECTIONS)

    msg = f"✅ **{section.title}** completed!"
    if not is_complete:
        next_section = get_section_by_index(next_index)
        msg += f" Moving to **{next_section.title}**…"

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
