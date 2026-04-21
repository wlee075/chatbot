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
    elif "TRIAGE: STALE_DRAFT_REGEN" in triage:
        route, reason = "draft", "STALE_REGEN"
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


def route_after_draft(state: PRDState) -> str:
    """
    Only run reflect after a real draft write.
    Skip straight back to question generation when draft_node determined there
    was no material new value.
    """
    mode = state.get("draft_execution_mode", "drafted")
    route = "reflect" if mode == "drafted" else "generate_questions"
    log_event(
        thread_id=state.get("thread_id", ""),
        run_id=state.get("run_id", ""),
        node_name="route_after_draft",
        section_name=(PRD_SECTIONS[state.get("section_index", 0)].title if 0 <= state.get("section_index", 0) < len(PRD_SECTIONS) else ""),
        section_index=state.get("section_index", 0),
        iteration=state.get("iteration", 0),
        level="INFO",
        event_type="routing_decision",
        message=f"Routing after draft: {mode} → {route}",
        route=route,
        reason=mode,
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


def route_after_framing(state: PRDState) -> str:
    """
    Path 1 (clear framing) → skip discovery, go straight to section elicitation.
    Path 2/3 (symptom / confused) → enter discovery loop.
    """
    if state.get("phase") == "elicitation":
        return "generate_questions"
    return "discovery_questions"


def route_after_discovery(state: PRDState) -> str:
    """
    Exit discovery to elicitation once 2 turns have completed
    or if framing was reclassified to 'clear' mid-discovery.
    """
    if state.get("discovery_turn_count", 0) >= 2 or state.get("framing_mode") == "clear":
        return "generate_questions"
    return "discovery_questions"


def route_after_confirmation(state: PRDState) -> str:
    """
    CONFIRMED → proceed to draft.
    CORRECTED → route back to await_answer so the user can re-answer
                the same question (current_questions is still in state).
    """
    if state.get("answer_confirmation_status") == "CONFIRMED":
        return "draft"
    return "await_answer"


def route_after_numeric_validation(state: PRDState) -> str:
    if state.get("validation_flag"):
        return "handle_numeric_error"
    return "intent_classifier"

def route_after_answer(state: PRDState) -> str:
    """
    Standard events (ANSWER, REPLY_TO_MESSAGE) → enter numeric check gate.
    Tagged events (TAG_MESSAGE_AS_TRUTH, CORRECT_MESSAGE) → handle directly,
      bypassing the validation gate since the UI already identified the exact message.
    """
    event_type = state.get("pending_event", {}).get("event_type", "ANSWER")
    if event_type in ("TAG_MESSAGE_AS_TRUTH", "CORRECT_MESSAGE"):
        return "handle_tagged_event"
    return "numeric_validation"


def route_after_intent(state: PRDState) -> str:
    reply_intent = state.get("reply_intent")
    if reply_intent in ("DIRECT_CLARIFICATION_QUESTION", "UNCLEAR_META"):
        route = "answer_clarification"
    elif reply_intent in ("REPHRASE_REQUEST", "AMBIGUOUS"):
        route = "rephrase_question"
    elif reply_intent in ("REPETITION_COMPLAINT", "COMPLAINT_OR_META"):
        route = "repair_repeated_question"
    elif reply_intent == "NUMERIC_ERROR":
        route = "handle_numeric_error"
    else:
        route = "option_resolution"
        
    log_event(
        thread_id=state.get("thread_id", ""),
        run_id=state.get("run_id", ""),
        node_name="route_after_intent",
        level="INFO",
        event_type="route_after_intent_decision",
        message=f"Routing after intent: {reply_intent} → {route}",
        reply_intent=reply_intent,
        chosen_route=route,
        reason=reply_intent,
        response_mode=state.get("response_mode", "")
    )
    return route

def route_after_contradiction(state: PRDState) -> str:
    if state.get("has_conflicts"):
        route = "generate_questions" 
    else:
        route = "truth_commit"
        
    log_event(
        thread_id=state.get("thread_id", ""),
        run_id=state.get("run_id", ""),
        node_name="route_after_contradiction",
        level="INFO",
        event_type="contradiction_route_taken",
        message=f"Routing after contradiction: conflicts? {state.get('has_conflicts')} → {route}",
        from_node="contradiction_validator",
        to_node=route
    )
    return route

