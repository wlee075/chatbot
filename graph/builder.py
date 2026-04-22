from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from graph.nodes import (
    advance_section_node,
    answer_clarification_node,
    await_answer_node,
    await_discovery_answer_node,
    await_first_message_node,
    detect_framing_node,
    detect_impact_node,
    discovery_questions_node,
    draft_node,
    finalize_node,
    generate_questions_node,
    handle_tagged_event_node,
    rebuild_mirror_node,
    reflect_node,
    terminal_session_node,
)
from graph.split_nodes import (
    multimodal_answer_materialization_node,
    numeric_validation_node,
    intent_classifier_node,
    target_context_selector_node,
    clarification_router_node,
    repair_mode_node,
    option_resolution_node,
    semantic_assessor_node,
    blocker_transition_node,
    contradiction_validator_node,
    truth_eligibility_node,
    truth_commit_node,
    concept_history_update_node,
    echo_generation_node,
    state_cleanup_node,
    handle_numeric_error_node,
    file_upload_intake_node,
    file_upload_rejection_node,
    uploaded_image_description_node,
    image_description_session_context_node,
)
from graph.routing import (
    route_after_draft,
    route_after_advance,
    route_after_answer,
    route_after_first_message,
    route_after_numeric_validation,
    route_after_discovery,
    route_after_intent,
    route_after_contradiction,
    route_after_framing,
    route_after_reflect,
    route_after_file_intake,
    route_after_multimodal_call,
    route_after_session_context_node,
)
from graph.state import PRDState


