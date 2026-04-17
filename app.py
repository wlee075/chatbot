import base64
import html
import os
import re
import time
import uuid

import streamlit as st
from dotenv import load_dotenv
from langchain_core.messages import AIMessageChunk, HumanMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from config.sections import PRD_SECTIONS
from graph.builder import build_graph
from prompts.templates import DEFAULT_MAX_SECTION_ITERATIONS

load_dotenv()

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="PRD Builder",
    page_icon="📋",
    layout="wide",
)

# ── Session state initialisation ──────────────────────────────────────────────
for _k, _v in {
    "graph_started": False,
    "last_elapsed_ms": None,
    "last_node_timings_ms": {},
    "image_context_buffer": [],
    "active_reference": None,
}.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v

if "thread_id" not in st.session_state:
    st.session_state.thread_id = str(uuid.uuid4())

# ── Inject citation link handler near the top ────────────────────────────────
_CITATION_HANDLER_JS = """
<script>
(function() {
  window._citationHandler = function(sourceId) {
    const elem = document.getElementById(sourceId);
    if (!elem) {
      console.warn('Source', sourceId, 'not found');
      return;
    }
    elem.scrollIntoView({ behavior: 'smooth', block: 'start' });
    const parent = elem.nextElementSibling;
    if (parent) {
      parent.style.transition = 'background-color 0.2s';
      parent.style.backgroundColor = '#fffacd';
      setTimeout(() => {
        parent.style.backgroundColor = '';
      }, 2500);
    }
  };
})();
</script>
"""

if "__citation_js_injected" not in st.session_state:
    st.session_state["__citation_js_injected"] = True
    st.markdown(_CITATION_HANDLER_JS, unsafe_allow_html=True)

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


# ── Gemini Vision helper (D-M3) ───────────────────────────────────────────────
_VISION_PROMPT = (
    "Describe this image in plain English. "
    "Focus only on elements relevant to a software product, business process, "
    "team structure, data, or user interface. "
    "If the image contains none of those (e.g. meme, selfie, pet photo) "
    "respond with exactly: IRRELEVANT"
)


