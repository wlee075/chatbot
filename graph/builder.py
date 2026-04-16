from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from graph.nodes import (
    advance_section_node,
    await_answer_node,
    draft_node,
    finalize_node,
    generate_questions_node,
    load_context_node,
    reflect_node,
)
from graph.routing import route_after_advance, route_after_reflect
from graph.state import PRDState


def build_graph(checkpointer: MemorySaver | None = None):
    """
    Constructs and compiles the PRD chatbot LangGraph.

    Graph topology (reflection pattern):

        START
          │
        load_context          ← emit welcome message
          │
        generate_questions    ← Elicitor: ask questions, add to chat_history
          │
        await_answer          ← INTERRUPT: wait for PM input via Streamlit
          │
        draft                 ← Drafter: synthesise Q&A into section prose
          │
        reflect               ← Reflector: score against 3 rubrics
          │
      ┌───┴────────────────────────────────────────┐
      │ PASS or max iterations hit                 │ REWORK (iterations remain)
      ▼                                            ▼
    advance_section                         generate_questions (loop back)
      │
      ├── more sections → generate_questions
      └── all done      → finalize → END
    """
    builder = StateGraph(PRDState)

    builder.add_node("load_context", load_context_node)
    builder.add_node("generate_questions", generate_questions_node)
    builder.add_node("await_answer", await_answer_node)
    builder.add_node("draft", draft_node)
    builder.add_node("reflect", reflect_node)
    builder.add_node("advance_section", advance_section_node)
    builder.add_node("finalize", finalize_node)

    builder.add_edge(START, "load_context")
    builder.add_edge("load_context", "generate_questions")
    builder.add_edge("generate_questions", "await_answer")
    builder.add_edge("await_answer", "draft")
    builder.add_edge("draft", "reflect")

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
