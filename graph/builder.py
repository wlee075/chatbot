from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from graph.nodes import (
    advance_section_node,
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
    interpret_and_echo_node,
    reflect_node,
)
from graph.routing import (
    route_after_draft,
    route_after_advance,
    route_after_answer,
    route_after_discovery,
    route_after_framing,
    route_after_reflect,
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
    builder.add_node("generate_questions", generate_questions_node)
    builder.add_node("await_answer", await_answer_node)
    builder.add_node("handle_tagged_event", handle_tagged_event_node)
    builder.add_node("interpret_and_echo", interpret_and_echo_node)
    builder.add_node("detect_impact", detect_impact_node)
    builder.add_node("draft", draft_node)
    builder.add_node("reflect", reflect_node)
    builder.add_node("advance_section", advance_section_node)
    builder.add_node("finalize", finalize_node)

    # ── Edges ─────────────────────────────────────────────────────────────────
    builder.add_edge(START, "await_first_message")
    builder.add_edge("await_first_message", "detect_framing")

    builder.add_conditional_edges(
        "detect_framing",
        route_after_framing,
        {
            "generate_questions": "generate_questions",
            "discovery_questions": "discovery_questions",
        },
    )

    builder.add_edge("discovery_questions", "await_discovery_answer")
    builder.add_conditional_edges(
        "await_discovery_answer",
        route_after_discovery,
        {
            "generate_questions": "generate_questions",
            "discovery_questions": "discovery_questions",
        },
    )

    # Confirmation gate: await_answer → (route) → interpret_and_echo | handle_tagged_event
    builder.add_edge("generate_questions", "await_answer")
    builder.add_conditional_edges(
        "await_answer",
        route_after_answer,
        {
            "interpret_and_echo": "interpret_and_echo",
            "handle_tagged_event": "handle_tagged_event",
        },
    )
    builder.add_edge("handle_tagged_event", "detect_impact")
    builder.add_edge("interpret_and_echo", "detect_impact")
    builder.add_edge("detect_impact", "draft")

    builder.add_conditional_edges(
        "draft",
        route_after_draft,
        {
            "reflect": "reflect",
            "generate_questions": "generate_questions",
        },
    )

    builder.add_conditional_edges(
        "reflect",
        route_after_reflect,
        {
            "advance_section": "advance_section",
            "generate_questions": "generate_questions",
        },
    )

    builder.add_conditional_edges(
        "advance_section",
        route_after_advance,
        {
            "generate_questions": "generate_questions",
            "finalize": "finalize",
        },
    )

    builder.add_edge("finalize", END)

    return builder.compile(checkpointer=checkpointer or MemorySaver())
