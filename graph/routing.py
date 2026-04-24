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
        if recovery_count >= DEFAULT_MAX_RECOVERY_MODE_CONSECUTIVE_ITERATIONS:
            route, reason = "terminal_session", "RECOVERY_CAP"
        elif iteration >= state.get("max_iterations", DEFAULT_MAX_SECTION_ITERATIONS):
            route, reason = "terminal_session", "ITER_CAP"
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
    was no material new value — UNLESS question generation already signaled
    section-complete (no_question_available), in which case route directly to
    advance_section. Reflect cannot evaluate a completed section with no draft
    and would always LOOP, creating an infinite cycle.
    """
    mode = state.get("draft_execution_mode", "drafted")
    gen_status = state.get("generation_status", "question_generated")

    if mode == "drafted":
        route = "reflect"
    elif gen_status == "no_question_available":
        # Section is complete but draft was skipped (no new material).
        # Route directly to advance_section — reflect has no draft to score
        # and would always return LOOP, creating:
        #   generate_questions → draft(skip) → reflect(LOOP) → generate_questions
        route = "advance_section"
    else:
        route = "generate_questions"

    log_event(
        thread_id=state.get("thread_id", ""),
        run_id=state.get("run_id", ""),
        node_name="route_after_draft",
        section_name=(PRD_SECTIONS[state.get("section_index", 0)].title if 0 <= state.get("section_index", 0) < len(PRD_SECTIONS) else ""),
        section_index=state.get("section_index", 0),
        iteration=state.get("iteration", 0),
        level="INFO",
        event_type="routing_decision",
        message=f"Routing after draft: {mode} (gen_status={gen_status}) → {route}",
        route=route,
        reason=mode,
        generation_status=gen_status,
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
    Intercept: if uploaded_files are populated, route to file_upload_intake.
    """
    if state.get("uploaded_files"):
        return "file_upload_intake"
        
    if state.get("phase") == "elicitation":
        return "generate_questions"
    return "discovery_questions"


def route_after_discovery(state: PRDState) -> str:
    """
    Exit discovery to elicitation once 2 turns have completed
    or if framing was reclassified to 'clear' mid-discovery.
    Intercept: if uploaded_files are populated, route to file_upload_intake.
    Intercept: if event_type is TERMINATE_SESSION, route to terminal session.
    """
    event_type = state.get("pending_event", {}).get("event_type")
    if event_type == "TERMINATE_SESSION":
        return "terminal_session"
    if event_type in ("SUBMIT_SESSION_CONTEXT", "REMOVE_SESSION_CONTEXT", "REVERT_SESSION_CONTEXT"):
        return "handle_tagged_event"
        
    if state.get("uploaded_files"):
        return "file_upload_intake"
        
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


def route_after_first_message(state: PRDState) -> str:
    """
    Routes from await_first_message based on intercepted structural payloads, 
    otherwise default advances to detect_framing for the user's primary conversational turn execution.
    """
    event_type = state.get("pending_event", {}).get("event_type", "ANSWER")
    if event_type == "TERMINATE_SESSION":
        return "terminal_session"
    if event_type in ("SUBMIT_SESSION_CONTEXT", "REMOVE_SESSION_CONTEXT", "REVERT_SESSION_CONTEXT"):
        return "handle_tagged_event"
        
    if state.get("uploaded_files"):
        return "file_upload_intake"
        
    return "detect_framing"


def route_after_numeric_validation(state: PRDState) -> str:
    if state.get("validation_flag"):
        return "handle_numeric_error"
    return "intent_classifier"

def route_after_answer(state: PRDState) -> str:
    """
    Standard events (ANSWER, REPLY_TO_MESSAGE) → answer_validity gate first.
    Tagged events (TAG_MESSAGE_AS_TRUTH, CORRECT_MESSAGE) → handle directly,
      bypassing the validation gate since the UI already identified the exact message.
    Intercept: if uploaded_files are populated, route to file_upload_intake.
    Intercept: if event_type is TERMINATE_SESSION, route to terminal session.
    """
    event_type = state.get("pending_event", {}).get("event_type", "ANSWER")
    if event_type == "TERMINATE_SESSION":
        return "terminal_session"
    if event_type in ("SUBMIT_SESSION_CONTEXT", "REMOVE_SESSION_CONTEXT", "REVERT_SESSION_CONTEXT"):
        return "handle_tagged_event"

    if state.get("uploaded_files"):
        return "file_upload_intake"

    if event_type in ("TAG_MESSAGE_AS_TRUTH", "CORRECT_MESSAGE"):
        return "handle_tagged_event"
    # All standard ANSWER / REPLY_TO_MESSAGE paths go through the unified NeMo gateway
    return "nemo_guardrails_gateway"


