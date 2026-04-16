from config.sections import PRD_SECTIONS
from graph.state import PRDState
from prompts.templates import (
    DEFAULT_MAX_RECOVERY_MODE_CONSECUTIVE_ITERATIONS,
    DEFAULT_MAX_SECTION_ITERATIONS,
)
from utils.logger import log_event


def route_after_reflect(state: PRDState) -> str:
    """
    PASS  → save draft and advance to the next section.
    REWORK → evaluated in priority order:
      1. Recovery mode cap hit (consecutive ENTER RECOVERY MODE) → advance
      2. Total iteration cap hit                                 → advance
      3. Otherwise                                               → loop back
    """
    section_idx = state.get("section_index", 0)
    section_name = (
        PRD_SECTIONS[section_idx].title if 0 <= section_idx < len(PRD_SECTIONS) else ""
    )
    verdict = state.get("verdict", "")
    triage = state.get("triage_decision", "")
    recovery_count = state.get("recovery_mode_consecutive_count", 0)
    iteration = state.get("iteration", 0)
    overall_score = state.get("overall_score", -1.0)

    if verdict == "PASS":
        route, reason = "advance_section", "PASS"
    elif recovery_count >= DEFAULT_MAX_RECOVERY_MODE_CONSECUTIVE_ITERATIONS:
        route, reason = "advance_section", "RECOVERY_CAP"
    elif iteration >= state.get("max_iterations", DEFAULT_MAX_SECTION_ITERATIONS):
        route, reason = "advance_section", "ITER_CAP"
    else:
        route, reason = "generate_questions", "LOOP"

    log_event(
        thread_id=state.get("thread_id", ""),
        run_id=state.get("run_id", ""),
        node_name="route_after_reflect",
        section_name=section_name,
        section_index=section_idx,
        iteration=iteration,
        level="INFO",
        event_type="routing_decision",
        message=f"Routing after reflect: {reason} → {route}",
        overall_score=overall_score,
        verdict=verdict,
        triage="RECOVERY" if "RECOVERY MODE" in triage else "NORMAL",
        recovery_mode_consecutive_count=recovery_count,
        route=route,
        reason=reason,
    )
    return route


def route_after_advance(state: PRDState) -> str:
    """
    When all sections are done, move to finalize.
    Otherwise, start the next section's elicitation loop.
    """
    if state.get("is_complete"):
        return "finalize"
    return "generate_questions"