def _describe_image(file_bytes: bytes, mime_type: str) -> str | None:
    """Call Gemini Vision. Returns plain-text description or None if irrelevant."""
    b64 = base64.standard_b64encode(file_bytes).decode()
    llm = ChatGoogleGenerativeAI(
        model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
        temperature=0,
    )
    response = llm.invoke([
        HumanMessage(content=[
            {"type": "text", "text": _VISION_PROMPT},
            {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{b64}"}},
        ])
    ])
    text = response.content.strip()
    return None if text.upper().startswith("IRRELEVANT") else text or None


# ── Initial state builder (D-M2) ──────────────────────────────────────────────
def _build_initial_state(image_context: list[str]) -> dict:
    return {
        "thread_id": st.session_state.thread_id,
        "run_id": str(uuid.uuid4()),
        "context_doc": "",
        "max_iterations": DEFAULT_MAX_SECTION_ITERATIONS,
        "phase": "discovery",
        "framing_mode": "",
        "discovery_turn_count": 0,
        "section_index": 0,
        "iteration": 0,
        "current_questions": "",
        "current_draft": "",
        "reflection": "",
        "technical_gaps": "",
        "user_gaps": "",
        "verdict": "",
        "requirement_gaps": "",
        "triage_decision": "",
        "recovery_mode_consecutive_count": 0,
        "overall_score": -1.0,
        "confidence": -1.0,
        "raw_answer_buffer": "",
        "pending_echo": "",
        "pending_concept_updates": {},
        "answer_confirmation_status": "",
        "pending_event": {},
        "event_history": [],
        "section_scores": {},
        "section_draft_meta": {},
        "draft_execution_mode": "",
        "impacted_sections": [],
        "last_section_updates": [],
        "confirmed_qa_store": {},
        "section_qa_pairs": [],
        "pending_interrupt_type": "question",
        "interrupt_queue": [],
        "image_context": image_context,
        "forward_hints": [],
        "contradiction_log": [],
        "tbd_fields": [],
        "prd_sections": {},
        "chat_history": [],
        "prd_markdown": "",
        "is_complete": False,
    }


# ── Placeholder detector ───────────────────────────────────────────────────────
_PLACEHOLDER_RE = re.compile(
    r"\b[XYZ]\b"
    r"(?:"
    r"\s*%"
    r"|"
    r"\s*(?:days?|hours?|weeks?|months?|years?|users?|items?|reports?|"
    r"requests?|seconds?|minutes?|ms|calls?|events?|cases?)"
    r"|(?<=[=><≥≤\s])\s*"
    r")",
    re.IGNORECASE,
)

_PLACEHOLDER_CONTEXT_RE = re.compile(
    r"(?:"
    r"\d+\s*(?:to|or|and|-)\s*[XYZ]"
    r"|[XYZ]\s*(?:to|or|and|-)\s*\d+"
    r"|(?:least|most|than|under|over|above|below|around|about|"
    r"approximately|~)\s+[XYZ]"
    r"|[XYZ]\s*(?:times|x)\b"
    r")",
    re.IGNORECASE,
)

_SOURCE_TAG_RE = re.compile(
    r"\[SOURCE:\s*concept_key=([^,\]]+),\s*round=(\d+)\]",
    re.IGNORECASE,
)

_INTERNAL_TERM_REPLACEMENTS: dict[str, str] = {
    "background_problem": "earlier business problem",
    "section consistency": "alignment",
    "Headliner paragraph": "Summary section",
    "stored under": "saved in your notes",
    "concept_key": "source reference",
}


def _display_section_title(raw: str) -> str:
    """Map internal section names to user-facing labels."""
    if not raw:
        return raw
    low = raw.strip().lower()
    if low in {"headliner", "headliner paragraph", "tldr", "tl;dr"}:
        return "Summary"
    return raw


def _build_source_message_lookup(chat_history: list[dict], store: dict) -> dict[str, str]:
    """Best-effort mapping from concept_key to user message anchor id."""
    user_msgs: list[tuple[int, str]] = [
        (idx, (m.get("content", "") or "").strip())
        for idx, m in enumerate(chat_history)
        if m.get("role") == "user"
    ]
    used: set[int] = set()
    out: dict[str, str] = {}
    for concept_key, payload in store.items():
        if not isinstance(payload, dict):
            continue
        answer = (payload.get("answer", "") or "").strip().lower()
        if not answer:
            continue
        best_idx = None
        for idx, msg_text in user_msgs:
            if idx in used:
                continue
            low_msg = msg_text.lower()
            if answer in low_msg or low_msg in answer:
                best_idx = idx
                break
        if best_idx is not None:
            used.add(best_idx)
            out[concept_key] = f"msg_{best_idx}"
    return out


# ── Inject citation click handler + hover tooltip (once per session) ──────────────────────────
_CITATION_HANDLER_JS = """
<style>
.cite-chip {
  position: relative;
  display: inline-block;
  cursor: pointer;
  text-decoration: underline dotted;
  color: #0066cc;
  border-radius: 4px;
  padding: 0 2px;
  transition: background-color 0.15s;
}

.cite-chip:hover {
  background-color: #e8f0ff;
}

.cite-tooltip {
  position: absolute;
  bottom: 125%;
  left: 50%;
  transform: translateX(-50%);
  background-color: #1a1a2e;
  color: white;
  padding: 12px 14px;
  border-radius: 6px;
  font-size: 0.85rem;
  line-height: 1.5;
  max-width: 280px;
  white-space: pre-wrap;
  word-wrap: break-word;
  z-index: 10000;
  opacity: 0;
  pointer-events: none;
  transition: opacity 0.2s;
  box-shadow: 0 4px 12px rgba(0,0,0,0.3);
}

.cite-tooltip::after {
  content: '';
  position: absolute;
  top: 100%;
  left: 50%;
  transform: translateX(-50%);
  border: 6px solid transparent;
  border-top-color: #1a1a2e;
}

.cite-chip:hover .cite-tooltip {
  opacity: 1;
  pointer-events: auto;
}
</style>

<script>
(function() {
  window._citationHandler = function(sourceId) {
    const elem = document.getElementById(sourceId);
    if (!elem) {
      console.warn('Source', sourceId, 'not found');
      return;
    }
    elem.scrollIntoView({ behavior: 'smooth', block: 'start' });
    const parent = elem.nextElementSibling;
    if (parent) {
      parent.style.transition = 'background-color 0.2s';
      parent.style.backgroundColor = '#fffacd';
      setTimeout(() => {
        parent.style.backgroundColor = '';
      }, 2500);
    }
  };
})();
</script>
"""

def _present_content(content: str, source_lookup: dict[str, str] | None = None, answer_store: dict | None = None) -> str:
    """
    User-facing text cleanup: hide internal terms and render source chips
    as clickable citations with hover tooltips showing user answers.
    
    Args:
        content: Text with [SOURCE: concept_key=..., round=N] tags
        source_lookup: Maps concept_key → message_id for click navigation
        answer_store: Maps concept_key → {"answer": str, ...} for hover display
    """
    if not content:
        return content

    text = content
    
    # ── FIRST: Process SOURCE tags with regex (before term replacement breaks the pattern) ──
    def _source_repl(match: re.Match) -> str:
        concept_key = match.group(1).strip()
        round_n = match.group(2)
        target = source_lookup.get(concept_key) if source_lookup else None
        
        # Get answer text for tooltip if available
        answer_text = ""
        if answer_store and concept_key in answer_store:
            payload = answer_store[concept_key]
            if isinstance(payload, dict):
                answer_text = (payload.get("answer", "") or "").strip()
        
        # Truncate answer to fit in tooltip (max 200 chars)
        if len(answer_text) > 200:
            answer_text = answer_text[:197] + "…"
        
        # Escape single quotes for HTML attribute
        answer_text_escaped = html.escape(answer_text)
        
        if target:
            # Render with hover tooltip showing the answer
            tooltip_html = f"<div class='cite-tooltip'>{answer_text_escaped}</div>" if answer_text else ""
            return f"<span class='cite-chip' onclick=\"if(window._citationHandler)window._citationHandler('{target}');\">{tooltip_html}your earlier answer ↗</span>"
        return f"<span class='cite-fallback'>(round {round_n})</span>"

    text = _SOURCE_TAG_RE.sub(_source_repl, text)
    
    # ── THEN: Apply generic term replacements to remaining text ──
    text = text.replace("Headliner", "Summary")
    text = text.replace("Headliner paragraph", "Summary section")
    for bad, good in _INTERNAL_TERM_REPLACEMENTS.items():
        text = text.replace(bad, good)
    text = re.sub(r"round\s*=\s*\d+", "earlier answer", text, flags=re.IGNORECASE)

    return text


def _find_placeholders(text: str) -> list[str]:
    found = []
    for pattern in (_PLACEHOLDER_RE, _PLACEHOLDER_CONTEXT_RE):
        for m in pattern.finditer(text):
            snippet = text[max(0, m.start() - 8):m.end() + 8].strip()
            found.append(f"…{snippet}…")
    return found


def _extract_reflection_items(reflection_text: str, label: str) -> list[str]:
    pattern = re.compile(
        rf"^\s*[-•*]?\s*{re.escape(label)}:\s*(.+?)\s*$",
        re.IGNORECASE | re.MULTILINE,
    )
    return [match.group(1).strip() for match in pattern.finditer(reflection_text or "")]


def _extract_verdict_reason(reflection_text: str) -> str:
    match = re.search(r"VERDICT:\s*REWORK\s*-\s*(.+)$", reflection_text or "", re.IGNORECASE | re.MULTILINE)
    if match:
        return match.group(1).strip().rstrip(".") + "."
    return ""


def _review_quality_checks(msg: dict) -> list[str]:
    rubric_scores = msg.get("rubric_scores", {}) or {}
    checks = [
        ("Coverage check passed", "completeness"),
        ("Specificity check passed", "specificity"),
        ("Consistency check passed", "consistency"),
        ("Implementation-readiness check passed", "implementability"),
    ]
    passed: list[str] = []
    for label, key in checks:
        score = rubric_scores.get(key, -1)
        if isinstance(score, (int, float)) and score >= 8.5:
            passed.append(label)
    return passed


def _minor_wording_notes(msg: dict) -> list[str]:
    user_gaps = (msg.get("user_gaps") or msg.get("requirement_gaps") or "").splitlines()
    notes: list[str] = []
    for line in user_gaps:
        stripped = line.strip().lstrip("-•*0123456789. \t")
        if not stripped:
            continue
        if re.search(r"wording|phrase|phrasing|tone|label|terminology|rename", stripped, re.IGNORECASE):
            notes.append(stripped)
    return notes[:3]


def _build_review_summary(msg: dict) -> dict[str, object]:
    reflection_text = msg.get("content", "") or ""
    strengths = _extract_reflection_items(reflection_text, "RESOLVED")[:3]
    open_items = _extract_reflection_items(reflection_text, "UNRESOLVED")

    improvement = ""
    user_gaps = (msg.get("user_gaps") or msg.get("requirement_gaps") or "").splitlines()
    for line in user_gaps:
        stripped = line.strip().lstrip("-•*0123456789. \t")
        if stripped:
            improvement = stripped
            break

    if not improvement and open_items:
        improvement = open_items[0]

    verdict = (msg.get("verdict") or "REWORK").upper()
    verdict_reason = _extract_verdict_reason(reflection_text)

    if verdict == "PASS":
        status = "Approved"
        explanation = verdict_reason or "This section is clear enough to move forward."
        next_step = "Move on to the next section."
    else:
        status = "Needs one improvement"
        explanation = verdict_reason or improvement or "One important decision still needs clarification."
        next_step = improvement or "Clarify the open decision, then review the section again."

    if not strengths:
        strengths = _review_quality_checks(msg)[:3]

    return {
        "status": status,
        "explanation": explanation,
        "strengths": strengths[:3],
        "improvement": improvement,
        "next_step": next_step,
        "quality_checks": _review_quality_checks(msg),
        "minor_wording_notes": _minor_wording_notes(msg),
        "confidence": msg.get("confidence", -1.0),
    }


_SHOW_INTERNAL_REVIEW_DEBUG = os.getenv("SHOW_INTERNAL_REVIEW_DEBUG") == "1"


# ── Streaming helper (Step 7) ─────────────────────────────────────────────────
_STREAMING_NODES = frozenset({"generate_questions", "discovery_questions", "interpret_and_echo"})

# ── Progress status system ─────────────────────────────────────────────────────
# Maps internal node names to (first-pass label, repeat label).
# Repeat label is shown when the same node fires more than once in a single turn
# (draft→reflect→draft loop), so users see progress not a stuck spinner.
_NODE_STATUS: dict[str, tuple[str, str]] = {
    "detect_framing":       ("Understanding what you're building…",   "Re-reading your context…"),
    "detect_impact":        ("Checking which sections are affected…",  "Re-checking section impact…"),
    "draft":                ("Writing your draft…",                    "Improving the draft…"),
    "reflect":              ("Checking for gaps…",                     "Checking remaining gaps…"),
    "advance_section":      ("Moving to the next topic…",              "Moving on…"),
    "handle_tagged_event":  ("Processing your selection…",             "Processing your selection…"),
    "generate_questions":   ("Preparing the next question…",           "Preparing a sharper follow-up…"),
    "discovery_questions":  ("Getting to know your product…",          "Exploring further…"),
    "interpret_and_echo":   ("Making sense of your answer…",           "Re-interpreting your answer…"),
}

# CSS injected once per session for the status card
_STATUS_CARD_CSS = """
<style>
.prd-status-card {
    background: #f8f9fa;
    border-left: 3px solid #6c63ff;
    border-radius: 6px;
    padding: 10px 14px;
    margin-bottom: 8px;
    font-size: 0.88rem;
    color: #444;
    line-height: 1.7;
}
.prd-status-card .status-current {
    font-weight: 600;
    color: #1a1a2e;
}
.prd-status-card .status-done {
    color: #888;
}
</style>
"""

def _build_status_card(completed: list[str], current: str) -> str:
    """Render the status card HTML from completed steps + running step."""
    lines = [_STATUS_CARD_CSS, '<div class="prd-status-card">']
    for step in completed[-3:]:  # show at most 3 completed steps
        lines.append(f'<span class="status-done">✓ {step}</span><br>')
    lines.append(f'<span class="status-current">⟳ {current}</span>')
    lines.append("</div>")
    return "\n".join(lines)

# Human-readable chip labels for each UI action event type
_REFERENCE_LABELS: dict[str, str] = {
    "REPLY_TO_MESSAGE": "Replying to earlier message",
}


def _render_message_actions(msg_id: str, content: str, role: str, msg_type: str) -> None:
    """
    Renders a single Reply button on every chat message bubble.
    Clicking sets active_reference with REPLY_TO_MESSAGE event type.
    The graph interprets intent (correction / confirmation / reference / answer)
    from the reply content + referenced message.
    Only shown when a session is active and the graph is still waiting for input.
    """
    if not st.session_state.get("graph_started"):
        return

    gst = _get_graph_state()
    if not (gst and gst.next):
        return

    btn_col, _spacer = st.columns([1, 7], gap="small")
    with btn_col:
        if st.button(
            "↩ Reply",
            key=f"msgaction_{msg_id}",
            help="Reply to this message",
            use_container_width=True,
        ):
            st.session_state.active_reference = {
                "event_type": "REPLY_TO_MESSAGE",
                "target_message_id": msg_id,
                "label": _REFERENCE_LABELS["REPLY_TO_MESSAGE"],
                "target_content": content,
                "source_message_role": role,
            }
            st.rerun()


def _stream_graph_resume(payload: dict | str) -> None:
    """
    Runs one graph turn via Command(resume=payload), streaming Elicitor tokens
    live to the UI.
    Status card updates on every node transition (not gated on streaming state).
    stream_text resets per streaming node so echo and question never concatenate.
    """
    t0 = time.monotonic()
    node_visit_counts: dict[str, int] = {}
    completed_labels: list[str] = []
    last_node = ""
    last_streaming_node = ""
    stream_text = ""
    node_durations_ms: dict[str, int] = {}
    node_started_at = t0

    with st.chat_message("Agent", avatar="assistant"):
        status_slot = st.empty()
        stream_slot = st.empty()

    def _label_for(node: str) -> str:
        first, repeat = _NODE_STATUS.get(node, (None, None))
        if first is None:
            return ""
        count = node_visit_counts.get(node, 0)
        return repeat if count > 1 else first

    try:
        for chunk, metadata in graph.stream(
            Command(resume=payload),
            _config(),
            stream_mode="messages",
        ):
            node = metadata.get("langgraph_node", "")

            if node and node != last_node:
                now = time.monotonic()
                if last_node:
                    node_durations_ms[last_node] = node_durations_ms.get(last_node, 0) + int((now - node_started_at) * 1000)
                node_started_at = now

                # Archive finished node label
                if last_node:
                    prev_label = _label_for(last_node)
                    if prev_label and (not completed_labels or completed_labels[-1] != prev_label):
                        completed_labels.append(prev_label)
                node_visit_counts[node] = node_visit_counts.get(node, 0) + 1
                last_node = node

                # Reset stream text when entering a new streaming node so
                # echo text and question text don't concatenate into one blob
                if node in _STREAMING_NODES and node != last_streaming_node:
                    stream_text = ""
                    last_streaming_node = node
                    stream_slot.empty()

                # Always update status card on node change
                current_label = _label_for(node)
                if current_label:
                    status_slot.markdown(
                        _build_status_card(completed_labels, current_label),
                        unsafe_allow_html=True,
                    )

            if node in _STREAMING_NODES:
                if isinstance(chunk, AIMessageChunk) and chunk.content:
                    stream_text += chunk.content
                    stream_slot.markdown(stream_text + " ▌")

    except Exception as exc:
        status_slot.empty()
        stream_slot.empty()
        st.session_state.last_elapsed_ms = None
        st.session_state.last_node_timings_ms = {}
        st.error(f"Something went wrong: {exc}")
        return

    if last_node:
        node_durations_ms[last_node] = node_durations_ms.get(last_node, 0) + int((time.monotonic() - node_started_at) * 1000)

    status_slot.empty()
    stream_slot.empty()
    st.session_state.last_elapsed_ms = int((time.monotonic() - t0) * 1000)
    st.session_state.last_node_timings_ms = node_durations_ms
    st.rerun()
# Render
# ══════════════════════════════════════════════════════════════════════════════

# Resolve graph state once per rerun — avoids repeated calls below
gstate = _get_graph_state() if st.session_state.graph_started else None
sv: dict = gstate.values if gstate else {}

# ── Top bar (D-M1): visible only when session is active ───────────────────────
if st.session_state.graph_started:
    col_title, _sp, col_new, col_dl = st.columns([5, 2, 1, 1])
    with col_title:
        st.markdown("**PRD Builder**")
    with col_new:
        if st.button("↩ New", use_container_width=True):
            st.session_state.thread_id = str(uuid.uuid4())
            st.session_state.graph_started = False
            st.session_state.last_elapsed_ms = None
            st.session_state.last_node_timings_ms = {}
            st.session_state.image_context_buffer = []
            st.session_state.active_reference = None
            st.rerun()
    with col_dl:
        prd_md = sv.get("prd_markdown", "")
        if prd_md:
            st.download_button(
                label="⬇ PRD",
                data=prd_md,
                file_name="product_requirements.md",
                mime="text/markdown",
                use_container_width=True,
                type="primary",
            )
    st.divider()

    # ── PRD Progress panel ────────────────────────────────────────────────────
    # Sticky bar: section badges + active section name label.
    prd_sections_done = sv.get("prd_sections", {})
    section_scores_map = sv.get("section_scores", {})
    last_updates = sv.get("last_section_updates", [])
    current_section_id = (
        PRD_SECTIONS[sv.get("section_index", 0)].id
        if 0 <= sv.get("section_index", 0) < len(PRD_SECTIONS)
        else ""
    )
    current_section_title = next(
        (s.title for s in PRD_SECTIONS if s.id == current_section_id), ""
    )
    current_section_title = _display_section_title(current_section_title)

    # Sticky wrapper — container gives us a stVerticalBlock we can attach
    # position:sticky to via the .prd-sticky-bar marker + :has() selector.
    with st.container():
        st.markdown(
            """
            <style>
            div[data-testid="stVerticalBlock"]:has(> div[data-testid="stMarkdown"] .prd-sticky-bar) {
                position: sticky !important;
                top: 0 !important;
                z-index: 999 !important;
                background: var(--background-color, white) !important;
                padding-bottom: 4px;
            }
            </style>
            <div class="prd-sticky-bar" style="display:none"></div>
            """,
            unsafe_allow_html=True,
        )

        _badge_cols = st.columns(len(PRD_SECTIONS), gap="small")
        for _bi, _sec in enumerate(PRD_SECTIONS):
            with _badge_cols[_bi]:
                _score_entry = section_scores_map.get(_sec.id, {})
                _completeness = _score_entry.get("completeness", -1.0)
                _has_draft = _sec.id in prd_sections_done
                _recently_updated = _sec.id in last_updates

                if _sec.id == current_section_id:
                    _badge = "🟡"
                    _help = "Current section"
                elif _score_entry.get("verdict") == "PASS":
                    _badge = "✅"
                    _help = f"Done · score {_completeness:.0%}" if _completeness >= 0 else "Done"
                elif _has_draft and _recently_updated:
                    _badge = "🔄"
                    _help = f"Updated this turn"
                elif _has_draft:
                    _score_label = f" · {_completeness:.0%}" if _completeness >= 0 else ""
                    _badge = "📝"
                    _help = f"Drafted{_score_label}"
                else:
                    _badge = "○"
                    _help = "Not started"

                if _has_draft or _sec.id == current_section_id:
                    if st.button(
                        _badge,
                        key=f"prdbadge_{_sec.id}",
                        help=f"{_display_section_title(_sec.title)} — {_help}",
                        use_container_width=True,
                    ):
                        st.session_state[f"_preview_open_{_sec.id}"] = not st.session_state.get(
                            f"_preview_open_{_sec.id}", False
                        )
                        st.rerun()
                else:
                    st.markdown(f"<div style='text-align:center;color:#aaa'>{_badge}</div>", unsafe_allow_html=True)

        # Active section name shown beneath the badge row
        if current_section_title:
            st.caption(f"**{current_section_title}**")

    # Section preview expanders (open/closed state per section, toggled by badges)
    for _sec in PRD_SECTIONS:
        if st.session_state.get(f"_preview_open_{_sec.id}"):
            _draft_text = prd_sections_done.get(_sec.id, "")
            if _draft_text:
                with st.expander(f"📄 {_display_section_title(_sec.title)}", expanded=True):
                    st.markdown(_present_content(_draft_text, {}, {}), unsafe_allow_html=True)
                    if st.button("Close", key=f"_close_preview_{_sec.id}"):
                        st.session_state[f"_preview_open_{_sec.id}"] = False
                        st.rerun()

    st.divider()

# ── Landing heading (D-M1): visible only before session starts ─────────────────
if not st.session_state.graph_started:
    st.markdown(
        "<div style='text-align:center; margin-top:15vh;'>"
        "<h2>What are you building today?</h2>"
        "</div>",
        unsafe_allow_html=True,
    )

# ── Chat history: visible only when session is active ─────────────────────────
if st.session_state.graph_started:
    if not gstate or not sv:
        st.error("Session state not found. Click ↩ New to start over.")
        st.stop()

    chat_history: list[dict] = sv.get("chat_history", [])
    confirmed_qa_store = sv.get("confirmed_qa_store", {})
    source_lookup = _build_source_message_lookup(
        chat_history,
        confirmed_qa_store,
    )

    for idx, msg in enumerate(chat_history):
        role = msg.get("role", "assistant")
        msg_type = msg.get("type", "")
        content = msg.get("content", "")
        msg_id = f"msg_{idx}"
        st.markdown(f'<div id="{msg_id}"></div>', unsafe_allow_html=True)

        if role == "user":
            with st.chat_message("user"):
                st.markdown(content)
                _render_message_actions(msg_id, content, "user", msg_type)

        elif msg_type in ("system", "elicit"):
            with st.chat_message("Agent", avatar="assistant"):
                st.markdown(_present_content(content, source_lookup, confirmed_qa_store), unsafe_allow_html=True)
                _render_message_actions(msg_id, content, "assistant", msg_type)

        elif msg_type == "draft":
            with st.chat_message("Agent", avatar="assistant"):
                with st.expander("📝 View draft", expanded=False):
                    st.markdown(_present_content(content, source_lookup, confirmed_qa_store), unsafe_allow_html=True)

        elif msg_type == "reflect":
            verdict = msg.get("verdict", "REWORK")
            review_summary = _build_review_summary(msg)
            with st.chat_message("Agent", avatar="assistant"):
                if verdict == "PASS":
                    st.success("**Approved ✅**")
                else:
                    st.warning("**Needs one more update ⚠️**")

                st.markdown(
                    _present_content(review_summary["explanation"], source_lookup, confirmed_qa_store),
                    unsafe_allow_html=True,
                )

                st.markdown("**Next step**")
                st.markdown(
                    _present_content(str(review_summary["next_step"]), source_lookup, confirmed_qa_store),
                    unsafe_allow_html=True,
                )

                with st.expander("Advanced review details", expanded=False):
                    confidence = review_summary["confidence"]
                    if isinstance(confidence, (int, float)) and confidence >= 0:
                        st.caption(f"Confidence: {confidence:.0%}")

                    strengths = review_summary["strengths"]
                    if strengths:
                        st.markdown("**What is working well**")
                        for item in strengths:
                            st.markdown(
                                f"- {_present_content(str(item), source_lookup, confirmed_qa_store)}",
                                unsafe_allow_html=True,
                            )

                    if verdict != "PASS" and review_summary["improvement"]:
                        st.markdown("**Top improvement**")
                        st.markdown(
                            _present_content(str(review_summary["improvement"]), source_lookup, confirmed_qa_store),
                            unsafe_allow_html=True,
                        )

                    quality_checks = review_summary["quality_checks"]
                    if quality_checks:
                        st.markdown("**Quality checks passed**")
                        for item in quality_checks:
                            st.markdown(f"- {item}")

                    minor_notes = review_summary["minor_wording_notes"]
                    if minor_notes:
                        st.markdown("**Minor wording notes**")
                        for item in minor_notes:
                            st.markdown(
                                f"- {_present_content(item, source_lookup, confirmed_qa_store)}",
                                unsafe_allow_html=True,
                            )

                if _SHOW_INTERNAL_REVIEW_DEBUG:
                    with st.expander("Internal review debug", expanded=False):
                        st.markdown(_present_content(content, source_lookup, confirmed_qa_store), unsafe_allow_html=True)

        elif msg_type == "echo_confirmation":
            with st.chat_message("Agent", avatar="assistant"):
                st.markdown(_present_content(content, source_lookup, confirmed_qa_store), unsafe_allow_html=True)

        elif msg_type == "reask":
            with st.chat_message("Agent", avatar="assistant"):
                st.info(_present_content(content, source_lookup, confirmed_qa_store))

        elif msg_type == "contradiction_flag":
            with st.chat_message("Agent", avatar="assistant"):
                st.warning(_present_content(content, source_lookup, confirmed_qa_store))

        elif msg_type == "tagged_event":
            with st.chat_message("Agent", avatar="assistant"):
                st.success(_present_content(content, source_lookup, confirmed_qa_store))

        elif msg_type == "section_update_feed":
            with st.chat_message("Agent", avatar="assistant"):
                st.markdown(f"📊 {_present_content(content, source_lookup, confirmed_qa_store)}")
                updated_ids = msg.get("updated_section_ids", [])
                drafts = msg.get("section_drafts", {})
                for sec_id in updated_ids:
                    sec_title = _display_section_title(next(
                        (s.title for s in PRD_SECTIONS if s.id == sec_id), sec_id
                    ))
                    # Always show the latest draft from graph state, not the
                    # snapshot stored in chat_history (may be stale after
                    # subsequent rewrites in the same session).
                    latest_draft = sv.get("prd_sections", {}).get(
                        sec_id, drafts.get(sec_id, "")
                    )
                    if latest_draft:
                        with st.expander(
                            f"🔄 {sec_title} — view updated draft",
                            expanded=False,
                        ):
                            st.markdown(_present_content(latest_draft, source_lookup, confirmed_qa_store), unsafe_allow_html=True)

        elif msg_type == "advance":
            with st.chat_message("Agent", avatar="assistant"):
                st.success(_present_content(content, source_lookup, confirmed_qa_store))

        elif msg_type == "complete":
            with st.chat_message("Agent", avatar="assistant"):
                st.balloons()
                st.success(_present_content(content, source_lookup, confirmed_qa_store))

# ── Pending input: render user bubble + stream ABOVE the composer row ────────
# Stored by the composer when user submits; processed here so the messages
# appear after chat history, not below the input widget.
if st.session_state.get("_pending_payload"):
    _pp = st.session_state.pop("_pending_payload")
    _pm = st.session_state.pop("_pending_user_msg", "")
    with st.chat_message("user"):
        st.markdown(_pm)
    _stream_graph_resume(_pp)  # ends with st.rerun()

# ── Latency badge (A6) — only shown when debug telemetry is enabled ─────────
if _SHOW_INTERNAL_REVIEW_DEBUG and st.session_state.graph_started and st.session_state.last_elapsed_ms is not None:
    elapsed = st.session_state.last_elapsed_ms
    if elapsed >= 60_000:
        label = f"⏱ {elapsed // 60_000}m {(elapsed % 60_000) // 1000}s"
    elif elapsed >= 1_000:
        label = f"⏱ {elapsed / 1000:.1f}s"
    else:
        label = f"⏱ {elapsed}ms"
    st.caption(f"Last response: **{label}**")
    _timings = st.session_state.get("last_node_timings_ms", {}) or {}
    if _timings:
        parts = ", ".join(f"{k}: {v}ms" for k, v in sorted(_timings.items(), key=lambda kv: kv[1], reverse=True))
        st.caption(f"Node timings: {parts}")

# ── Composer reference chip (D-M14) ──────────────────────────────────────────
# Shown above the chat input when the user has selected a per-message action.
if st.session_state.graph_started and st.session_state.get("active_reference"):
    ref = st.session_state.active_reference
    excerpt = _present_content(ref.get("target_content", ""), {}, {})
    excerpt_display = (excerpt[:80] + "…") if len(excerpt) > 80 else excerpt
    chip_col, clear_col = st.columns([9, 1], gap="small")
    with chip_col:
        st.info(
            f"**{ref['label']}**"
            + (f" — _{excerpt_display}_" if excerpt_display else "")
        )
    with clear_col:
        if st.button("✕", key="clear_active_reference", help="Remove reference"):
            st.session_state.active_reference = None
            st.rerun()

# ── Composer row: [📎 Add image | text input] ─────────────────────────────────
_img_col, _inp_col = st.columns([1, 13], gap="small")

with _img_col:
    with st.popover("📎", help="Add image", use_container_width=True):
        uploaded_img = st.file_uploader(
            "Share a screenshot or diagram to help describe what you're building.",
            type=["png", "jpg", "jpeg", "webp"],
            key="image_uploader",
            label_visibility="visible",
        )
        if uploaded_img is not None:
            img_key = f"_img_processed_{uploaded_img.name}_{uploaded_img.size}"
            if img_key not in st.session_state:
                with st.spinner("Reading your image…"):
                    mime = uploaded_img.type or "image/png"
                    description = _describe_image(uploaded_img.read(), mime)
                st.session_state[img_key] = True
                if description:
                    st.session_state.image_context_buffer.append(description)
                    st.success("✓ Image added as context.")
                    if st.session_state.graph_started and gstate:
                        graph.update_state(_config(), {"image_context": [description]})
                else:
                    st.info("Image didn't contain product-related content — skipped.")

with _inp_col:
    if st.session_state.graph_started:
        # Active session: answer questions or show completion state
        is_waiting = bool(gstate and gstate.next) and not sv.get("is_complete", False)
        if is_waiting:
            active_ref = st.session_state.get("active_reference")
            placeholder = (
                "Reply, correct, or clarify…"
                if active_ref else "Type your answer and press Enter…"
            )
            user_input = st.chat_input(placeholder)
            if user_input and user_input.strip():
                placeholders = _find_placeholders(user_input)
                if placeholders:
                    st.warning(
                        "Your answer contains placeholder values (X, Y, Z) — "
                        "please use real numbers.\n\n"
                        + "\n".join(f"- `{s}`" for s in placeholders)
                    )
                else:
                    # Build structured payload
                    active_ref = st.session_state.get("active_reference")
                    if active_ref:
                        payload: dict | str = {
                            "event_type": active_ref["event_type"],
                            "content": user_input,
                            "target_message_id": active_ref["target_message_id"],
                            "target_content": active_ref.get("target_content", ""),
                            "source_message_role": active_ref.get("source_message_role", ""),
                            "ui_action_label": active_ref.get("label", ""),
                        }
                        st.session_state.active_reference = None  # consume reference
                    else:
                        payload = {"event_type": "ANSWER", "content": user_input}
                    # Store and rerun — processed above the composer row on next render
                    st.session_state._pending_payload = payload
                    st.session_state._pending_user_msg = user_input
                    st.rerun()
        elif sv.get("is_complete", False):
            st.chat_input("Session complete — download your PRD above.", disabled=True)

    else:
        # Landing: first message initialises the graph (D-M2)
        user_input = st.chat_input("Describe what you're building…")
        if user_input and user_input.strip():
            try:
                graph.invoke(
                    _build_initial_state(list(st.session_state.image_context_buffer)),
                    _config(),
                )
            except Exception as exc:
                st.error(f"Could not start session: {exc}")
                st.stop()
            st.session_state.graph_started = True
            st.session_state.image_context_buffer = []
            st.session_state._pending_payload = user_input
            st.session_state._pending_user_msg = user_input
            st.rerun()
