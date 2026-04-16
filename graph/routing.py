from config.sections import PRD_SECTIONS
from graph.state import PRDState
from prompts.templates import (
    DEFAULT_MAX_RECOVERY_MODE_CONSECUTIVE_ITERATIONS,
    DEFAULT_MAX_SECTION_ITERATIONS,
)


def route_after_reflect(state: PRDState) -> str:
    """
    PASS  → save draft and advance to the next section.
    REWORK → evaluated in priority order:
      1. Recovery mode cap hit (consecutive ENTER RECOVERY MODE) → advance
      2. Total iteration cap hit                                 → advance
      3. Otherwise                                               → loop back
    """
    if state.get("verdict") == "PASS":
        return "advance_section"

    # Recovery mode cap: consecutive ENTER RECOVERY MODE verdicts signal the
    # section has fundamental gaps the loop cannot resolve.
    if (
        state.get("recovery_mode_consecutive_count", 0)
        >= DEFAULT_MAX_RECOVERY_MODE_CONSECUTIVE_ITERATIONS
    ):
        return "advance_section"

    # Total iteration cap: hard ceiling regardless of triage state.
    if state.get("iteration", 0) >= state.get(
        "max_iterations", DEFAULT_MAX_SECTION_ITERATIONS
    ):
        return "advance_section"

    return "generate_questions"


def route_after_advance(state: PRDState) -> str:
    """
    When all sections are done, move to finalize.
    Otherwise, start the next section's elicitation loop.
    """
    if state.get("is_complete"):
        return "finalize"
    return "generate_questions"