def route_after_nemo_guardrails(state: PRDState) -> str:
    """
    Reads gateway_route_to set by nemo_guardrails_gateway_node.

    Possible values:
      await_answer         — noise_input: user re-types (clarification already emitted)
      task_request_blocked — task_request: boundary acknowledgement, no mode switch
      answer_clarification — meta_request / off_topic
      contradiction_validator — contradiction
      numeric_validation   — valid_answer / partial_answer / user_correction / cross_section
    """
    route = state.get("gateway_route_to", "numeric_validation")
    message_class = state.get("message_class", "")

    log_event(
        thread_id=state.get("thread_id", ""),
        run_id=state.get("run_id", ""),
        node_name="route_after_nemo_guardrails",
        level="INFO",
        event_type="routing_decision",
        message=f"Routing after nemo_guardrails: {message_class} → {route}",
        route=route,
        message_class=message_class,
        guardrail_reason=state.get("guardrail_reason", ""),
        guardrail_source=state.get("guardrail_source", ""),
    )
    return route


def route_after_intent(state: PRDState) -> str:
    route = state.get("clarification_route_id", "option_resolution")
    reply_intent = state.get("reply_intent")
        
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


def route_after_file_intake(state: PRDState) -> str:
    """
    Routes after file upload validation. If downstream analysis is blocked,
    immediately push back to file_upload_rejection_node.
    Otherwise, we must resume the exact normal path that we intercepted.
    We determine the resumption route by checking the phase state properties similar to before.
    """
    if not state.get("downstream_analysis_allowed", True):
        return "file_upload_rejection"
        
    # Determine the original intended route that we intercepted
    has_images = any(f.get("file_type") in ("jpg", "png") for f in state.get("accepted_files", []))
    if has_images:
        return "uploaded_image_description"
        
    if state.get("pending_event") and state.get("pending_event", {}).get("event_type", "") in ("TAG_MESSAGE_AS_TRUTH", "CORRECT_MESSAGE"):
        return "handle_tagged_event"
    elif state.get("pending_event"):
        return "numeric_validation"
        
    # If not pending_event, it came from framing/discovery
    # Check if discovery is done or framing is clear
    if state.get("discovery_turn_count", 0) >= 2 or state.get("framing_mode") == "clear" or state.get("phase") == "elicitation":
        return "generate_questions"
        
    return "discovery_questions"

def route_after_multimodal_call(state: PRDState) -> str:
    """
    Called strictly after uploaded_image_description completes.
    Routes to context node to convert API outputs into a UI draft.
    """
    if state.get("image_description_status") == "described":
        return "image_description_session_context"
        
    # Provide a fallback if multimodal fails completely
    if not state.get("framing_mode"):
        return "detect_framing"
    return "generate_questions" if state.get("discovery_turn_count", 0) >= 2 else "discovery_questions"

def route_after_session_context_node(state: PRDState) -> str:
    """
    Called strictly after image_description_session_context_node finishes.
    This replaces the old halting review flow! Background generated contexts
    are appended inline, so we instantly resume parsing the conversational payload.
    """
    if state.get("pending_event") and state.get("pending_event", {}).get("event_type", "") in ("TAG_MESSAGE_AS_TRUTH", "CORRECT_MESSAGE"):
        return "handle_tagged_event"
        
    # If no framing_mode exists, this is the very first turn. Go to detect_framing.
    if not state.get("framing_mode"):
        return "detect_framing"
        
    elif state.get("pending_event"):
        return "numeric_validation"
    return "discovery_questions"

def route_after_generate_questions(state: PRDState) -> str:
    """
    If the question generator exhausted all candidates and has no active question,
    safely bypass the `await_answer` lock and proceed directly to `draft`.

    Defence-in-depth: if generation_status is no_question_available but the last
    question target stamp does not match the active section, log a warning.
    The mismatch clarification is already emitted by generate_questions_node itself
    before this router is reached, so this log is observability-only.
    """
    status = state.get("generation_status", "question_generated")
    if status == "no_question_available":
        route = "draft"
        # ── question_target_section_consistency_guard: defensive log ──────────
        _last_target = state.get("last_question_target_section_id", "")
        _active_idx = state.get("section_index", 0)
        _active_id = (
            PRD_SECTIONS[_active_idx].id
            if 0 <= _active_idx < len(PRD_SECTIONS) else ""
        )
        _cross = state.get("cross_section_target")
        if _last_target and _last_target != _active_id and not _cross:
            log_event(
                thread_id=state.get("thread_id", ""),
                run_id=state.get("run_id", ""),
                node_name="route_after_generate_questions",
                level="WARNING",
                event_type="section_target_mismatch_observed",
                message=(
                    f"no_question_available reached router but last target "
                    f"'{_last_target}' != active section '{_active_id}'. "
                    f"Clarification should have been emitted by generate_questions_node."
                ),
                last_question_target_section_id=_last_target,
                active_section_id=_active_id,
            )
        # ── end guard ────────────────────────────────────────────────────────
    else:
        route = "await_answer"

    log_event(
        thread_id=state.get("thread_id", ""),
        run_id=state.get("run_id", ""),
        node_name="route_after_generate_questions",
        level="INFO",
        event_type="routing_decision",
        message=f"Routing after generate_questions: {status} → {route}",
        route=route,
        reason=status,
    )
    return route