def build_graph(checkpointer: MemorySaver | None = None):
    """
    Constructs and compiles the PRD chatbot LangGraph.

    Graph topology:

        START
          │
        await_first_message   ← INTERRUPT: capture first user message (no welcome)
          │
        detect_framing        ← LLM: classify CLEAR | SYMPTOM | CONFUSED
          │
      ┌───┴────────────────────────────────┐
      │ phase=="elicitation"               │ phase=="discovery"
      ▼                                    ▼
    generate_questions            discovery_questions   (turn 1/2)
      │                                    │
    await_answer ←──────────────  await_discovery_answer
      │            route_after_discovery: exit after 2 turns → generate_questions
      │
    interpret_and_echo    ← LLM restatement; auto-confirms + commits to store
      │
    detect_impact → draft → reflect → ...
    """
    builder = StateGraph(PRDState)

    # ── Discovery path (Path 2 / 3) ───────────────────────────────────────────
    builder.add_node("await_first_message", await_first_message_node)
    builder.add_node("detect_framing", detect_framing_node)
    builder.add_node("discovery_questions", discovery_questions_node)
    builder.add_node("await_discovery_answer", await_discovery_answer_node)

    # ── Elicitation path (all paths eventually) ───────────────────────────────
    builder.add_node("rebuild_mirror", rebuild_mirror_node)
    builder.add_node("generate_questions", generate_questions_node)
    builder.add_node("await_answer", await_answer_node)
    builder.add_node("handle_tagged_event", handle_tagged_event_node)
    builder.add_node("numeric_validation", numeric_validation_node)
    builder.add_node("intent_classifier", intent_classifier_node)
    builder.add_node("target_context_selector", target_context_selector_node)
    builder.add_node("multimodal_answer_materialization", multimodal_answer_materialization_node)
    builder.add_node("clarification_router", clarification_router_node)
    builder.add_node("repair_mode", repair_mode_node)
    builder.add_node("option_resolution", option_resolution_node)
    builder.add_node("semantic_assessor", semantic_assessor_node)
    builder.add_node("blocker_transition", blocker_transition_node)
    builder.add_node("contradiction_validator", contradiction_validator_node)
    builder.add_node("truth_eligibility", truth_eligibility_node)
    builder.add_node("truth_commit", truth_commit_node)
    builder.add_node("concept_history_update", concept_history_update_node)
    builder.add_node("echo_generation", echo_generation_node)
    builder.add_node("state_cleanup", state_cleanup_node)
    builder.add_node("handle_numeric_error", handle_numeric_error_node)
    builder.add_node("file_upload_intake", file_upload_intake_node)
    builder.add_node("file_upload_rejection", file_upload_rejection_node)
    builder.add_node("uploaded_image_description", uploaded_image_description_node)
    builder.add_node("image_description_session_context", image_description_session_context_node)
    
    builder.add_node("answer_clarification", answer_clarification_node)
    builder.add_node("detect_impact", detect_impact_node)
    builder.add_node("draft", draft_node)
    builder.add_node("reflect", reflect_node)
    builder.add_node("advance_section", advance_section_node)
    builder.add_node("terminal_session", terminal_session_node)
    builder.add_node("finalize", finalize_node)

    # ── Edges ─────────────────────────────────────────────────────────────────
    builder.add_edge(START, "await_first_message")
    
    builder.add_conditional_edges(
        "await_first_message",
        route_after_first_message,
        {
            "detect_framing": "detect_framing",
            "file_upload_intake": "file_upload_intake",
            "image_description_session_context": "image_description_session_context",
            "terminal_session": "terminal_session",
            "handle_tagged_event": "handle_tagged_event",
        }
    )

    builder.add_conditional_edges(
        "detect_framing",
        route_after_framing,
        {
            "generate_questions": "rebuild_mirror",
            "discovery_questions": "discovery_questions",
            "file_upload_intake": "file_upload_intake",
        },
    )

    builder.add_edge("discovery_questions", "await_discovery_answer")
    builder.add_conditional_edges(
        "await_discovery_answer",
        route_after_discovery,
        {
            "generate_questions": "rebuild_mirror",
            "discovery_questions": "discovery_questions",
            "file_upload_intake": "file_upload_intake",
            "image_description_session_context": "image_description_session_context",
            "terminal_session": "terminal_session",
            "await_discovery_answer": "await_discovery_answer",
            "handle_tagged_event": "handle_tagged_event",
        },
    )

    # Rebuild entry point
    builder.add_edge("rebuild_mirror", "generate_questions")

    # Confirmation gate: await_answer → (route) → interpret_and_echo | handle_tagged_event
    builder.add_edge("generate_questions", "await_answer")
    builder.add_conditional_edges(
        "await_answer",
        route_after_answer,
        {
            "numeric_validation": "numeric_validation",
            "handle_tagged_event": "handle_tagged_event",
            "file_upload_intake": "file_upload_intake",
            "image_description_session_context": "image_description_session_context",
            "terminal_session": "terminal_session",
            "await_answer": "await_answer",
        },
    )
    
    builder.add_conditional_edges(
        "file_upload_intake",
        route_after_file_intake,
        {
            "uploaded_image_description": "uploaded_image_description",
            "file_upload_rejection": "file_upload_rejection",
            "generate_questions": "rebuild_mirror",
            "discovery_questions": "discovery_questions",
            "numeric_validation": "numeric_validation",
            "handle_tagged_event": "handle_tagged_event",
        }
    )
    
    builder.add_conditional_edges(
        "uploaded_image_description",
        route_after_multimodal_call,
        {
            "image_description_session_context": "image_description_session_context",
            "detect_framing": "detect_framing",
            "generate_questions": "generate_questions",
            "discovery_questions": "discovery_questions"
        }
    )
    
    builder.add_conditional_edges(
        "image_description_session_context",
        route_after_session_context_node,
        {
            "await_answer": "await_answer",
            "await_first_message": "await_first_message",
            "await_discovery_answer": "await_discovery_answer",
            "handle_tagged_event": "handle_tagged_event",
            "numeric_validation": "numeric_validation",
            "detect_framing": "detect_framing",
            "generate_questions": "generate_questions",
            "discovery_questions": "discovery_questions"
        }
    )
    # The rejection node halts further logic traversal, routing users back into await nodes implicitly
    # Since it modifies stream history and terminates its immediate arc, it loops to whichever
    # next loop iteration catches state completion (usually an `await_answer` manually fired again).
    # Actually, we should route back to their previous active state wait block manually:
    # However we don't have enough state memory here to perfectly rewind without duplicating await edges. 
    # Just sending it to await_answer works to block progress until user remediates.
    builder.add_edge("file_upload_rejection", "await_answer")

    
    builder.add_conditional_edges(
        "numeric_validation",
        route_after_numeric_validation,
        {
            "handle_numeric_error": "handle_numeric_error",
            "intent_classifier": "intent_classifier",
        }
    )
    
    builder.add_edge("handle_tagged_event", "detect_impact")
    
    # Intent split routing
    builder.add_edge("intent_classifier", "target_context_selector")
    builder.add_edge("target_context_selector", "multimodal_answer_materialization")
    builder.add_edge("multimodal_answer_materialization", "clarification_router")
    
    builder.add_conditional_edges(
        "clarification_router",
        route_after_intent,
        {
            "option_resolution": "option_resolution",
            "answer_clarification": "answer_clarification",
            "repair_mode": "repair_mode",
            "handle_numeric_error": "handle_numeric_error"
        }
    )
    
    builder.add_edge("repair_mode", "generate_questions")
    builder.add_edge("handle_numeric_error", "await_answer")
    builder.add_edge("answer_clarification", "await_answer")
    
    builder.add_edge("option_resolution", "semantic_assessor")
    builder.add_edge("semantic_assessor", "blocker_transition")
    builder.add_edge("blocker_transition", "contradiction_validator")
    
    builder.add_edge("contradiction_validator", "truth_eligibility")
    builder.add_conditional_edges(
        "truth_eligibility",
        route_after_contradiction,
        {
            "truth_commit": "truth_commit",
            "generate_questions": "rebuild_mirror"
        }
    )
    
    builder.add_edge("truth_commit", "concept_history_update")
    builder.add_edge("concept_history_update", "echo_generation")
    builder.add_edge("echo_generation", "state_cleanup")
    builder.add_edge("state_cleanup", "detect_impact")
    
    builder.add_edge("detect_impact", "draft")
    
    # Clarification completed → return straight to waiting for answer to the NEW elicited question
    builder.add_edge("answer_clarification", "await_answer")

    builder.add_conditional_edges(
        "draft",
        route_after_draft,
        {
            "reflect": "reflect",
            "generate_questions": "rebuild_mirror",
        },
    )

    builder.add_conditional_edges(
        "reflect",
        route_after_reflect,
        {
            "advance_section": "advance_section",
            "generate_questions": "rebuild_mirror",
            "draft": "draft",
            "terminal_session": "terminal_session",
        },
    )

    builder.add_conditional_edges(
        "advance_section",
        route_after_advance,
        {
            "generate_questions": "rebuild_mirror",
            "finalize": "finalize",
        },
    )

    builder.add_edge("advance_section", "generate_questions")

    builder.add_edge("terminal_session", END)
    builder.add_edge("finalize", END)

    return builder.compile(checkpointer=checkpointer or MemorySaver())
