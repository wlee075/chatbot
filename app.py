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
from utils.progress_rail import compute_progress_data, get_pdf_download_state
import warnings

# Suppress Checkpointer unregistered type warnings for previous Enum states inside old SQLite streams
warnings.filterwarnings("ignore", message=".*Deserializing unregistered type graph.nodes.ExtractionCandidateType.*")
warnings.filterwarnings("ignore", message=".*Deserializing unregistered type graph.nodes.ProofStatus.*")

load_dotenv()

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Data Science Information Gathering",

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

def _get_display_image_summary(raw_block: str, ctx_hist: dict | None = None) -> str:
    if ctx_hist and ctx_hist.get("edited_summary"):
        return ctx_hist["edited_summary"]
    if not raw_block:
        return ""
    import re
    m = re.search(r'\[what_is_going_on\]\s*(.*?)(?=\n\n\[entities\]|\Z)', raw_block, re.DOTALL)
    if m:
        return m.group(1).strip()
    return raw_block.strip()


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
def _build_submit_payload(user_input: str, stashed_upload: dict | None, active_ref: dict | None = None) -> dict | None:
    """
    Centralized payload builder enforcing validity explicitly.
    A turn is valid if content is non-empty OR uploaded_files is non-empty.
    """
    user_input = user_input.strip() if user_input else ""
    
    # Invariant: valid if content non-empty OR file attached
    is_valid = bool(user_input) or bool(stashed_upload)
    if not is_valid:
        return None
        
    if active_ref:
        target_content_str = active_ref.get("target_content", "")
        truncated_preview = target_content_str[:100] + ("..." if len(target_content_str) > 100 else "")
        # A tagged UI action uses its bound explicit event_type, otherwise fallback to ANSWER
        payload = {
            "event_type": active_ref.get("event_type", "ANSWER"),
            "content": user_input,
            "target_message_id": active_ref.get("target_message_id", ""),
            "target_content": truncated_preview,
            "source_message_role": active_ref.get("source_message_role", ""),
            "ui_action_label": active_ref.get("label", ""),
        }
    else:
        payload = {"event_type": "ANSWER", "content": user_input}
        
    if stashed_upload:
        payload["uploaded_files"] = [stashed_upload]
        
    return payload

