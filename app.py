import uuid

import streamlit as st
from dotenv import load_dotenv
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from config.sections import PRD_SECTIONS
from graph.builder import build_graph
from prompts.templates import DEFAULT_MAX_SECTION_ITERATIONS
from utils.doc_parser import parse_uploaded_file

load_dotenv()

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="PRD Builder",
    page_icon="📋",
    layout="wide",
)

# ── Session state initialisation ──────────────────────────────────────────────
if "thread_id" not in st.session_state:
    st.session_state.thread_id = str(uuid.uuid4())

if "graph_started" not in st.session_state:
    st.session_state.graph_started = False

if "context_doc" not in st.session_state:
    st.session_state.context_doc = ""


# ── Graph (cached once per process so MemorySaver persists across reruns) ─────
@st.cache_resource
def _get_graph():
    return build_graph(MemorySaver())


graph = _get_graph()


def _config() -> dict:
    return {"configurable": {"thread_id": st.session_state.thread_id}}


def _get_graph_state():
    try:
        return graph.get_state(_config())
    except Exception:
        return None


# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("📋 PRD Builder")
    st.caption("Claude · LangGraph · Reflection Pattern")
    st.divider()

    if not st.session_state.graph_started:
        # ── Pre-session: upload + start ──────────────────────────────────────
        st.subheader("Context Document")
        st.caption("Upload an existing brief, spec, or notes (optional).")
        uploaded = st.file_uploader(
            "Accepts PDF, TXT, MD",
            type=["pdf", "txt", "md"],
            label_visibility="collapsed",
        )
        if uploaded:
            st.session_state.context_doc = parse_uploaded_file(uploaded)
            st.success(f"✓ Loaded: {uploaded.name}")

        st.divider()

        if st.button("🚀 Start PRD Session", use_container_width=True, type="primary"):
            initial_state = {
                "context_doc": st.session_state.context_doc,
                "max_iterations": DEFAULT_MAX_SECTION_ITERATIONS,
                "section_index": 0,
                "iteration": 0,
                "current_questions": "",
                "section_qa_pairs": [],
                "current_draft": "",
                "reflection": "",
                "verdict": "",
                "requirement_gaps": "",
                "triage_decision": "",
                "recovery_mode_consecutive_count": 0,
                "overall_score": -1.0,
                "prd_sections": {},
                "chat_history": [],
                "prd_markdown": "",
                "is_complete": False,
            }
            with st.spinner("Starting session…"):
                graph.invoke(initial_state, _config())
            st.session_state.graph_started = True
            st.rerun()

    else:
        # ── In-session: progress tracker ─────────────────────────────────────
        gstate = _get_graph_state()
        sv = gstate.values if gstate else {}

        completed = set(sv.get("prd_sections", {}).keys())
        current_idx = sv.get("section_index", 0)
        iteration = sv.get("iteration", 0)
        max_iter = sv.get("max_iterations", 3)
        is_complete = sv.get("is_complete", False)

        st.subheader("Progress")
        for i, section in enumerate(PRD_SECTIONS):
            if section.id in completed:
                st.write(f"✅ {section.title}")
            elif i == current_idx and not is_complete:
                iter_label = (
                    f" _(iter {iteration + 1}/{max_iter})_" if iteration > 0 else ""
                )
                st.write(f"▶️ **{section.title}**{iter_label}")
            else:
                st.write(f"○ {section.title}")

        st.divider()

        # Download button appears once PRD is complete
        prd_md = sv.get("prd_markdown", "")
        if prd_md:
            st.download_button(
                label="⬇️ Download PRD (Markdown)",
                data=prd_md,
                file_name="product_requirements.md",
                mime="text/markdown",
                use_container_width=True,
                type="primary",
            )
            st.divider()

        if st.button("🔄 New Session", use_container_width=True):
            st.session_state.thread_id = str(uuid.uuid4())
            st.session_state.graph_started = False
            st.session_state.context_doc = ""
            st.rerun()


# ── Main content ──────────────────────────────────────────────────────────────
st.title("PRD Requirements Chatbot")

if not st.session_state.graph_started:
    # ── Landing page ──────────────────────────────────────────────────────────
    st.info("👈 Upload an optional context document and click **Start PRD Session**.")
    st.markdown(
        """
        ### How it works

        This chatbot guides you through a Product Requirements Document using the
        **reflection agentic pattern** — three specialised roles work in a loop
        until each section meets a quality bar:

        | Role | Responsibility |
        |---|---|
        | **Elicitor** | Asks targeted questions per PRD section |
        | **Drafter** | Synthesises your answers into section prose |
        | **Reflector** | Reviews the draft against 3 rubrics and emits PASS or REWORK |

        #### The 3 rubrics
        1. **Completeness** — all expected sub-components are addressed
        2. **Specificity** — claims are concrete and measurable (no vague language)
        3. **Internal Consistency** — no contradictions with prior sections

        On **REWORK**, the Elicitor asks sharper follow-up questions incorporating
        the Reflector's feedback. Max **3 iterations** per section before auto-advancing.

        At the end, download your complete PRD as a Markdown file.
        """
    )
    st.stop()

# ── Active session ─────────────────────────────────────────────────────────────
gstate = _get_graph_state()
if not gstate or not gstate.values:
    st.error("Session state not found. Please start a new session from the sidebar.")
    st.stop()

sv = gstate.values
chat_history: list[dict] = sv.get("chat_history", [])


# ── Render chat history ────────────────────────────────────────────────────────
for msg in chat_history:
    role = msg.get("role", "assistant")
    msg_type = msg.get("type", "")
    content = msg.get("content", "")

    if role == "user":
        with st.chat_message("user"):
            st.markdown(content)

    elif msg_type == "system":
        with st.chat_message("assistant"):
            st.markdown(content)

    elif msg_type == "elicit":
        with st.chat_message("assistant"):
            st.markdown(content)

    elif msg_type == "draft":
        with st.chat_message("assistant"):
            with st.expander("📝 View draft", expanded=False):
                st.markdown(content)

    elif msg_type == "reflect":
        verdict = msg.get("verdict", "REWORK")
        with st.chat_message("assistant"):
            if verdict == "PASS":
                st.success("**Review: PASSED ✅**")
                with st.expander("View review details", expanded=False):
                    st.markdown(content)
            else:
                st.warning("**Review: NEEDS REWORK ⚠️**")
                with st.expander("View feedback (read before answering)", expanded=True):
                    st.markdown(content)

    elif msg_type == "advance":
        with st.chat_message("assistant"):
            st.success(content)

    elif msg_type == "complete":
        with st.chat_message("assistant"):
            st.balloons()
            st.success(content)


# ── Chat input ─────────────────────────────────────────────────────────────────
is_waiting = bool(gstate.next) and not sv.get("is_complete", False)

if is_waiting:
    user_input = st.chat_input("Type your answer and press Enter…")
    if user_input:
        with st.spinner("Drafting and reviewing your answer… (this may take ~20s)"):
            try:
                graph.invoke(Command(resume=user_input), _config())
            except Exception as exc:
                st.error(f"Something went wrong: {exc}")
        st.rerun()

elif sv.get("is_complete", False):
    st.chat_input("Session complete — download your PRD from the sidebar.", disabled=True)