def _build_initial_state(image_context: list[str]) -> dict:
    return {
        "thread_id": st.session_state.thread_id,
        "run_id": str(uuid.uuid4()),
        "session_status": "",
        "session_end_reason": "",
        "session_end_message": "",
        "input_disabled": False,
        "draft_available": False,
        "draft_download_available": False,
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
        "store_version": 0,
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
        "rebuild_count": 0,
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
    r"\[SOURCE:\s*concept_key=([^,\]]+),\s*round=([^\]]+)\]",
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


# ── Compact timeline bar (above the input box) ──────────────────────────────

def _render_timeline_bar(sv: dict) -> None:
    """Inject a compact horizontal section-timeline bar just above the composer.

    Four visually distinct segment states:
      complete  → solid green fill (done)
      current   → indigo ring + glow (working on this now)
      partial   → amber half-tone fill (answers captured, not finished)
      pending   → muted dark (untouched)

    Header: 'Current Section: <name>' (workspace language, not wizard steps).
    """
    import html as _html
    data      = compute_progress_data(sv, PRD_SECTIONS)
    pct       = data["pct"]
    current   = _html.escape(data["current_title"])
    completed = data["completed"]
    segments  = data["checklist"]   # [{id, title, status}]

    pills = ""
    for seg in segments:
        title = _html.escape(seg["title"])
        if seg["status"] == "complete":
            pills += f'<div class="tl-pill tl-done" title="{title}"></div>'
        elif seg["status"] == "current":
            pills += f'<div class="tl-pill tl-cur-pip" title="{title}"></div>'
        elif seg["status"] == "partial":
            pills += f'<div class="tl-pill tl-partial" title="{title}"></div>'
        else:
            pills += f'<div class="tl-pill tl-future" title="{title}"></div>'

    # Percentage color tone signals achievement level
    if pct == 0:
        pct_color = "#6b7280"   # muted — just starting
    elif pct >= 80:
        pct_color = "#22c55e"   # green — strong momentum
    else:
        pct_color = "#9ca3af"   # neutral

    bar = f"""
<style>
.tl-wrap {{
    font-family: system-ui, -apple-system, sans-serif;
    margin: 6px 0 4px;
    user-select: none;
}}
.tl-header {{
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    margin-bottom: 5px;
}}
.tl-section-label {{
    font-size: 12px;
    color: #e2e8f0;
    font-weight: 700;
}}
.tl-section-prefix {{
    font-size: 12px;
    color: #6b7280;
    font-weight: 400;
}}
.tl-status-label {{
    font-size: 11px;
    color: {pct_color};
    font-weight: 500;
}}
.tl-bar {{
    display: flex;
    gap: 2px;
    align-items: center;
    height: 8px;
    margin-bottom: 5px;
}}
.tl-pill {{
    flex: 1;
    border-radius: 999px;
    height: 100%;
}}
.tl-done {{
    background: #22c55e;
}}
.tl-cur-pip {{
    background: transparent;
    border: 2px solid #6366f1;
    box-shadow: 0 0 5px rgba(99,102,241,.55);
    animation: tl-pulse 2s ease-in-out infinite;
}}
@keyframes tl-pulse {{
    0%, 100% {{ box-shadow: 0 0 4px rgba(99,102,241,.4); }}
    50%       {{ box-shadow: 0 0 9px rgba(99,102,241,.8); }}
}}
.tl-partial {{
    background: rgba(251,191,36,.45);
}}
.tl-future {{
    background: #1f2937;
}}
.tl-cur-label {{
    font-size: 11px;
    color: #6b7280;
    font-weight: 400;
    font-style: italic;
}}
@media (max-width: 640px) {{
    .tl-section-label {{ font-size: 10px; }}
    .tl-cur-label     {{ display: none; }}
}}
</style>
<div class="tl-wrap">
  <div class="tl-header">
    <span class="tl-section-label">
      <span class="tl-section-prefix">Current Section: </span>{current}
    </span>
    <span class="tl-status-label">{pct}% complete</span>
  </div>
  <div class="tl-bar">{pills}</div>
  <div class="tl-cur-label">Sections can be completed in any order</div>
</div>
"""
    st.markdown(bar, unsafe_allow_html=True)





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
div[data-testid="stChatMessageContent"],
div[data-testid="stMarkdownContainer"] {
  overflow: visible !important;
}

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

.cite-match {
  background-color: rgba(255, 215, 0, 0.4);
  border-bottom: 2px solid #ffd700;
  padding: 0 1px;
  border-radius: 2px;
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

if "__citation_js_injected" not in st.session_state:
    st.session_state["__citation_js_injected"] = True
    st.markdown(_CITATION_HANDLER_JS, unsafe_allow_html=True)

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
        # Ship-now UI Fallback (instead of generic (round N))
        if answer_text:
            snippet_text = (answer_text[:119] + "…") if len(answer_text) > 120 else answer_text
            snippet_text = snippet_text.replace("\n", " ").strip()
            return f"<span class='cite-fallback'>(from: \"{html.escape(snippet_text)}\")</span>"
        return ""

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
.prd-status-card .status-current .spin {
    display: inline-block;
    animation: prd-spin 1.1s linear infinite;
}
@keyframes prd-spin {
    from { transform: rotate(0deg); }
    to   { transform: rotate(360deg); }
}
</style>
"""

# Escalating thinking-state helper text (used before first node fires)
_THINKING_STAGES: list[tuple[float, str]] = [
    (0.0,  "Thinking..."),
    (3.0,  "Still working..."),
    (8.0,  "This is taking longer than usual..."),
    (15.0, "Complex request detected — almost there."),
]

def _build_status_card(completed: list[str], current: str) -> str:
    """Render the status card HTML from completed steps + running step."""
    lines = [_STATUS_CARD_CSS, '<div class="prd-status-card">']
    for step in completed[-3:]:  # show at most 3 completed steps
        lines.append(f'<span class="status-done">✓ {step}</span><br>')
    lines.append(f'<span class="status-current"><span class="spin">⟳</span> {current}</span>')
    lines.append("</div>")
    return "\n".join(lines)


def _thinking_text(elapsed: float) -> str:
    """Return the appropriate thinking-stage label for elapsed seconds."""
    label = _THINKING_STAGES[0][1]
    for threshold, text in _THINKING_STAGES:
        if elapsed >= threshold:
            label = text
    return label

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

    # ── Seed spinner immediately (before first node fires) ────────────────────
    # This covers the dead-zone between submit and the first graph node.
    status_slot.markdown(
        _build_status_card([], _thinking_text(0.0)),
        unsafe_allow_html=True,
    )
    _first_node_fired = False

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
                _first_node_fired = True
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

            # ── Escalating thinking text (no node yet) ───────────────────────
            # Update spinner label on every chunk if we haven't received a
            # named node yet — covers LangGraph startup latency edge cases.
            if not _first_node_fired:
                elapsed = time.monotonic() - t0
                thinking = _thinking_text(elapsed)
                status_slot.markdown(
                    _build_status_card([], thinking),
                    unsafe_allow_html=True,
                )

    except Exception as exc:
        status_slot.empty()
        stream_slot.empty()
        st.session_state.last_elapsed_ms = None
        st.session_state.last_node_timings_ms = {}
        import uuid
        import traceback
        req_id = str(uuid.uuid4())[:8]
        # Write traceback to console/logs, never to UI
        print(f"UI CRASH [{req_id}]:\n{traceback.format_exc()}")
        st.error(f"We hit an unexpected snag parsing your request. Please try again. (Ref: {req_id})")
        return

    if last_node:
        node_durations_ms[last_node] = node_durations_ms.get(last_node, 0) + int((time.monotonic() - node_started_at) * 1000)

    status_slot.empty()
    stream_slot.empty()
    turn_ms = int((time.monotonic() - t0) * 1000)
    st.session_state.last_elapsed_ms = turn_ms
    st.session_state.last_node_timings_ms = node_durations_ms
    
    import logging
    metrics_logger = logging.getLogger("orchestrator_metrics")
    metrics_logger.info(f"TURN_LATENCY_MS: {turn_ms}")
    for nd, dur in node_durations_ms.items():
        metrics_logger.info(f"NODE_LATENCY_MS | {nd}: {dur}")
        
    st.rerun()
# Render
# ══════════════════════════════════════════════════════════════════════════════

# Resolve graph state once per rerun — avoids repeated calls below
gstate = _get_graph_state() if st.session_state.graph_started else None
sv: dict = gstate.values if gstate else {}

# ── Sticky right-rail REMOVED — timeline is rendered above the composer instead ──────

# ── Top bar (D-M1): visible only when session is active ═════════════════
if st.session_state.graph_started:
    # Shared toolbar CSS ─────────────────────────────────────────────────
    st.markdown("""
<style>
/* ── shared button reset ── */
.tb-btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 5px;
    height: 34px;
    min-width: 80px;
    padding: 0 14px;
    border-radius: 7px;
    font-size: 13px;
    font-weight: 600;
    font-family: system-ui, -apple-system, sans-serif;
    white-space: nowrap;
    cursor: pointer;
    border: 1px solid transparent;
    text-decoration: none;
    transition: filter .15s, box-shadow .15s;
    line-height: 1;
}
.tb-btn:hover { filter: brightness(1.1); }
/* secondary (End / New) */
.tb-sec {
    background: #1e1e2e;
    color: #c9d1d9;
    border-color: #30363d;
}
/* primary (Download PDF) */
.tb-pri {
    background: #6366f1;
    color: #fff;
    border-color: #4f52d4;
    box-shadow: 0 1px 4px rgba(99,102,241,.35);
}
.tb-pri:disabled { opacity: .5; cursor: default; }
/* PDF badge pills */
.pdf-badge-draft {
    display: inline-block;
    font-size: 10px;
    font-weight: 700;
    padding: 1px 7px;
    border-radius: 99px;
    background: #d97706;
    color: #fff;
    margin-left: 4px;
    vertical-align: middle;
    letter-spacing: 0.03em;
}
.pdf-badge-complete {
    display: inline-block;
    font-size: 10px;
    font-weight: 700;
    padding: 1px 7px;
    border-radius: 99px;
    background: #16a34a;
    color: #fff;
    margin-left: 4px;
    vertical-align: middle;
    letter-spacing: 0.03em;
}
.tb-title {
    font-family: system-ui, -apple-system, sans-serif;
    font-size: 15px;
    font-weight: 700;
    color: #e2e8f0;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}
</style>
""", unsafe_allow_html=True)

    col_title, col_actions = st.columns([5, 2])
    with col_title:
        st.markdown('<span class="tb-title">Data Science Information Gathering</span>',
                    unsafe_allow_html=True)
    with col_actions:
        # ── Derive button states ──────────────────────────────────────────
        is_pending = bool(sv.get("is_complete", False) or sv.get("input_disabled", False))

        # ── PDF: always fresh snapshot ────────────────────────────────────
        prd_pdf = b""
        _report_title = "Draft Requirements Report"
        if st.session_state.get("graph_started"):
            try:
                from utils.report_composer import compose_report
                from graph.nodes import _render_pdf
                import logging as _logging
                _composer_log = _logging.getLogger("orchestrator_metrics")
                _report_artifact = compose_report(sv, trigger="download")
                _report_title = _report_artifact["report_title"]
                _composer_log.info("composer_pdf_export_started", extra={
                    "event_type": "composer_pdf_export_started",
                    "trigger": "download",
                    "source_hash": _report_artifact.get("source_hash", ""),
                })
                prd_pdf = _render_pdf(
                    _report_artifact["report_title"],
                    _report_artifact["generated_at"],
                    _report_artifact["executive_summary"],
                    _report_artifact["section_summaries"],
                )
            except Exception:
                pass

        safe_title = re.sub(r"[^\w\s\-]", "", _report_title).strip().replace(" ", "_")[:60] or "requirements_report"

        # ── Render action buttons ─────────────────────────────────────────
        # Three equal-width columns so buttons don't reflow
        b_end, b_new, b_dl = st.columns(3)
        with b_end:
            if st.button("⏹ End", use_container_width=True,
                         disabled=is_pending, key="tb_end"):
                st.session_state._pending_payload = {"event_type": "TERMINATE_SESSION", "content": ""}
                st.rerun()
        with b_new:
            if st.button("↩ New", use_container_width=True, key="tb_new"):
                st.session_state.thread_id = str(uuid.uuid4())
                st.session_state.graph_started = False
                st.session_state.last_elapsed_ms = None
                st.session_state.last_node_timings_ms = {}
                st.session_state.image_context_buffer = []
                st.session_state.active_reference = None
                st.rerun()
        with b_dl:
            # ── PDF gate: locked < 80 %, Draft 80-99 %, Final 100 % ─────
            try:
                _pdf_pct = compute_progress_data(sv, PRD_SECTIONS)["pct"]
            except Exception:
                _pdf_pct = 0
            _pdf_gate = get_pdf_download_state(_pdf_pct)
            if _pdf_gate["enabled"] and prd_pdf:
                st.download_button(
                    label=_pdf_gate["label"],
                    data=prd_pdf,
                    file_name=f"{safe_title}.pdf",
                    mime="application/pdf",
                    use_container_width=True,
                    type=_pdf_gate["btn_type"],
                    key="pdf_download_btn",
                    help=_pdf_gate["hint"],
                )
            else:
                st.button(
                    _pdf_gate["label"],
                    use_container_width=True,
                    disabled=True,
                    key="pdf_download_btn_disabled",
                    help=_pdf_gate["hint"],
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

    if st.session_state.get("debug_show_internal_progress", False):
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

    # Chunk chat_history into turns based on user boundaries
    turns = []
    current_turn = {"user": None, "assistant": []}
    
    for idx, msg in enumerate(chat_history):
        msg["_idx"] = idx  # Keep track of original index for msg_id
        if msg.get("role") == "user":
            if current_turn["user"] or current_turn["assistant"]:
                turns.append(current_turn)
            current_turn = {"user": msg, "assistant": []}
        else:
            current_turn["assistant"].append(msg)
            
    if current_turn["user"] or current_turn["assistant"]:
        turns.append(current_turn)

    is_waiting = bool(gstate and gstate.next) and not sv.get("is_complete", False)
    active_ref = st.session_state.get("active_reference")

    for turn in turns:
        # 1. Render User Message if any
        if turn["user"]:
            user_msg = turn["user"]
            content = user_msg.get("content", "")
            msg_id = f"msg_{user_msg['_idx']}"
            st.markdown(f'<div id="{msg_id}"></div>', unsafe_allow_html=True)
            with st.chat_message("user"):
                reply_snippet = user_msg.get("reply_to_content_snippet")
                if reply_snippet:
                    preview = (reply_snippet[:80] + "...") if len(reply_snippet) > 80 else reply_snippet
                    st.caption(f"Replying to: \"_{preview}_\"")
                st.markdown(content)
                
                # Strictly render the Attached Image Context only if this specific message originated it (ignore global session leaks)
                msg_id_val = user_msg.get("msg_id")
                bg_ctx_hists = [c for c in sv.get("background_generated_contexts", []) if c.get("source_turn_id") == msg_id_val]
                
                for bg_ctx_hist in bg_ctx_hists:
                    is_removed = not bg_ctx_hist.get("is_active", True)
                    label = "🖼️ Attached Image Context (Removed by User)" if is_removed else "🖼️ Attached Image Context"
                    
                    with st.expander(label, expanded=False):
                        eff_summary = _get_display_image_summary(bg_ctx_hist.get("generated_summary", ""), bg_ctx_hist)
                        if is_removed:
                            st.warning("Removed from active context. The agent will no longer use this image data.")
                            st.markdown(f"~~{eff_summary}~~")
                        else:
                            st.markdown(eff_summary)
                        
                _render_message_actions(user_msg.get("msg_id") or msg_id, content, "user", user_msg.get("type", ""))

        # 2. Render Single Terminal Intents for Assistant
        assistant_msgs = turn["assistant"]
        if not assistant_msgs:
            continue
            
        # Classify the terminal block for this cycle
        has_advance = any(m.get("type") in ("advance", "complete") for m in assistant_msgs)
        # Index of the advance/complete message — elicit messages BEFORE this
        # index are section-closure clarifications (suppress them); elicit
        # messages AFTER are the opening question for the next section (keep them).
        advance_idx = next(
            (i for i, m in enumerate(assistant_msgs) if m.get("type") in ("advance", "complete")),
            len(assistant_msgs),
        )
        # When multiple post-advance elicits exist (e.g. parser-fallback dual-call
        # pattern in generate_questions_node emits two elicit messages), only the
        # LAST one is authoritative — it is the actual next-section opening question.
        last_post_advance_elicit_idx = None
        if has_advance:
            for _k in range(len(assistant_msgs) - 1, advance_idx, -1):
                if assistant_msgs[_k].get("type") == "elicit":
                    last_post_advance_elicit_idx = _k
                    break
        
        # Single Bubble Context
        with st.chat_message("Agent", avatar="assistant"):
            for msg_i, msg in enumerate(assistant_msgs):
                msg_type = msg.get("type", "")
                content = msg.get("content", "")
                msg_id = f"msg_{msg['_idx']}"
                st.markdown(f'<div id="{msg_id}"></div>', unsafe_allow_html=True)
                
                # --- Filter Logic ---
                if msg_type in ("reflect", "elicit") and has_advance and msg_i < advance_idx:
                    # Suppress clarification/elicit messages that appear BEFORE
                    # the section-advance banner (those belong to the old section).
                    continue
                if msg_type == "elicit" and has_advance and msg_i > advance_idx:
                    # Multiple post-advance elicits: only keep the final one.
                    if msg_i != last_post_advance_elicit_idx:
                        continue

                    
                if msg_type == "elicit" and "I have all the details I need for this section. Let's move on." in content and len(content) > 65:
                    content = content.replace("I have all the details I need for this section. Let's move on.", "").strip()

                # --- Render Logic ---
                if msg_type in ("system", "elicit", "clarification_answer", "numeric_validation_error"):
                    content_segs = msg.get("content_segments")
                    if content_segs:
                        html_pieces = []
                        citation_registry = {}  # key: (s_msg_id, snippet_html), val: citation_number
                        sources_list = []
                        cit_counter = 1

                        for seg in content_segs:
                            p = seg.get("provenance")
                            if p:
                                 d_time = p.get("source_display_time", "Earlier")
                                 speaker = "User"
                                 snippet_html = p.get("snippet_html", "")
                                 s_msg_id = p.get("source_message_id", "")
                                 status = p.get("proof_status", "EXACT_SURFACE")
                                 
                                 # Track how often Streamlit is re-rendering the same valid snippets
                                 source_key = (s_msg_id, snippet_html)
                                 
                                 _global_renders = st.session_state.setdefault("_citation_render_counts", {})
                                 _global_renders[source_key] = _global_renders.get(source_key, 0) + 1
                                 if _global_renders[source_key] > 1:
                                     # Explicitly log if the UI loop reprocesses this historical snippet multiple times
                                     import logging
                                     logging.getLogger("orchestrator_metrics").info(
                                         "Historical snippet reuse in UI",
                                         extra={
                                             "event_type": "citation_render_reuse_detected",
                                             "turn_id": sv.get("run_id", ""), 
                                             "message_id": s_msg_id, 
                                             "rerender_count": _global_renders[source_key], 
                                             "same_snippet_reprocessed": True
                                         }
                                     )
                                 
                                 if source_key not in citation_registry:
                                     citation_registry[source_key] = cit_counter
                                     clean_snip = html.escape(snippet_html.replace('\n', ' ').strip())
                                     if len(clean_snip) > 100: clean_snip = clean_snip[:97] + "..."
                                     sources_list.append(f"<div style='font-size:0.85rem; color:#666; margin-bottom:4px;'><b>[{cit_counter}]</b> {speaker} &middot; {d_time} &mdash; <i>\"{clean_snip}\"</i></div>")
                                     cit_counter += 1
                                     
                                 cit_num = citation_registry[source_key]

                                 anchor_js = f"window._citationHandler('{s_msg_id}')" if s_msg_id and s_msg_id != "fact_store" else ""
                                 onclick_html = f" onclick=\"{anchor_js}\"" if anchor_js else ""
                                 
                                 # We just render the citation marker next to the keyword now
                                 html_pieces.append(
                                     f'<span class="cite-chip"{onclick_html}>{seg["text"]} <sup style="color:#0066cc; font-weight:bold;">[{cit_num}]</sup></span>'
                                 )
                            else:
                                html_pieces.append(seg["text"])
                                
                        rendered_content = "".join(html_pieces)
                        st.markdown(_present_content(rendered_content, source_lookup, confirmed_qa_store), unsafe_allow_html=True)
                        
                        if sources_list:
                            st.markdown("<hr style='margin: 12px 0; border: none; border-top: 1px solid #ddd;'/>", unsafe_allow_html=True)
                            st.markdown("".join(sources_list), unsafe_allow_html=True)
                    else:
                        render_val = content
                        if msg_type == "numeric_validation_error":
                            reason_code = sv.get("validation_reason", "")
                            
                            UI_NUMERIC_ERROR_MAPPING = {
                                "hours_per_day_exceeds_24": "That number looks off — the hours per day can’t be more than 24. Could you clarify the correct figure?",
                                "negative_values": "That number looks off — it can't be negative. Could you clarify the correct figure?",
                                "impossible_percentages": "That percentage looks off — it shouldn't be over 100%. Could you clarify?",
                                "zero_when_not_allowed": "That number looks off — it must be greater than zero. Could you clarify?"
                            }
                            
                            fallback_msg = "That number looks off — could you double-check and clarify the correct figure?"
                            render_val = UI_NUMERIC_ERROR_MAPPING.get(reason_code, fallback_msg)

                        st.markdown(_present_content(render_val, source_lookup, confirmed_qa_store), unsafe_allow_html=True)
                        
                    # Add helper line text inside the latest assistant box
                    if turn == turns[-1] and msg == assistant_msgs[-1] and is_waiting and not active_ref:
                        next_action = sv.get("next_action")
                        if msg_type == "clarification_answer":
                            helper_copy = "I just clarified the missing context. If that makes sense, we can continue."
                        elif next_action == "START_DRAFT":
                            helper_copy = "After you reply, I’ll likely begin drafting."
                        elif next_action == "ASK_ONE_MORE":
                            helper_copy = "I need one final detail before drafting."
                        elif next_action == "ASK_MULTIPLE":
                            helper_copy = "I still need a few key details before drafting."
                        elif next_action == "UPDATE_DRAFT":
                            helper_copy = "Your next reply will update the current draft."
                        elif next_action == "WAITING_CONFIRMATION":
                            helper_copy = "Please confirm the last point so I can continue."
                        else:
                            helper_copy = "After you reply, I’ll either start drafting if I have enough detail, or ask one focused follow-up question."
                        st.caption(f"_{helper_copy}_")

                    if msg_type not in ("clarification_answer", "numeric_validation_error"):
                        _render_message_actions(msg.get("msg_id") or msg_id, content, "assistant", msg_type)
                    
                elif msg_type == "draft":
                    if sv.get("response_type") in ("clarification_answer", "numeric_validation_error"):
                        continue
                    with st.expander("📝 View draft", expanded=False):
                        st.markdown(_present_content(content, source_lookup, confirmed_qa_store), unsafe_allow_html=True)
                        
                elif msg_type == "contradiction_flag":
                    st.markdown(content)
                    evidence = msg.get("contradiction_evidence")
                    if evidence:
                        with st.expander("🔍 View earlier reply"):
                            st.markdown(f"**Earlier you said:**\n\n> {evidence.get('evidence_snippet', '')}")
                            
                elif msg_type == "reflect":
                    if sv.get("response_type") in ("clarification_answer", "numeric_validation_error"):
                        continue
                    verdict = msg.get("verdict", "REWORK")
                    review_summary = _build_review_summary(msg)
                    
                    if verdict == "PASS":
                        st.success("**Approved ✅**")
                    else:
                        st.warning("**Needs one more update ⚠️**")
                        
                    with st.expander("💭 Reasoning", expanded=False):
                        st.markdown(
                            _present_content(review_summary["explanation"], source_lookup, confirmed_qa_store),
                            unsafe_allow_html=True,
                        )
                    
                    # The next_action_reason is only for backend telemetry and no longer displayed in the UI.
                    
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
                    st.markdown(_present_content(content, source_lookup, confirmed_qa_store), unsafe_allow_html=True)

                elif msg_type == "reask":
                    st.info(_present_content(content, source_lookup, confirmed_qa_store))

                elif msg_type == "tagged_event":
                    st.success(_present_content(content, source_lookup, confirmed_qa_store))

                elif msg_type == "section_update_feed":
                    st.markdown(f"📊 {_present_content(content, source_lookup, confirmed_qa_store)}")
                    updated_ids = msg.get("updated_section_ids", [])
                    drafts = msg.get("section_drafts", {})
                    for sec_id in updated_ids:
                        sec_title = _display_section_title(next(
                            (s.title for s in PRD_SECTIONS if s.id == sec_id), sec_id
                        ))
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
                    st.success(_present_content(content, source_lookup, confirmed_qa_store))

                elif msg_type == "complete":
                    st.balloons()
                    st.success(_present_content(content, source_lookup, confirmed_qa_store))
                    # ── U1: Inline PDF download — gated by 80% threshold ──
                    _inline_pdf = sv.get("prd_pdf_bytes", b"")
                    if _inline_pdf:
                        _raw_title = sv.get("prd_report_title", "") or "requirements_report"
                        _safe_fn = re.sub(r"[^\w\s\-]", "", _raw_title).strip().replace(" ", "_")[:60] or "requirements_report"
                        _inline_pct = compute_progress_data(sv, PRD_SECTIONS)["pct"]
                        _inline_gate = get_pdf_download_state(_inline_pct)
                        if _inline_gate["enabled"]:
                            st.download_button(
                                label=_inline_gate["label"],
                                data=_inline_pdf,
                                file_name=f"{_safe_fn}.pdf",
                                mime="application/pdf",
                                key="inline_pdf_download_btn",
                                type=_inline_gate["btn_type"],
                                help=_inline_gate["hint"],
                            )
                        else:
                            st.button(
                                _inline_gate["label"],
                                disabled=True,
                                key="inline_pdf_download_btn_locked",
                                help=_inline_gate["hint"],
                            )
                        if _inline_gate["badge"]:
                            badge_cls = "pdf-badge-draft" if _inline_gate["badge"] == "Draft" else "pdf-badge-complete"
                            st.markdown(
                                f'<span class="{badge_cls}">{_inline_gate["badge"]}</span> '
                                f'<span style="font-size:11px;color:#6b7280">{_inline_gate["hint"]}</span>',
                                unsafe_allow_html=True,
                            )

    if sv.get("session_status") == "ended_retry_limit":
        st.error(f"**Session Ended:** {sv.get('session_end_message', 'Unable to get enough information to continue. Session has ended.')}")

# ── Pending input: render user bubble + stream ABOVE the composer row ────────
# Stored by the composer when user submits; processed here so the messages
# appear after chat history, not below the input widget.
if st.session_state.get("_pending_payload"):
    _pp = st.session_state.pop("_pending_payload")
    _pm = st.session_state.pop("_pending_user_msg", "")
    if _pm:
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

# ── Compact timeline bar: rendered once per rerun above the chat composer ─────────────
if st.session_state.graph_started and sv:
    _render_timeline_bar(sv)

if True:
    if st.session_state.graph_started:
        # Active session: answer questions or show completion state
        if is_waiting:
            user_input = None
            
            # Ensure no committed image summaries are floated near the composer. Unsent files are previewed natively by st.chat_input.
            placeholder = (
                "Reply, correct, or clarify…"
                if active_ref else "Type your answer and press Enter…"
            )
            user_input_obj = st.chat_input(placeholder, accept_file=True, file_type=["png", "jpg", "jpeg", "webp"])
                
        elif sv.get("is_complete", False):
            st.chat_input("Session complete — see download in chat above.", disabled=True)
            user_input_obj = None
        elif sv.get("input_disabled", False):
            st.chat_input("Session has ended.", disabled=True)
            user_input_obj = None
        else:
            user_input_obj = None
            
        if user_input_obj is not None:
            # Handle the dict-like or string object
            user_input = getattr(user_input_obj, "text", "") if hasattr(user_input_obj, "text") else (user_input_obj if isinstance(user_input_obj, str) else "")
            uploaded_files_list = []
            
            if isinstance(user_input_obj, dict):
                files = user_input_obj.get("files", [])
                user_input = user_input_obj.get("text", "")
            else:
                files = getattr(user_input_obj, "files", []) if hasattr(user_input_obj, "files") else []
                
            if files:
                import uuid
                uploaded_img = files[0]
                mime = uploaded_img.type or "image/png"
                uploaded_files_list.append({
                    "file_id": f"file_{uuid.uuid4().hex[:8]}",
                    "filename": uploaded_img.name,
                    "size_bytes": uploaded_img.size,
                    "mime_type": mime,
                    "bytes": uploaded_img.read()
                })
                
            # Safe submit rule checking
            has_text_input = bool(user_input and user_input.strip())
            has_files = bool(uploaded_files_list)
            
            if has_text_input or has_files:
                placeholders = _find_placeholders(user_input) if has_text_input else []
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
                        target_content_str = active_ref.get("target_content", "")
                        # Truncate to purely act as a preview/debug string. Never meant for semantic interpretation.
                        truncated_preview = target_content_str[:100] + ("..." if len(target_content_str) > 100 else "")
                        payload = {
                            "event_type": active_ref["event_type"],
                            "content": user_input,
                            "target_message_id": active_ref["target_message_id"],
                            "target_content": truncated_preview,
                            "source_message_role": active_ref.get("source_message_role", ""),
                            "ui_action_label": active_ref.get("label", ""),
                        }
                        st.session_state.active_reference = None  # consume reference
                    else:
                        content_val = user_input if has_text_input else "[Image Uploaded]"
                        payload = {"event_type": "ANSWER", "content": content_val}
                        
                    if uploaded_files_list:
                        payload["uploaded_files"] = uploaded_files_list
                        
                    import logging, re
                    metrics_logger = logging.getLogger("orchestrator_metrics")
                    contains_prior_chat = bool(re.search(r"(?i)\buser:.*\bassistant:", user_input)) if user_input else False
                    metrics_logger.info("composer_submit_payload_debug", extra={
                        "event_type": "composer_submit_payload_debug",
                        "turn_id": sv.get("run_id", "unknown") if sv else "unknown",
                        "latest_user_input_length": len(user_input),
                        "contains_prior_chat_markers": contains_prior_chat,
                        "reply_context_present": bool(active_ref),
                        "upload_text_length": 0
                    })
                        
                    # Store and rerun — processed above the composer row on next render
                    st.session_state._pending_payload = payload
                    st.session_state._pending_user_msg = user_input
                    st.rerun()

    else:
        # Landing: first message initialises the graph (D-M2)
        user_input_obj = st.chat_input("What are you building?", accept_file=True, file_type=["png", "jpg", "jpeg", "webp"])
        
        user_input = getattr(user_input_obj, "text", "") if hasattr(user_input_obj, "text") else (user_input_obj if isinstance(user_input_obj, str) else "")
        uploaded_files_list = []
        if isinstance(user_input_obj, dict):
            files = user_input_obj.get("files", [])
            user_input = user_input_obj.get("text", "")
        else:
            files = getattr(user_input_obj, "files", []) if hasattr(user_input_obj, "files") else []
            
        if files:
            import uuid
            uploaded_img = files[0]
            mime = uploaded_img.type or "image/png"
            stashed_upload = {
                "file_id": f"file_{uuid.uuid4().hex[:8]}",
                "filename": uploaded_img.name,
                "size_bytes": uploaded_img.size,
                "mime_type": mime,
                "bytes": uploaded_img.read()
            }
        else:
            stashed_upload = None

        has_text_input = bool(user_input and user_input.strip())
        has_files = bool(stashed_upload)
        
        if has_text_input or has_files:
            content_val = user_input if has_text_input else "[Image Uploaded]"
            payload = _build_submit_payload(user_input, stashed_upload)
            
            if payload:
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
                
                st.session_state._pending_payload = payload
                st.session_state._pending_user_msg = content_val
                st.rerun()
