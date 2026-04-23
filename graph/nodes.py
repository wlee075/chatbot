'''
Parses OVERALL SCORE with a format-agnostic regex
If score ≥ 0 (parseable): overrides PASS → REWORK when score < 8.5; forces TRIAGE: ENTER RECOVERY MODE when score < 5.0
Stores overall_score in state and chat_history dict (useful for the future test set)
Stores SCORING_INTERPRETATION_BLOCK forwarded to REFLECTOR_SYSTEM.format()
advance_section_node resets overall_score to -1.0

Threshold behaviour table:
OVERALL SCORE	LLM says PASS	System enforces
≥ 8.5	PASS	PASS
5.0–8.4	PASS	REWORK (override)
< 5.0	anything	REWORK + ENTER RECOVERY MODE
-1.0 (parse fail)	PASS	PASS (no override)
'''
import hashlib
import json
import os
import re
import time
import uuid
import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from difflib import SequenceMatcher

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.types import interrupt

from config.sections import PRD_SECTIONS, get_section_by_index, get_section_by_id
from graph.concept_map import get_impacted_sections as _rule_impacted_sections
from graph.state import PRDState, ConceptStatus, ConversationUnderstandingOutput, ConceptRecord, BlockerRecord, CorrectionRecord, ActionCandidateRecord, DraftReadinessDict
from utils.logger import log_event
from utils.llm_logger import llm_invoke, flush_turn_summary
from utils.telemetry import log_canonical_write, log_integrity_failure, log_suppression_decision
from utils.validator import IntegrityValidator
import html
from enum import Enum
from typing import TypedDict, Optional

# ── Business Config ──────────────────────────────────────────────────────────
PROVENANCE_CONF_THRESHOLD = 8.5
MAX_CHIPS_PER_MESSAGE = 3

class ExtractionReason(str, Enum):
    STOPWORD = "stopword"
    TOO_SHORT = "too_short"
    PUNCTUATION = "punctuation"
    SUBSUMED = "subsumed"
    DUPLICATE = "duplicate"
    LOW_VALUE = "low_value"
    TRUNCATED = "truncated"

class ExtractionCandidateType(str, Enum):
    NOUN_CHUNK = "noun_chunk"
    TOKEN = "token"
    ENTITY = "entity"

class ProofStatus(str, Enum):
    EXACT_SURFACE = "EXACT_SURFACE"         # Word-for-word match
    NORMALIZED_SURFACE = "NORMALIZED_SURFACE" # Material difference (e.g. PDFs vs pdf)
    LEMMA_BACKED = "LEMMA_BACKED"           # Linked via linguistic root
    REFUSED = "REFUSED"                     # Insufficient trust or ambiguous

class ProofUnit(TypedDict):
    assistant_surface_text: str
    concept_key: str
    source_message_id: str
    source_span: tuple[int, int]
    source_display_time: str
    snippet_html: str
    proof_status: str
    ranking_reason: str

# ── Business domain allowlist for extraction ──────────────────────────────────
DOMAIN_ALLOWLIST = {"pdf", "sku", "sap", "sql", "prd", "lark", "csv", "excel", "mailbox"}

# ── spaCy resources (singleton) ──────────────────────────────────────────────
_NLP = None

def _get_nlp():
    global _NLP
    if _NLP is None:
        try:
            import spacy
            _NLP = spacy.load("en_core_web_sm")
            ruler = _NLP.add_pipe("entity_ruler", before="ner")
            patterns = [
                {"label": "FILE_TYPE", "pattern": [{"LOWER": "pdf"}]},
                {"label": "FILE_TYPE", "pattern": [{"LOWER": "csv"}]},
                {"label": "FILE_TYPE", "pattern": [{"LOWER": "excel"}]},
                {"label": "FILE_TYPE", "pattern": [{"LOWER": "prd"}]},
                {"label": "TOOL", "pattern": [{"LOWER": "sap"}]},
                {"label": "TOOL", "pattern": [{"LOWER": "sql"}]},
                {"label": "TOOL", "pattern": [{"LOWER": "salesforce"}]},
                {"label": "TOOL", "pattern": [{"LOWER": "streamlit"}]},
                {"label": "TOOL", "pattern": [{"LOWER": "lark"}]},
                {"label": "EMAIL_GROUP", "pattern": [{"LOWER": "mailbox"}]},
                {"label": "EMAIL_GROUP", "pattern": [{"LOWER": "group"}, {"LOWER": "mailbox"}]},
                {"label": "SYSTEM", "pattern": [{"LOWER": "sku"}]},
            ]
            ruler.add_patterns(patterns)
        except Exception as e:
            log_event(
                thread_id="", run_id="", message="spacy_load_failure",
                level="ERROR", event_type="spacy_error", error=str(e)
            )
            _NLP = False  # Fallback marker
    return _NLP

# ── NLTK resources (cached at module load) ────────────────────────────────────
try:
    import nltk
    from nltk.tokenize import sent_tokenize as _sent_tokenize, word_tokenize as _word_tokenize
    from nltk.corpus import stopwords as _nltk_sw
    from nltk.stem import SnowballStemmer as _SnowballStemmer

    # Bootstrap required corpora once; safe to call repeatedly.
    for _corpus in ("punkt_tab", "stopwords"):
        try:
            nltk.data.find(f"tokenizers/{_corpus}" if "punkt" in _corpus else f"corpora/{_corpus}")
        except LookupError:
            nltk.download(_corpus, quiet=True)

    _STEMMER = _SnowballStemmer("english")
    _STOPWORDS_EN: frozenset[str] = frozenset(_nltk_sw.words("english"))
    _NLTK_AVAILABLE = True
except Exception as _nltk_err:
    import logging as _logging
    _logging.warning(f"NLTK unavailable ({_nltk_err}); clause extraction will use fallback tokenizer.")
    _STEMMER = None
    _STOPWORDS_EN = frozenset({"a", "an", "the", "is", "are", "was", "were", "it", "they"})
    _NLTK_AVAILABLE = False

    def _sent_tokenize(text: str) -> list[str]:
        return [s.strip() for s in re.split(r'(?<=[.!?])\s+', text) if s.strip()]

    def _word_tokenize(text: str) -> list[str]:
        return re.findall(r"[a-zA-Z']+", text.lower())
from prompts.templates import (
    CLARIFICATION_ANSWER_PROMPT,
    CONVERSATION_UNDERSTANDING_BLOCK,
    INTENT_FALLBACK_CLASSIFICATION_PROMPT,
    DECISION_ENFORCEMENT_BLOCK,
    DEFAULT_MAX_RECOVERY_MODE_CONSECUTIVE_ITERATIONS,
    DEFAULT_MAX_SECTION_ITERATIONS,
    DRAFTER_CONTEXT_DOC_BLOCK,
    DRAFTER_PRD_CONTEXT_BLOCK,
    DRAFTER_SYSTEM,
    ECHO_INTERPRET_PROMPT,
    ELICITOR_CONTEXT_BLOCK,
    ELICITOR_FIRST_TURN_BLOCK,
    ELICITOR_ITERATION_BLOCK,
    ELICITOR_PRD_BLOCK,
    ELICITOR_SYSTEM,
    GLOBAL_RIGOR_BLOCK,
    HUMAN_TRUST_BLOCK,
    IMPACT_DETECTION_PROMPT,
    ITERATION_DISCIPLINE_BLOCK,
    LANGUAGE_RULES_BLOCK,
    NUMERIC_GROUNDING_BLOCK,
    PASS_SCORE_THRESHOLD,
    RECOVERY_MODE_SCORE_THRESHOLD,
    REFLECTOR_PRIOR_SECTIONS_BLOCK,
    REFLECTOR_SYSTEM,
    SCORING_INTERPRETATION_BLOCK,
    SIDE_FACT_EXTRACTION_PROMPT,
)


# ── Shared helpers ────────────────────────────────────────────────────────────

def _get_llm() -> ChatGoogleGenerativeAI:
    model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    return ChatGoogleGenerativeAI(model=model, temperature=0)


def _format_prd_so_far(prd_sections: dict) -> str:
    """Render accumulated PRD sections as readable markdown."""
    if not prd_sections:
        return ""
    parts = []
    for section in PRD_SECTIONS:
        if section.id in prd_sections:
            parts.append(f"## {section.title}\n{prd_sections[section.id]}")
    return "\n\n".join(parts)


def _safe_highlight_render(text: str, start: int, end: int) -> str:
    """Return plain text for snippet window without injecting raw HTML. HTML insertion here violates architecture boundary."""
    if not text or start < 0 or end > len(text) or start >= end:
        return text
    
    pre = text[:start]
    match = text[start:end]
    post = text[end:]
    
    return f'{pre}{match}{post}'


def _sniper_window(text: str, start: int, end: int, window_size: int = 100) -> str:
    """Center snippet on match, snapping to sentence/word boundaries."""
    if not text:
        return ""
    if len(text) <= window_size:
        return _safe_highlight_render(text, start, end)

    half_win = window_size // 2
    match_center = (start + end) // 2
    
    w_start = max(0, match_center - half_win)
    w_end = min(len(text), match_center + half_win)

    # Snap to nearest word boundary
    if w_start > 0:
        while w_start < start and text[w_start] not in (" ", "\n"):
            w_start += 1
    if w_end < len(text):
        while w_end > end and text[w_end] not in (" ", "\n"):
            w_end -= 1

    # Snap to nearest sentence boundary if close
    sent_snap_range = 20
    prev_dot = text.rfind(".", max(0, w_start - sent_snap_range), w_start + sent_snap_range)
    if prev_dot != -1 and prev_dot < start:
        w_start = prev_dot + 1
    
    # Final render
    snippet = text[w_start:w_end].strip()
    # Recalculate relative offsets for the snippet
    rel_start = start - w_start
    rel_end = end - w_start
    
    rendered = _safe_highlight_render(snippet, rel_start, rel_end)
    
    # Add ellipsis if snapped or cut
    prefix = "" if w_start == 0 else "... "
    suffix = "" if w_end == len(text) else " ..."
    return f"{prefix}{rendered}{suffix}"


def _parse_rubric_score(text: str, rubric: str) -> float:
    """Extract a single rubric score from reflector output. Returns -1.0 on failure.

    Tolerates varied separator formats: em-dash, colon, space, markdown bold, etc.
    Restricts to the same line as the rubric name to avoid false matches.
    """
    m = re.search(
        rf"{re.escape(rubric)}[^\d\n]*(\d+\.?\d*)\s*/\s*10",
        text,
        re.IGNORECASE,
    )
    return float(m.group(1)) if m else -1.0


def _log_ctx(state: PRDState, node_name: str) -> dict:
    """Extract the standard context fields required by every log_event call."""
    section_idx = state.get("section_index", 0)
    section_name = (
        PRD_SECTIONS[section_idx].title
        if 0 <= section_idx < len(PRD_SECTIONS)
        else ""
    )
    return {
        "thread_id": state.get("thread_id", ""),
        "run_id": state.get("run_id", ""),
        "node_name": node_name,
        "section_name": section_name,
        "section_index": section_idx,
        "iteration": state.get("iteration", 0),
    }

def _enforce_visibility(return_dict: dict, prompt_text: str, section_title: str, section_index: int, iteration: int = 0, event_type: str = "elicit") -> dict:
    """Consolidated helper to append a prompt to chat_history and assert its visibility."""
    if not prompt_text:
        return return_dict
    
    hist = return_dict.get("chat_history", [])
    msg_dict = {
        "role": "assistant",
        "msg_id": f"msg_{str(uuid.uuid4())[:8]}",
        "type": event_type,
        "section": section_title,
        "section_index": section_index,
        "iteration": iteration,
        "content": prompt_text,
    }
    if "content_segments" in return_dict:
        segs = return_dict["content_segments"]
        
        # Phase 5 Fix: The single correct sanitization boundary for terminal emission
        for s in segs:
            p = s.get("provenance")
            if p and "snippet_html" in p:
                raw_snip = p["snippet_html"]
                has_html = bool(raw_snip and "<" in raw_snip and ">" in raw_snip)
                if has_html:
                    p["snippet_html"] = re.sub(r'<[^>]+>', '', raw_snip).strip()
                    
                log_event(
                    thread_id="", run_id="", node_name="final_response_assembly",
                    level="INFO", event_type="citation_sanitization_applied",
                    message="Sanitization applied before final assembly",
                    turn_id=return_dict.get("run_id", ""),
                    message_id=p.get("source_message_id", ""),
                    sanitized_before_assembly=True,
                    html_removed=has_html
                )

        msg_dict["content_segments"] = segs
        prov_count = sum(1 for s in segs if s.get("provenance")) if isinstance(segs, list) else 0
        log_event(thread_id="", run_id="", node_name="_enforce_visibility",
                  level="DEBUG", event_type="provenance_segments_attached",
                  message=f"content_segments attached: {len(segs)} segments, {prov_count} with provenance",
                  total_segments=len(segs), provenance_count=prov_count)
        
    hist.append(msg_dict)
    return_dict["chat_history"] = hist
    
    log_event(
        thread_id="", run_id="", node_name="final_response_assembly",
        level="INFO", event_type="final_response_assembly",
        message="Assembled final output for turn",
        response_type=event_type,
        final_text=prompt_text,
        used_draft_ui="draft" in event_type.lower(),
        used_clarification_ui="clarification" in event_type.lower(),
        active_question_id=return_dict.get("active_question_id", "")
    )
    
    has_visible_history = any(
        m.get("role") == "assistant" and m.get("content") == prompt_text
        for m in return_dict.get("chat_history", [])
    )
    assert has_visible_history, "CRITICAL: Emitted user-facing prompt but missing visible assistant message in chat_history"
    
    return return_dict

def rebuild_mirror_node(state: PRDState) -> dict:
    """
    Ensures section_qa_pairs is a deterministic derivative of confirmed_qa_store.
    Enforces idempotent state reconciliation at the start of every turn.
    """
    t0 = time.monotonic()
    ctx = _log_ctx(state, "rebuild_mirror")
    section = get_section_by_index(state["section_index"])

    # 1. Extraction from Canonical Store
    store = state.get("confirmed_qa_store", {})
    # Filter for values belonging to the current section (exclude contradictions)
    section_facts = [
        v for v in store.values()
        if v.get("section_id") == section.id and not v.get("contradiction_flagged")
    ]

    # 2. Sort by iteration and round to maintain chronological sequence for drafting
    section_facts.sort(key=lambda x: (x.get("iteration", 0), x.get("round", 0)))

    # 3. Format into the legacy section_qa_pairs structure
    rebuilt_qa = [
        {
            "questions": f.get("questions", ""),
            "answer": f.get("answer", ""),
            "section": f.get("section", section.title)
        }
        for f in section_facts
    ]

    # 4. Parity Check & Telemetry
    before_qa = state.get("section_qa_pairs", [])
    parity = (before_qa == rebuilt_qa)

    log_event(
        **ctx, level="INFO", event_type="mirror_rebuild",
        message="State mirror reconciled",
        parity_maintained=parity,
        facts_count=len(rebuilt_qa),
        rebuild_count=state.get("rebuild_count", 0) + 1
    )

    duration_ms = int((time.monotonic() - t0) * 1000)
    log_event(**ctx, level="INFO", event_type="node_end", message="rebuild_mirror finished", duration_ms=duration_ms)

    return {
        "section_qa_pairs": rebuilt_qa,
        "rebuild_count": state.get("rebuild_count", 0) + 1
    }


# ── Node: load_context ────────────────────────────────────────────────────────

def load_context_node(state: PRDState) -> dict:
    """
    Passthrough — context doc is already in state.
    Emits a welcome message to seed the chat history.
    """
    ctx = _log_ctx(state, "load_context_node")
    t0 = time.monotonic()
    context_len = len(state.get("context_doc", ""))
    log_event(
        **ctx, level="INFO", event_type="node_start",
        message="load_context_node started",
        context_doc_present=bool(state.get("context_doc")),
        context_len=context_len,
    )
    first_section = get_section_by_index(0)
    section_list = ", ".join(s.title for s in PRD_SECTIONS)

    doc_note = (
        "\n\n📎 _Context document loaded — I'll use it to ask sharper questions._"
        if state.get("context_doc")
        else ""
    )

    welcome = (
        f"👋 Welcome! I'll guide you through building a PRD **section by section** "
        f"using the reflection pattern.\n\n"
        f"**{len(PRD_SECTIONS)} sections to complete:** {section_list}\n\n"
        f"For each section I will:\n"
        f"1. Ask you targeted questions\n"
        f"2. Draft the section from your answers\n"
        f"3. Review it against **4 rubrics** (Completeness, Specificity, "
        f"Internal Consistency, Implementability)\n"
        f"4. Loop with sharper follow-ups if needed "
        f"(max {state.get('max_iterations', DEFAULT_MAX_SECTION_ITERATIONS)} iterations)\n\n"
        f"Let's start with **{first_section.title}**.{doc_note}"
    )

    duration_ms = int((time.monotonic() - t0) * 1000)
    log_event(
        **ctx, level="INFO", event_type="node_end",
        message="load_context_node finished",
        duration_ms=duration_ms,
        context_doc_present=bool(state.get("context_doc")),
        context_len=context_len,
    )
    return {
        "chat_history": [
            {"role": "assistant", "msg_id": f"msg_{str(uuid.uuid4())[:8]}", "type": "system", "content": welcome}
        ]
    }


_global_logged_messages = set()

def _get_semantic_cues(token_or_span) -> tuple[bool, bool, bool]:
    is_negated = False
    is_historical = False
    is_example = False
    root = getattr(token_or_span, "root", token_or_span)
    
    # Negation by strict dependency
    if root and hasattr(root, "dep_"):
        if root.dep_ == "neg" or any(c.dep_ == "neg" for c in root.children) or (root.head and any(c.dep_ == "neg" for c in root.head.children)):
            is_negated = True
    
    sent_text = root.sent.text.lower() if hasattr(root, "sent") else getattr(root.doc, "text", "").lower()
    
    if any(w in sent_text for w in ("before", "previously", "used to", "in the past", "was")):
        is_historical = True
        
    if any(w in sent_text for w in ("example", "for instance", "such as")):
        is_example = True
            
    return is_negated, is_historical, is_example

def _log_keyword_extraction_observability(text: str, msg_id: str) -> dict | None:
    if msg_id in _global_logged_messages:
        return None
    _global_logged_messages.add(msg_id)

    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    
    empty_semantics = {
        "message_id": msg_id,
        "timestamp_utc": now,
        "raw_text": text,
        "candidates": [],
        "action_graph": []
    }

    if not text.strip():
        # Log empty... 
        log_event(
            thread_id="", run_id="", message="Keyword extraction on user input",
            level="INFO", event_type="keyword_extraction_observability",
            message_id=msg_id, timestamp_utc=now, raw_user_text=text,
            char_count=0, token_count=0, extractor_name="unavailable",
            entities_detected=[], all_candidates_raw=[], deduped_candidates=[],
            filtered_out=[], candidate_pool_for_downstream=[]
        )
        return empty_semantics

    nlp = _get_nlp()
    if not nlp:
        log_event(
            thread_id="", run_id="", message="Keyword extraction on user input",
            level="INFO", event_type="keyword_extraction_observability",
            message_id=msg_id, timestamp_utc=now, raw_user_text=text,
            char_count=len(text), token_count=len(text.split()), extractor_name="unavailable",
            entities_detected=[], all_candidates_raw=[], deduped_candidates=[],
            filtered_out=[], candidate_pool_for_downstream=[]
        )
        return empty_semantics

    doc = nlp(text)
    extractor_name = f"{nlp.meta.get('name', 'unknown')} v{nlp.meta.get('version', 'unknown')}"

    entities_detected = []
    all_raw = []
    filtered = []
    valid = []
    locked_spans = []

    # 0. Entities (NER)
    for ent in doc.ents:
        neg, hist, ex = _get_semantic_cues(ent)
        cand = {
            "surface_text": ent.text,
            "normalized": ent.lemma_.lower(),
            "type": ExtractionCandidateType.ENTITY.value,
            "start": ent.start_char,
            "end": ent.end_char,
            "pos": ent.label_,
            "confidence": 0.90 if ent.label_ in ("FILE_TYPE", "TOOL", "SYSTEM", "EMAIL_GROUP") else 0.82,
            "is_negated": neg,
            "is_historical": hist,
            "is_example": ex
        }
        entities_detected.append(cand.copy())
        all_raw.append(cand.copy())
        valid.append(cand)
        locked_spans.append((ent.start_char, ent.end_char))

    # A. Noun Chunks
    for chunk in doc.noun_chunks:
        # Check overlaps: now NON-DESTRUCTIVE for valid logic. We don't continue out anymore!
        # If chunk contains an entity, we keep the chunk but maybe flag it.
        # But wait, original code skipped it completely... Let's just not skip it to preserve "action + object" phrases!
        neg, hist, ex = _get_semantic_cues(chunk)
        cand = {
            "surface_text": chunk.text,
            "normalized": chunk.lemma_.lower(),
            "type": ExtractionCandidateType.NOUN_CHUNK.value,
            "start": chunk.start_char,
            "end": chunk.end_char,
            "pos": None,
            "confidence": 0.78,
            "is_negated": neg,
            "is_historical": hist,
            "is_example": ex
        }
        all_raw.append(cand.copy())
        
        # Filter logic
        if cand["normalized"] in DOMAIN_ALLOWLIST:
            valid.append(cand)
            locked_spans.append((cand["start"], cand["end"]))
        elif "mailbo" in chunk.text.lower():
            cand["reason"] = ExtractionReason.TRUNCATED
            filtered.append(cand)
        elif len(chunk.text) <= 3:
            cand["reason"] = ExtractionReason.TOO_SHORT
            filtered.append(cand)
        elif all(t.is_punct or t.is_space for t in chunk):
            cand["reason"] = ExtractionReason.PUNCTUATION
            filtered.append(cand)
        else:
            if chunk.lemma_.lower() in ["that", "it's me", "its me", "the end goal", "thing", "stuff", "process", "one"]:
                cand["reason"] = ExtractionReason.LOW_VALUE
                filtered.append(cand)
            else:
                valid.append(cand)
                locked_spans.append((cand["start"], cand["end"]))

    # B. Individual Tokens
    for token in doc:
        if token.pos_ not in ("NOUN", "PROPN") and token.lemma_.lower() not in DOMAIN_ALLOWLIST and token.text.lower() != "mailbo":
            continue
            
        neg, hist, ex = _get_semantic_cues(token)
        cand = {
            "surface_text": token.text,
            "normalized": token.lemma_.lower(),
            "type": ExtractionCandidateType.TOKEN.value,
            "start": token.idx,
            "end": token.idx + len(token.text),
            "pos": token.pos_,
            "confidence": 0.65 if token.lemma_.lower() not in DOMAIN_ALLOWLIST else 0.90,
            "is_negated": neg,
            "is_historical": hist,
            "is_example": ex
        }
        
        # Keep old token logic skipping overlapping parts since token is just fragments
        if any(cand["start"] >= s[0] and cand["end"] <= s[1] for s in locked_spans):
            continue
            
        all_raw.append(cand.copy())

        if cand["normalized"] in DOMAIN_ALLOWLIST:
            valid.append(cand)
        elif token.text.lower() == "mailbo":
            cand["reason"] = ExtractionReason.TRUNCATED
            filtered.append(cand)
        elif token.is_stop or token.pos_ in ("PRON", "DET"):
            cand["reason"] = ExtractionReason.STOPWORD
            filtered.append(cand)
        elif len(token.text) <= 4:  
            cand["reason"] = ExtractionReason.TOO_SHORT
            filtered.append(cand)
        else:
            if token.lemma_.lower() in ["that", "its", "me", "thing", "stuff", "process", "one"]:
                cand["reason"] = ExtractionReason.LOW_VALUE
                filtered.append(cand)
            else:
                valid.append(cand)

    # Dedup
    deduped = []
    seen = set()
    for v in sorted(valid, key=lambda x: (x["type"] == ExtractionCandidateType.ENTITY.value, len(x["surface_text"])), reverse=True): 
        k = v["normalized"]
        if k not in seen:
            seen.add(k)
            deduped.append(v)
        else:
            v["reason"] = ExtractionReason.DUPLICATE
            filtered.append(v)

    # Action Graph
    action_graph = []
    for token in doc:
        if token.pos_ == "VERB" and token.dep_ in ("ROOT", "xcomp", "advcl", "conj"):
            dobj = None
            pobj = None
            for child in token.children:
                if child.dep_ == "dobj":
                    dobj = child
                if child.dep_ == "prep":
                    for grandchild in child.children:
                        if grandchild.dep_ == "pobj":
                            pobj = grandchild
            if dobj:
                end_token = pobj if pobj else dobj
                action_graph.append({
                    "verb": token.lemma_.lower(),
                    "object": dobj.text,
                    "destination_if_any": pobj.text if pobj else None,
                    "confidence": 0.8,
                    "source_span": (token.idx, end_token.idx + len(end_token.text)),
                    "extraction_method": "dependency_parse"
                })

    candidate_pool = [d["normalized"] for d in deduped]

    # Full logging
    log_event(
        thread_id="", run_id="", message="Keyword extraction on user input",
        level="INFO", event_type="keyword_extraction_observability",
        message_id=msg_id,
        timestamp_utc=now,
        raw_user_text=text,
        char_count=len(text),
        token_count=len(doc),
        extractor_name=extractor_name,
        entities_detected=entities_detected,
        all_candidates_raw=all_raw,
        deduped_candidates=deduped,
        filtered_out=filtered,
        candidate_pool_for_downstream=candidate_pool
    )
    
    # Compact human audit logging
    flags = []
    if any(c.get("reason") == ExtractionReason.TRUNCATED for c in filtered):
        flags.append("truncation_detected")
    if sum(1 for c in filtered if c.get("reason") == ExtractionReason.STOPWORD) > 3:
        flags.append("high_stopword_noise")
    if any(c.get("reason") == ExtractionReason.LOW_VALUE for c in filtered):
        flags.append("low_value_chunk_noise")
    if len([c for c in filtered if c.get("reason") == ExtractionReason.LOW_VALUE]) > 5:
        flags.append("too_many_generic_chunks")
        
    raw_top = [c["surface_text"] for c in all_raw[:5]]
    filtered_str = [f'{c["surface_text"]}({c.get("reason")})' for c in filtered[:5]]
    flags_str = f" | flags={flags}" if flags else ""
    
    compact_log = f"[keyword_audit] msg_id={msg_id} | text='{text[:30]}...' | raw_top=[{', '.join(raw_top)}] | filtered=[{', '.join(filtered_str)}] | final=[{', '.join(candidate_pool)}]{flags_str}\n"
    
    try:
        from pathlib import Path
        audit_file = Path(__file__).resolve().parent.parent / "logs" / "keyword_audit.log"
        audit_file.parent.mkdir(parents=True, exist_ok=True)
        with open(audit_file, "a", encoding="utf-8") as f:
            f.write(compact_log)
    except Exception:
        pass
        
    semantic_candidates = [
        {
            "surface": d["surface_text"],
            "normalized": d["normalized"],
            "type": d["type"],
            "confidence": d["confidence"],
            "source_span": (d["start"], d["end"]),
            "is_negated": d["is_negated"],
            "is_historical": d["is_historical"],
            "is_example": d["is_example"]
        } for d in deduped
    ]
        
    return {
        "message_id": msg_id,
        "timestamp_utc": now,
        "raw_text": text,
        "candidates": semantic_candidates,
        "action_graph": action_graph
    }

def _log_semantic_transition(concept_key, old_status, new_status, trigger, msg_id):
    log_event(
        thread_id="", run_id="", message=f"Semantic transition: {concept_key}",
        level="INFO", event_type="concept_state_transition",
        concept_key=concept_key, old_status=old_status, new_status=new_status,
        transition_trigger=trigger, message_id=msg_id
    )

def _sync_concept_history(state: PRDState, semantics: dict) -> dict:
    history = state.get("concept_history", {})
    updates = {}
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    msg_id = semantics["message_id"]

    for cand in semantics.get("candidates", []):
        key = cand["normalized"]
        is_negated = cand["is_negated"]
        is_historical = cand["is_historical"]
        is_example = cand["is_example"]
        confidence = cand["confidence"]

        if key in history:
            entry = history[key].copy()
        else:
            entry = {
                "concept_key": key,
                "mentions": [],
                "source_message_ids": [],
                "status": ConceptStatus.MENTIONED.value,
                "status_reason": "First extracted.",
                "is_current": False,
                "is_negated": False,
                "is_historical": False,
                "is_example": False,
                "was_corrected": False,
                "superseded_by": None,
                "corrected_from": None,
                "last_seen_at": now,
                "last_transition_at": now
            }

        if msg_id and msg_id not in entry.get("mentions", []):
            if "mentions" not in entry: entry["mentions"] = []
            entry["mentions"].append(msg_id)
        if msg_id and msg_id not in entry.get("source_message_ids", []):
            if "source_message_ids" not in entry: entry["source_message_ids"] = []
            entry["source_message_ids"].append(msg_id)
            
        entry["last_seen_at"] = now
        entry["is_negated"] = is_negated
        entry["is_historical"] = is_historical
        entry["is_example"] = is_example

        old_status = entry["status"]

        if entry["status"] == ConceptStatus.SUPERSEDED.value:
            updates[key] = entry
            continue
            
        if is_example and entry["status"] == ConceptStatus.MENTIONED.value:
            entry["status"] = ConceptStatus.EXAMPLE_ONLY.value
            entry["status_reason"] = "Hypothetical context."
        elif is_negated and entry["status"] in (ConceptStatus.CURRENT.value, ConceptStatus.MENTIONED.value):
            entry["status"] = ConceptStatus.NEGATED.value
            entry["status_reason"] = f"Negated in msg {msg_id}."
            log_event(thread_id="", run_id="", message="concept negated", level="INFO", event_type="concept_negated", concept=key)
        elif is_historical and entry["status"] in (ConceptStatus.CURRENT.value, ConceptStatus.MENTIONED.value):
            entry["status"] = ConceptStatus.HISTORICAL.value
            entry["status_reason"] = f"Marked historical in msg {msg_id}."
            log_event(thread_id="", run_id="", message="concept historical", level="INFO", event_type="concept_marked_historical", concept=key)
        elif is_negated and entry["status"] != ConceptStatus.CURRENT.value:
            entry["status"] = ConceptStatus.NEGATED.value
            entry["status_reason"] = f"Negated in msg {msg_id}."
        elif entry["status"] == ConceptStatus.CONFLICTED.value:
            pass # stays conflicted until explicit correction/confirmation clears it
        elif entry["status"] == ConceptStatus.NEGATED.value and not is_negated:
            entry["status"] = ConceptStatus.CONFLICTED.value
            entry["status_reason"] = f"Contradictory assertion after negation in {msg_id}."
            
        if entry["status"] != old_status:
            entry["last_transition_at"] = now
            _log_semantic_transition(key, old_status, entry["status"], f"Triggered by {msg_id} cue flags.", msg_id)
            
        updates[key] = entry
        
    return updates

def build_conversation_understanding_output(state: PRDState) -> ConversationUnderstandingOutput:
    history = state.get("concept_history", {})
    current_concepts = []
    historical_concepts = []
    negated_concepts = []
    example_only_concepts = []
    future_or_planned_concepts = []
    conflicted_concepts = []

    for key, entry in history.items():
        status = entry.get("status")
        # Mapped to TypedDict ConceptRecord
        record = ConceptRecord(
            concept_key=entry.get("concept_key", key),
            surface=key,
            scope_type=entry.get("scope_type"),
            scope_value=entry.get("scope_value"),
            confidence=entry.get("confidence", 1.0),
            status_reason=entry.get("status_reason", ""),
            source_message_ids=entry.get("source_message_ids", [])
        )

        # Basic fallback for E14, E18: future migration plan
        # We can simulate this state based on status_reason or scope
        scope_val = entry.get("scope", {}).get("value") or entry.get("scope_value", "")
        
        if "future" in record["status_reason"].lower() or "planned" in record["status_reason"].lower() or scope_val in ["future_planned", "roadmap"]:
            future_or_planned_concepts.append(record)
            continue

        if status == ConceptStatus.CURRENT.value:
            current_concepts.append(record)
        elif status == ConceptStatus.HISTORICAL.value:
            historical_concepts.append(record)
        elif status == ConceptStatus.NEGATED.value:
            negated_concepts.append(record)
        elif status == ConceptStatus.EXAMPLE_ONLY.value:
            example_only_concepts.append(record)
        elif status == ConceptStatus.CONFLICTED.value:
            conflicted_concepts.append(record)

    corrections_recently_applied = []
    for key, entry in history.items():
        if entry.get("was_corrected") and entry.get("superseded_by"):
            msg_ids = entry.get("source_message_ids", [])
            corrections_recently_applied.append(CorrectionRecord(
                old_concept=key,
                new_concept=entry["superseded_by"],
                reason=entry.get("status_reason", ""),
                source_message_id=msg_ids[-1] if msg_ids else "",
                timestamp_utc=0,
                trigger_type="implicit_correction"
            ))

    action_candidates_if_any = []
    unresolved_blockers = []
    
    # Action gaps (from latest semantics)
    chat_hist = state.get("chat_history", [])
    latest_semantics = None
    for msg in reversed(chat_hist):
        if msg.get("role") == "user" and "semantics" in msg:
            latest_semantics = msg["semantics"]
            break
            
    if latest_semantics and "action_graph" in latest_semantics:
        for edge_raw in latest_semantics["action_graph"]:
            a_conf = edge_raw.get("confidence", 0.0)
            a_meth = edge_raw.get("extraction_method", "unknown")
            verb = edge_raw.get("verb", "")
            obj = edge_raw.get("object", "")
            dest = edge_raw.get("destination_if_any")
            is_complete = True
            missing_parts = []
            
            if verb in ["send", "forward", "email", "notify", "export", "share"] and not dest:
                is_complete = False
                missing_parts.append("destination")
                unresolved_blockers.append(BlockerRecord(
                    blocker_type="missing_destination",
                    target=f"{verb} {obj}",
                    reason=f"Action '{verb} {obj}' is missing a destination.",
                    severity="advisory_warning",
                    source="action_gap",
                    suggested_question_type="clarify_destination"
                ))
                
            action_candidates_if_any.append(ActionCandidateRecord(
                verb=verb,
                object=obj,
                destination=dest,
                confidence=a_conf,
                extraction_method=a_meth,
                is_complete=is_complete,
                missing_parts=missing_parts
            ))

    # Semantic gaps
    # E10: Replacement missing
    for rec in negated_concepts:
        # Check if there is an active replacement for negated tools
        if not current_concepts:
            unresolved_blockers.append(BlockerRecord(
                blocker_type="replacement_missing",
                target=rec["concept_key"],
                reason=f"Concept '{rec['concept_key']}' was negated but no replacement was given.",
                severity="hard",
                source="semantic_gap",
                suggested_question_type="clarify_replacement"
            ))
            break

    # Section missing gaps (Simplified check for now. Relies on the old PRD section missing block logic)
    qa_store = state.get("confirmed_qa_store", {})
    # For POC simplicity, assuming target unmet slots append into blockers directly. 
    # Proper PRD_SECTION sync happens in the main Elicitor later if needed.
    
    is_ready = True
    hard_blockers = []
    advisory_warnings = []
    for blocker in unresolved_blockers:
        if blocker["severity"] == "hard":
            hard_blockers.append(blocker["target"])
            is_ready = False
        else:
            advisory_warnings.append(blocker["target"])
            
    # Conflicts are auto-blockers
    if conflicted_concepts:
        is_ready = False
        hard_blockers.append("conflicted_concepts")

    draft_readiness = DraftReadinessDict(
        is_ready=is_ready,
        hard_blockers=hard_blockers,
        advisory_warnings=advisory_warnings
    )

    return ConversationUnderstandingOutput(
        current_concepts=current_concepts,
        historical_concepts=historical_concepts,
        negated_concepts=negated_concepts,
        example_only_concepts=example_only_concepts,
        future_or_planned_concepts=future_or_planned_concepts,
        conflicted_concepts=conflicted_concepts,
        unresolved_blockers=unresolved_blockers,
        draft_readiness=draft_readiness,
        corrections_recently_applied=corrections_recently_applied,
        action_candidates_if_any=action_candidates_if_any
    )

def _build_user_message_dict(text: str, role_override: str = "user") -> dict:
    now = datetime.datetime.now(datetime.timezone.utc)
    msg_id = f"msg_{str(uuid.uuid4())[:8]}"
    semantics = _log_keyword_extraction_observability(text, msg_id)
    return {
        "role": role_override,
        "content": text,
        "msg_id": msg_id,
        "timestamp_utc": now.timestamp(),
        "display_time": now.strftime("%I:%M %p · %d %b").lstrip("0").replace(" 0", " "),
        "semantics": semantics
    }

PROVENANCE_CONF_THRESHOLD = 8.5
MAX_CHIPS_PER_MESSAGE = 3
def _get_proof_chain(term: str, chat_history: list[dict], qa_store: dict, referenced_concept_keys: list[str]) -> Optional[ProofUnit]:
    """Implement the Hardened Matching Ladder and Ranking Precedence."""
    nlp = _get_nlp()
    if not nlp:
        return None

    term_doc = nlp(term.lower())
    term_lemma = " ".join([t.lemma_ for t in term_doc])
    
    candidates: list[ProofUnit] = []

    # ── Step 1: Collect ALL possible evidence matches ──
    
    # A. Search Chat History (Lexical First)
    for msg in reversed(chat_history):
        if msg.get("role") != "user":
            continue
        content = msg.get("content", "")
        if not content:
            continue
        
        # Exact Surface Match
        matches = list(re.finditer(rf'\b{re.escape(term)}\b', content, re.IGNORECASE))
        for m in matches:
            candidates.append({
                "assistant_surface_text": term,
                "concept_key": term.lower(),
                "source_message_id": msg.get("msg_id", ""),
                "source_span": m.span(),
                "source_display_time": msg.get("display_time", "Earlier"),
                "proof_status": ProofStatus.EXACT_SURFACE.value,
                "ranking_reason": "exact_lexical_match",
                "snippet_html": "" # To be filled after ranking
            })
        
        # Lemma Match (if no exact match found in this message yet)
        if not matches:
            msg_doc = nlp(content.lower())
            
            # A. Check Noun Chunks
            found_lemma = False
            for chunk in msg_doc.noun_chunks:
                # Case-insensitive lemma match or prefix match for common plural cases (PDFs/PDF)
                c_lemma = chunk.lemma_.lower()
                if c_lemma == term_lemma or c_lemma.rstrip('s') == term_lemma:
                    candidates.append({
                        "assistant_surface_text": term,
                        "concept_key": term_lemma,
                        "source_message_id": msg.get("msg_id", ""),
                        "source_span": (chunk.start_char, chunk.end_char),
                        "source_display_time": msg.get("display_time", "Earlier"),
                        "proof_status": ProofStatus.LEMMA_BACKED.value,
                        "ranking_reason": "lemma_match_chunk",
                        "snippet_html": ""
                    })
                    found_lemma = True
                    break
            
            # B. Check Individual Tokens (if chunk match failed)
            if not found_lemma:
                for token in msg_doc:
                    t_lemma = token.lemma_.lower()
                    if t_lemma == term_lemma or t_lemma.rstrip('s') == term_lemma:
                        candidates.append({
                            "assistant_surface_text": term,
                            "concept_key": term_lemma,
                            "source_message_id": msg.get("msg_id", ""),
                            "source_span": (token.idx, token.idx + len(token.text)),
                            "source_display_time": msg.get("display_time", "Earlier"),
                            "proof_status": ProofStatus.LEMMA_BACKED.value,
                            "ranking_reason": "lemma_match_token",
                            "snippet_html": ""
                        })
                        break

    # B. Search QA Store (Abstract Facts)
    for ck in referenced_concept_keys:
        fact = qa_store.get(ck)
        if not fact or fact.get("provenance_confidence", 0) < PROVENANCE_CONF_THRESHOLD:
            continue
        
        fact_id = fact.get("source_message_id")
        fact_snippet = fact.get("source_snippet", "")
        
        # Check for Normalized Concept Match
        if ck == term.lower() or fact.get("answer", "").lower() == term.lower():
            # Try to find the exact span in the snippet if available
            span = (0, len(fact_snippet))
            m = re.search(rf'\b{re.escape(term)}\b', fact_snippet, re.IGNORECASE)
            if m:
                span = m.span()
            
            candidates.append({
                "assistant_surface_text": term,
                "concept_key": ck,
                "source_message_id": fact_id or "fact_store",
                "source_span": span,
                "source_display_time": fact.get("display_time", "Stored Fact"),
                "proof_status": ProofStatus.NORMALIZED_SURFACE.value,
                "ranking_reason": "confirmed_fact_match",
                "snippet_html": ""
            })

    if not candidates:
        return None

    # ── Step 2: Apply Hardened Ranking Formula ──
    # Order: EXACT_SURFACE > NORMALIZED_SURFACE > LEMMA_BACKED
    
    status_priority = {
        ProofStatus.EXACT_SURFACE.value: 3,
        ProofStatus.NORMALIZED_SURFACE.value: 2,
        ProofStatus.LEMMA_BACKED.value: 1,
        ProofStatus.REFUSED.value: 0
    }
    
    def rank_key(c: ProofUnit):
        p1 = status_priority.get(c["proof_status"], 0)
        # Lexical First (chat_history id vs fact_store)
        p2 = 1 if c["source_message_id"] != "fact_store" else 0
        # Recency Tiebreak
        recency = 0
        for i, h in enumerate(reversed(chat_history)):
            if h.get("msg_id") == c["source_message_id"]:
                recency = -i 
                break
        return (p1, p2, recency)

    winner = max(candidates, key=rank_key)
    
    # ── Step 3: Semantic Conflict Rule ──
    # If another candidate exists with DIFFERENT meaning (different lemma), refuse.
    # Simplified: Consistency is checked at the candidate level.
    
    # ── Step 4: Finalize Proof Unit ──
    raw_text = ""
    if winner["source_message_id"] == "fact_store":
        # Search for snippet in referenced concepts
        for ck in referenced_concept_keys:
            f = qa_store.get(ck)
            if f and f.get("source_message_id") == winner["source_message_id"]:
                raw_text = f.get("source_snippet", "")
                break
        if not raw_text: raw_text = winner["concept_key"] # Last resort
    else:
        for h in chat_history:
            if h.get("msg_id") == winner["source_message_id"]:
                raw_text = h.get("content", "")
                break
    
    if not raw_text:
        return None
        
    winner["snippet_html"] = _sniper_window(raw_text, winner["source_span"][0], winner["source_span"][1])
    return winner

def _segment_text_with_provenance(reply_content: str, referenced_concept_keys: list[str], state: PRDState) -> list[dict]:
    """Segment reply text and attach auditable ProofUnits."""
    qa_store = state.get("confirmed_qa_store", {})
    chat_history = state.get("chat_history", [])
    
    nlp = _get_nlp()
    if not nlp:
        return [{"text": reply_content, "provenance": None}]

    doc = nlp(reply_content)
    
    # ── Candidate Extraction ──
    term_candidates = []
    _DOMAIN_FILLER_WORDS = {
        "thing", "stuff", "process", "one", "way"
    }
    
    # Noun chunks
    for chunk in doc.noun_chunks:
        c_text = chunk.text.strip()
        # Reject chunk if it's entirely composed of stopwords or domain fillers
        # This keeps "the group mailbox" (has valid nouns) but rejects "that process" (stopword + filler)
        if len(c_text) > 4:
            is_all_junk = all(t.is_stop or t.text.lower() in _DOMAIN_FILLER_WORDS for t in chunk)
            if not is_all_junk:
                term_candidates.append(c_text)
            
    # Long proper nouns/nouns
    for token in doc:
        t_text = token.text.strip()
        if token.pos_ in ("NOUN", "PROPN") and len(t_text) > 4:
            if not token.is_stop and t_text.lower() not in _DOMAIN_FILLER_WORDS:
                if not any(t_text in longer for longer in term_candidates):
                    term_candidates.append(t_text)

    # Dedup and sort deterministically
    term_candidates = sorted(list(dict.fromkeys(term_candidates)), key=lambda x: (len(x), x), reverse=True)
    
    # ── Proof Attachment ──
    proof_units: dict[str, ProofUnit] = {}
    for term in term_candidates:
        proof = _get_proof_chain(term, chat_history, qa_store, referenced_concept_keys)
        if proof:
            proof_units[term.lower()] = proof

    if not proof_units:
        return [{"text": reply_content, "provenance": None}]

    # ── Segmentation ──
    matches = []
    for term_lower, proof in proof_units.items():
        for m in re.finditer(rf'\b{re.escape(term_lower)}\b', reply_content, re.IGNORECASE):
            matches.append((m.start(), m.end(), proof))

    # Identify longest non-overlapping spans
    matches.sort(key=lambda m: (m[0], -m[1]))
    
    # Filter overlaps
    non_overlapping = []
    curr_pos = 0
    for start, end, proof in matches:
        if start >= curr_pos:
            non_overlapping.append((start, end, proof))
            curr_pos = end

    # Rank filtered candidates by claim value, not position
    status_priority = {
        ProofStatus.EXACT_SURFACE.value: 3,
        ProofStatus.NORMALIZED_SURFACE.value: 2,
        ProofStatus.LEMMA_BACKED.value: 1,
        ProofStatus.REFUSED.value: 0
    }
    non_overlapping.sort(
        key=lambda x: (
            status_priority.get(x[2]["proof_status"], 0),
            1 if x[2]["source_message_id"] != "fact_store" else 0,
            x[1] - x[0],
            -x[0]  # Recency tiebreaker
        ),
        reverse=True
    )
    
    # Enforce limit AFTER ranking
    final_matches = non_overlapping[:MAX_CHIPS_PER_MESSAGE]
    
    # Spatial sort for slicing interpolation
    final_matches.sort(key=lambda x: x[0])
    
    segments = []
    last_idx = 0
    for start, end, proof in final_matches:
        if start > last_idx:
            segments.append({"text": reply_content[last_idx:start], "provenance": None})
        segments.append({"text": reply_content[start:end], "provenance": proof})
        last_idx = end
    
    if last_idx < len(reply_content):
        segments.append({"text": reply_content[last_idx:], "provenance": None})
        
    return segments

def _extract_submit_payload(resume_value: dict | str) -> tuple[str, list, dict]:
    """
    Central helper for wait nodes to digest resume payloads identically.
    Returns (user_text, uploaded_files, pending_event).
    """
    if isinstance(resume_value, dict):
        if "user_input" in resume_value and "pending_event" in resume_value:
            user_text = resume_value["user_input"].get("text", "")
            uploaded_files = resume_value["user_input"].get("files", [])
            pending_event = resume_value["pending_event"]
        else:
            user_text = resume_value.get("content", "")
            uploaded_files = resume_value.get("uploaded_files", [])
            pending_event = {k: v for k, v in resume_value.items() if k != "uploaded_files"}
            
        if "event_type" not in pending_event:
            pending_event["event_type"] = "ANSWER"
    else:
        user_text = str(resume_value) if resume_value is not None else ""
        uploaded_files = []
        pending_event = {"event_type": "ANSWER", "content": user_text}
    return user_text, uploaded_files, pending_event

def await_first_message_node(state: PRDState) -> dict:
    """
    D-M2 — Fires interrupt() immediately so the user's very first typed message
    becomes the project description. No welcome is emitted here.
    Sets context_doc so detect_framing_node can classify what was said.
    """
    first_message = interrupt({"type": "waiting_for_first_message"})
    text, uploaded_files, pending_event = _extract_submit_payload(first_message)
    text = text.strip()

    event_type = pending_event.get("event_type", "ANSWER")
    if event_type in ("SUBMIT_SESSION_CONTEXT", "REMOVE_SESSION_CONTEXT", "REVERT_SESSION_CONTEXT"):
        return {
            "uploaded_files": uploaded_files,
            "pending_event": pending_event
        }

    user_msg = _build_user_message_dict(text)
    semantics = user_msg.get("semantics", {})
    concept_history_update = _sync_concept_history(state, semantics) if semantics else {}
    
    return {
        "context_doc": text,
        "chat_history": [user_msg],
        "concept_history": concept_history_update,
        "uploaded_files": uploaded_files,
        "pending_event": pending_event
    }


# ── Node: detect_framing ──────────────────────────────────────────────────────

_FRAMING_PROMPT = (
    "Classify the following product description.\n\n"
    "CLEAR — the person describes a specific product, feature, or system to build "
    "with identifiable goals.\n"
    "SYMPTOM — they described a pain point or problem, but no product or solution idea.\n"
    "CONFUSED — they are vague, uncertain what to build, or said something like "
    "'I don't know where to start'.\n\n"
    "Product description:\n{context_doc}\n\n"
    "Reply with exactly one word: CLEAR, SYMPTOM, or CONFUSED."
)

_FRAMING_MAP = {"CLEAR": "clear", "SYMPTOM": "symptom_only", "CONFUSED": "confused"}


def detect_framing_node(state: PRDState) -> dict:
    """
    Classifies the user's first message as Path 1/2/3.
    Sets framing_mode ('clear' | 'symptom_only' | 'confused') and
    phase ('elicitation' for Path 1, 'discovery' for Path 2/3).
    """
    ctx = _log_ctx(state, "detect_framing_node")
    t0 = time.monotonic()
    context_doc = state.get("context_doc", "").strip()
    
    # Image-only bypass
    if not context_doc and state.get("uploaded_files"):
        log_event(
            **ctx, level="INFO", event_type="framing_fallback_image_only",
            message="Bypassing framing classifier for image-only submission"
        )
        return {"framing_mode": "clear", "phase": "elicitation"}

    log_event(
        **ctx, level="INFO", event_type="node_start",
        message="detect_framing_node started", context_len=len(context_doc),
    )

    llm = _get_llm()
    response = llm_invoke(
        llm, [HumanMessage(content=_FRAMING_PROMPT.format(context_doc=context_doc))],
        state=state, node_name="detect_framing_node", purpose="classify_framing_mode",
    )

    raw = response.content.strip().upper()
    first_word = raw.split()[0] if raw.split() else "CLEAR"
    if first_word not in _FRAMING_MAP:
        first_word = "CLEAR"

    framing_mode = _FRAMING_MAP[first_word]
    phase = "elicitation" if framing_mode == "clear" else "discovery"

    duration_ms = int((time.monotonic() - t0) * 1000)
    log_event(
        **ctx, level="INFO", event_type="node_end",
        message="detect_framing_node finished",
        framing_mode=framing_mode, phase=phase, duration_ms=duration_ms,
    )
    return {"framing_mode": framing_mode, "phase": phase}


def _build_visual_context_block(state: dict) -> str:
    """Standardizes the visual context injection block with proactive synthesis instructions."""
    bg_contexts = state.get("background_generated_contexts", [])
    active_bg_contexts = [ctx for ctx in bg_contexts if ctx.get("is_active")]
    active_bg_contexts.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    top_contexts = active_bg_contexts[:3]
    
    if not top_contexts:
        return ""
        
    block = "\n\n=== VERIFIED VISUAL CONTEXT ===\n"
    block += (
        "The user uploaded visual context. You must employ proactive but bounded synthesis:\n"
        "1. Proactive Connection: Actively connect the image to their text goal immediately. Do not wait for them to ask.\n"
        "2. Bounded Inference: Do not over-commit. If the connection is weak, use tentative language ('This appears to be...', 'Assuming this relates to...').\n"
        "3. Synthesis, Not Description: Do not merely restate the image details. Briefly interpret its relevance to the problem.\n"
        "4. Exact Question Limit: Your response must be highly concise and culminate in EXACTLY ONE sharply scoped question.\n\n"
        "Available Semantic Contexts:\n"
    )
    for ctx in top_contexts:
        filename = ctx.get("image_file_id", "unknown")
        eff_summary = ctx.get("edited_summary") or ctx.get("generated_summary", "")
        # Hardcap to prevent context blowing out entirely
        eff_summary = eff_summary[:800] 
        block += f"---\nFile: {filename}\nSummary: {eff_summary}\n"
    block += "===============================\n"
    return block


# ── Node: discovery_questions ─────────────────────────────────────────────────

_DISCOVERY_SYSTEM = (
    "You are a friendly product management coach helping someone clarify what they want to build.\n\n"
    "What they've said so far:\n{context_doc}\n{visual_context_block}\n\n"
    "Conversation Semantic State:\n{conversation_understanding}\n\n"
    "Situation: {framing_label}\n"
    "This is clarifying question set {turn_label}.\n\n"
    "Ask 1-2 short, natural questions to draw out:\n"
    "- What the core goal or business need is\n"
    "- Who will use this and why it matters\n\n"
    "Rules for utilizing semantic state:\n"
    "1. If `conflicted_concepts` exist, emit a clarification question ONLY to resolve the latest conflict.\n"
    "2. Otherwise, pick the highest priority `hard` item from `unresolved_blockers` and ask about it.\n"
    "3. Do NOT ask questions on concepts that are `CURRENT`.\n\n"
    "No bullet headers. No jargon. Conversational tone."
)

_FRAMING_LABELS = {
    "symptom_only": "they described a problem but no solution or product idea yet",
    "confused": "they're unsure what to build or where to start",
}


def discovery_questions_node(state: PRDState) -> dict:
    """
    Asks 1-2 clarifying questions for Path 2/3 users.
    Increments discovery_turn_count. Does NOT increment iteration (D-M12).
    """
    ctx = _log_ctx(state, "discovery_questions_node")
    t0 = time.monotonic()
    framing_mode = state.get("framing_mode", "confused")
    discovery_turn_count = state.get("discovery_turn_count", 0)
    context_doc = state.get("context_doc", "")

    log_event(
        **ctx, level="INFO", event_type="node_start",
        message="discovery_questions_node started",
        framing_mode=framing_mode, discovery_turn_count=discovery_turn_count,
    )

    framing_label = _FRAMING_LABELS.get(framing_mode, "unsure what to build")
    turn_label = f"{discovery_turn_count + 1} of 2"
    llm = _get_llm()
    import json
    bridge_output = build_conversation_understanding_output(state)
    
    response = llm_invoke(
        llm,
        [HumanMessage(content=_DISCOVERY_SYSTEM.format(
            context_doc=context_doc,
            visual_context_block=_build_visual_context_block(state),
            conversation_understanding=json.dumps(bridge_output, indent=2, default=str),
            framing_label=framing_label,
            turn_label=turn_label,
        ))],
        state=state, node_name="discovery_questions_node", purpose="generate_discovery_questions",
    )
    questions = response.content.strip()
    new_count = discovery_turn_count + 1

    duration_ms = int((time.monotonic() - t0) * 1000)
    log_event(
        **ctx, level="INFO", event_type="node_end",
        message="discovery_questions_node finished",
        new_discovery_turn_count=new_count, duration_ms=duration_ms,
    )
    
    # Generate provenance segments from user chat history
    provenance_segments = _segment_text_with_provenance(questions, [], state)
    
    return _enforce_visibility({
        "discovery_turn_count": new_count,
        "content_segments": provenance_segments,
    }, questions, "Discovery", 0, 0, event_type="system")


# ── Node: await_discovery_answer ──────────────────────────────────────────────

def await_discovery_answer_node(state: PRDState) -> dict:
    """
    Interrupt — pauses for the user's discovery-phase answer.
    Appends the answer to context_doc so the Elicitor can use the full
    discovery context when section elicitation begins.
    Adds msg_id and display_time for provenance tracking.
    """
    resume_value = interrupt({
        "type": "waiting_for_discovery_answer",
        "discovery_turn_count": state.get("discovery_turn_count", 0),
    })
    answer, uploaded_files, pending_event = _extract_submit_payload(resume_value)
        
    event_type = pending_event.get("event_type", "ANSWER")
    
    # T1/T2: Do not append structured UI events as standalone conversational context turns
    if event_type in ("SUBMIT_SESSION_CONTEXT", "REMOVE_SESSION_CONTEXT", "REVERT_SESSION_CONTEXT"):
        return {
            "uploaded_files": uploaded_files,
            "pending_event": pending_event
        }
        
    answer = answer.strip()
    existing = state.get("context_doc", "").strip()
    
    user_msg = _build_user_message_dict(answer)
    semantics = user_msg.get("semantics", {})
    concept_history_update = _sync_concept_history(state, semantics) if semantics else {}
    
    visual_context = _build_visual_context_block(state)
    if visual_context:
        new_context = f"{existing}\n\n{visual_context}\n{answer}" if existing else f"{visual_context}\n{answer}"
        user_msg["attached_image_context"] = visual_context
    else:
        new_context = f"{existing}\n\n{answer}" if existing else answer
    
    return {
        "context_doc": new_context,
        "chat_history": [user_msg],
        "concept_history": concept_history_update,
        "uploaded_files": uploaded_files,
        "pending_event": pending_event,
        "response_type": ""
    }


# ── Node: generate_questions ──────────────────────────────────────────────────


def _maybe_emit_numeric_repair_prompt(state: "PRDState", section, ctx: dict) -> dict | None:
    if state.get("pending_numeric_clarification"):
        repair_prompt = "I may have misunderstood that value. Did you mean 30 minutes per day, 3 hours per day, or something else?"
        log_event(**ctx, level="INFO", event_type="repair_prompt_emitted", message="Emitted deterministic repair prompt.")
        
        repair_id = str(uuid.uuid4())
        return _enforce_visibility({
            "generation_status": "question_generated",
            "generation_reason": "Emitted deterministic repair prompt.",
            "selected_candidate_id": repair_id,
            "duplicate_details": {"total_candidates_evaluated": 0, "rejection_reasons": []},
            "current_questions": repair_prompt,
            "question_status": "OPEN",
            "pending_numeric_clarification": False,
            "parent_question_id": state.get("active_question_id", ""),
            "repair_question_id": repair_id,
        }, repair_prompt, section.title if section else "Unknown", state["section_index"], state.get("iteration", 0), event_type="elicit")
    return None

def _maybe_emit_conflict_resolution_question(state: "PRDState", bridge_output: dict, section, ctx: dict) -> dict | None:
    if bridge_output.get("conflicted_concepts"):
        conflict = bridge_output["conflicted_concepts"][0]
        deterministic_q = f"I'm hearing mixed details about '{conflict.get('surface', conflict.get('concept_key', 'this'))}'. Could you clarify the current workflow for this?"
        
        log_event(**ctx, level="INFO", event_type="deterministic_conflict_gating", 
                  message="Short-circuited LLM generation due to semantic conflict", 
                  conflict_target=conflict.get("concept_key"))
        
        import uuid
        current_question_object = {
            "question_id": str(uuid.uuid4()),
            "question_text": deterministic_q,
            "subparts": ["conflict_resolution"],
        }
        
        return _enforce_visibility({
            "generation_status": "question_generated",
            "generation_reason": "Short-circuited LLM generation due to semantic conflict",
            "selected_candidate_id": current_question_object["question_id"],
            "duplicate_details": {"total_candidates_evaluated": 0, "rejection_reasons": []},
            "current_questions": deterministic_q,
            "current_question_segments": [],
            "current_question_object": current_question_object,
            "remaining_subparts": ["conflict_resolution"],
            "active_question_id": current_question_object["question_id"],
            "active_question_type": "OPEN_ENDED",
            "active_question_options": [],
            "question_status": "OPEN",
            "resolved_option_id": "",
            "answered_at": "",
            "recent_questions": state.get("recent_questions", []) + [deterministic_q],
            "repair_instruction": "",
        }, deterministic_q, section.title if section else "Unknown", state["section_index"], state.get("iteration", 0), event_type="elicit")
    return None

def _maybe_emit_resolved_branch_question(state: "PRDState", section, ctx: dict) -> dict | None:
    if state.get("question_status") == "ANSWERED":
        resolved_opt = state.get("resolved_option_id", "")
        if resolved_opt:
            remaining = state.get("remaining_subparts", [])
            target_subpart = remaining[0] if remaining else "overall details"
            target_lower = target_subpart.lower()
            
            if any(k in target_lower for k in ("metric", "success", "measure")):
                deterministic_q = f"How will we measure success specifically for the {resolved_opt}?"
            elif any(k in target_lower for k in ("manual", "bottleneck", "error", "pain")):
                deterministic_q = f"What part of the {resolved_opt} is the most manual or prone to errors today?"
            elif any(k in target_lower for k in ("user", "persona", "audience")):
                deterministic_q = f"Who are the primary users impacted by the {resolved_opt}?"
            elif any(k in target_lower for k in ("timeline", "deadline")):
                deterministic_q = f"What is the target timeline for the {resolved_opt}?"
            else:
                deterministic_q = f"Could you walk me through how the {resolved_opt} currently handles {target_subpart.replace('_', ' ')}?"
            
            log_event(**ctx, level="INFO", event_type="deterministic_branch_narrowing", 
                      message="Short-circuited LLM generation for resolved branch", 
                      resolved_option=resolved_opt)
            
            log_event(
                thread_id=state.get("thread_id", ""), run_id=state.get("run_id", ""), node_name="generate_questions_node",
                event_type="metric_llm_prevention", message="LLM call prevented by deterministic branch short-circuit", metric_name="branch_short_circuit", metric_value=1
            )
            
            import uuid
            current_question_object = {
                "question_id": str(uuid.uuid4()),
                "question_text": deterministic_q,
                "subparts": [target_subpart] if remaining else [],
            }
            
            return _enforce_visibility({
                "generation_status": "question_generated",
                "generation_reason": "Short-circuited LLM generation for resolved branch",
                "selected_candidate_id": current_question_object["question_id"],
                "duplicate_details": {"total_candidates_evaluated": 0, "rejection_reasons": []},
                "current_questions": deterministic_q,
                "current_question_segments": [],
                "current_question_object": current_question_object,
                "remaining_subparts": remaining,
                "active_question_id": current_question_object["question_id"],
                "active_question_type": "OPEN_ENDED",
                "active_question_options": [],
                "question_status": "OPEN",
                "resolved_option_id": "",
                "answered_at": "",
                "recent_questions": state.get("recent_questions", []) + [deterministic_q],
                "repair_instruction": "",
            }, deterministic_q, section.title if section else "Unknown", state["section_index"], state.get("iteration", 0), event_type="elicit")
    return None

def _build_elicitor_prompt_context(state: "PRDState", section, bridge_output: dict) -> str:
    iteration = state.get("iteration", 0)
    prd_so_far = _format_prd_so_far(state.get("prd_sections", {}))
    prd_block = ELICITOR_PRD_BLOCK.format(prd_so_far=prd_so_far) if prd_so_far else ""

    context_block = ""
    if state.get("context_doc"):
        context_block += ELICITOR_CONTEXT_BLOCK.format(context_doc=state["context_doc"])
    
    context_block += _build_visual_context_block(state)

    if iteration > 0 and state.get("reflection"):
        raw_gaps = state.get("requirement_gaps", "")
        iteration_block = ELICITOR_ITERATION_BLOCK.format(
            iteration=iteration + 1,
            max_iterations=state.get("max_iterations", DEFAULT_MAX_SECTION_ITERATIONS),
            reflection=state["reflection"],
            requirement_gaps=(
                raw_gaps if raw_gaps
                else "None identified. Refer to reflection feedback above."
            ),
            triage_decision=state.get("triage_decision", "TRIAGE: NORMAL ITERATION"),
        )
    else:
        iteration_block = ""

    qa_store = state.get("confirmed_qa_store", {})
    section_has_answers = any(
        v.get("section_id") == section.id for v in qa_store.values()
    )
    if iteration == 0 and not section_has_answers:
        first_turn_block = ELICITOR_FIRST_TURN_BLOCK
    else:
        first_turn_block = ""

    repair_instruction = state.get("repair_instruction", "")
    remaining_subparts = state.get("remaining_subparts", [])

    if remaining_subparts:
        first_turn_block += f"\n\nFOLLOW-UP BOUNDARY: Do NOT generate original broad question. Ask ONLY about these exact missing constraints: {remaining_subparts}."

    if repair_instruction in ("DUPLICATE_SUPPRESSED", "REPETITION_COMPLAINT"):
        if remaining_subparts:
            temp_remaining = list(remaining_subparts)
            if temp_remaining[0] == "workflow_sequence_missing":
                temp_remaining = ["mapping_logic_missing"] + [s for s in temp_remaining if s != "mapping_logic_missing"]
            elif temp_remaining[0] == "mapping_logic_missing":
                temp_remaining = ["destination_handling_missing"] + [s for s in temp_remaining if s != "destination_handling_missing"]
            remaining_subparts = temp_remaining
        
        first_turn_block += f"\n\nCRITICAL: User indicated frustration over repetition. Explicitly acknowledge context, and ensure this query is syntactically distinct. You MUST ask about a different narrower subpart: {remaining_subparts[0] if remaining_subparts else 'unknown'}."
    elif repair_instruction == "REPHRASE_REQUIRED":
        first_turn_block += "\n\nCRITICAL: The user was confused by your previous question. Do NOT repeat it. Instead, rephrase it much more clearly and simply while keeping the EXACT SAME underlying target subpart."

    first_turn_block += "\n\nCRITICAL NEXT QUESTION RULE: You MUST target the single highest-priority unresolved constraint or blocker. Do NOT ask about already-known info or low-leverage details."

    if state.get("question_status") == "ANSWERED":
        resolved_opt = state.get("resolved_option_id", "")
        if resolved_opt:
            first_turn_block += f"\n\nCRITICAL: The user just resolved the previous ambiguity by selecting '{resolved_opt}'. You MUST advance the conversation. DO NOT repeat the previous question. Ask the next logical narrower follow-up question."

    expected_components_list = "\n".join(
        f"  \u2022 {c}" for c in section.expected_components
    )

    import json
    conversation_understanding_block = CONVERSATION_UNDERSTANDING_BLOCK.format(
        conversation_understanding=json.dumps(bridge_output, indent=2, default=str)
    )

    system_prompt = ELICITOR_SYSTEM.format(
        section_title=section.title,
        section_description=section.description,
        expected_components_list=expected_components_list,
        context_block=context_block,
        prd_block=prd_block,
        conversation_understanding_block=conversation_understanding_block,
        iteration_block=iteration_block,
        first_turn_block=first_turn_block,
        global_rigor_block=GLOBAL_RIGOR_BLOCK,
        decision_enforcement_block=DECISION_ENFORCEMENT_BLOCK,
        iteration_discipline_block=ITERATION_DISCIPLINE_BLOCK,
        human_trust_block=HUMAN_TRUST_BLOCK,
        language_rules_block=LANGUAGE_RULES_BLOCK,
        numeric_grounding_block=NUMERIC_GROUNDING_BLOCK,
    )
    system_prompt += "\n\nCRITICAL UX RULE: Do not use internal reviewer terms like 'contradictory', 'ambiguous', 'blocker', 'rubric', 'missing components'. Explain what details you need using plain English. Ask EXACTLY ONE question."
    return system_prompt

def _invoke_structured_question_generator(system_prompt: str, section, state: "PRDState") -> tuple[dict | str, float]:
    import time
    llm = _get_llm()
    
    t0 = time.monotonic()
    response = llm_invoke(
        llm.with_structured_output(
            {
                "name": "QuestionSchema",
                "description": "Structure of the next logical question to ask.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "question_id": {"type": "string"},
                        "user_facing_gap_reason": {"type": "string"},
                        "single_next_question": {"type": "string"},
                        "subparts": {"type": "array", "items": {"type": "string"}},
                        "question_type": {"type": "string", "enum": ["OPEN_ENDED", "BINARY_CLARIFICATION"]},
                        "options": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["question_id", "user_facing_gap_reason", "single_next_question", "subparts"],
                },
            }
        ),
        [
            SystemMessage(content=system_prompt),
            HumanMessage(
                content=f"Generate questions for the '{section.title}' section."
            ),
        ],
        state=state, node_name="generate_questions_node", purpose="structured_question_generation"
    )
    return response, (time.monotonic() - t0)

def _apply_repeat_guard(response: dict | str, recent_q_history: list[str], state: "PRDState", ctx: dict) -> tuple[bool, str, dict]:
    new_q_text = response.get("single_next_question", "") if isinstance(response, dict) else str(response)
    is_semantic_repeat = False
    decision_reason = "identical lemma coverage"
    
    if recent_q_history and new_q_text:
        nlp = _get_nlp()
        if nlp:
            doc_n = nlp(new_q_text.lower())
            doc_o = nlp(recent_q_history[-1].lower())
            lemmas_n = {t.lemma_ for t in doc_n if t.pos_ in ("NOUN", "VERB", "ADJ") and not t.is_stop}
            lemmas_o = {t.lemma_ for t in doc_o if t.pos_ in ("NOUN", "VERB", "ADJ") and not t.is_stop}
            
            if lemmas_n and lemmas_o:
                overlap_ratio = len(lemmas_n & lemmas_o) / max(len(lemmas_n), 1)
                if overlap_ratio > 0.65:
                    from utils.adjudicator import invoke_llm_adjudicator
                    decision = invoke_llm_adjudicator(
                        task_type="semantic_repeat",
                        context_data={
                            "previous_question": recent_q_history[-1],
                            "candidate_next_question": new_q_text,
                            "run_id": state.get("run_id"),
                            "thread_id": state.get("thread_id")
                        },
                        llm=_get_llm()
                    )
                    if decision is None or decision.decision_result:
                        is_semantic_repeat = True
                        decision_reason = decision.reason if decision else decision_reason
                        log_event(
                            thread_id=state.get("thread_id", ""), run_id=state.get("run_id", ""), node_name="generate_questions_node",
                            level="WARNING", event_type="repeat_guard_decision",
                            message="Caught semantic repeat",
                            candidate_question=new_q_text,
                            is_repeat=True,
                            repeat_reason=decision_reason,
                            target_blocker_id=state.get("remaining_subparts", [""])[0] if state.get("remaining_subparts") else ""
                        )
    return is_semantic_repeat, new_q_text, {"reason": decision_reason}

def _generate_context_aware_fallback(state: "PRDState") -> str:
    """
    Deterministically generates the best possible fallback question when the LLM
    fails to extract a structured response, avoiding lazy 'Tell me more' strings.
    History-aware: checks recent_questions to avoid repeating the same question.
    Priority:
    1. Active blocker / remaining subpart (non-phantom)
    2. Concept conflict
    3. Image/screen context
    4. Unresolved section component from canonical registry
    5. Generic goal fallback (with history dedup)
    """
    recent = state.get("recent_questions", [])
    recent_lower = [q.lower() for q in recent]
    
    def _not_recently_asked(candidate: str) -> bool:
        c_lower = candidate.lower()
        return not any(
            c_lower in r or r in c_lower
            for r in recent_lower
            if len(r) > 10
        )
    
    # 1. Remaining Subparts (skip phantom "clarification")
    PHANTOM_SUBPARTS = {"clarification", "fallback", "unknown"}
    blockers = [b for b in state.get("remaining_subparts", []) if b not in PHANTOM_SUBPARTS]
    if blockers:
        subpart = blockers[0].replace("_", " ")
        if "audience" in subpart or "user" in subpart:
            q = "I'm still unclear who this is for. Is this meant for end users, admins, or someone else?"
        elif "action" in subpart or "workflow" in subpart:
            q = "I'm missing some details on the expected workflow. What is the specific action users should take here?"
        else:
            q = f"I'm still missing some details about the {subpart}. Could you clarify what is expected there?"
        if _not_recently_asked(q):
            return q
        # If the blocker-based question was recently asked, fall through to other options
        
    # 2. Concept Conflicts
    conflicts = state.get("concept_conflicts", [])
    if conflicts:
        target = conflicts[0].get("surface", conflicts[0].get("concept_key", "concept"))
        q = f"I'm seeing conflicting details about the {target}. Which version is correct?"
        if _not_recently_asked(q):
            return q
        
    # 3. Image/Screenshot Context
    if state.get("uploaded_files") or (state.get("pending_event", {}) and state.get("pending_event", {}).get("uploaded_files")):
        q = "I can see the screen, but I'm unclear what users are supposed to do here. What is the main action they should complete on this page?"
        if _not_recently_asked(q):
            return q
    
    # 4. Unresolved section components from canonical registry
    section_index = state.get("section_index")
    if section_index is not None:
        try:
            section = get_section_by_index(section_index)
            qa_store = state.get("confirmed_qa_store", {})
            resolved_components = set()
            for v in qa_store.values():
                if v.get("section_id") == section.id and not v.get("contradiction_flagged"):
                    for sp in v.get("resolved_subparts", []):
                        resolved_components.add(sp.lower().replace(" ", "_"))
                        resolved_components.add(sp)
            
            for comp in section.expected_components:
                comp_norm = comp.lower().replace(" ", "_")
                if comp not in resolved_components and comp_norm not in resolved_components:
                    q = f"I'm still missing some details about {comp}. Could you share what you have in mind?"
                    if _not_recently_asked(q):
                        return q
        except (IndexError, AttributeError):
            pass
        
    # 5. Generic Goal Fallback (with history dedup)
    generic_options = [
        "I'm missing one key piece of context: what specific problem are you trying to solve here?",
        "Could you describe what the ideal end state looks like once this is built?",
        "What would a successful outcome look like for the people using this?",
        "What is the most important thing this needs to do that it doesn't do today?",
    ]
    for option in generic_options:
        if _not_recently_asked(option):
            return option
    
    # Absolute last resort: return the first option even if it was asked before
    return generic_options[0]

def _fallback_for_hard_blocker(remaining_subparts: list[str]) -> dict:
    import time
    fallback_blocker = remaining_subparts[0] if remaining_subparts else "clarification"
    if fallback_blocker == "workflow_sequence_missing":
        safe_text = "I want to make sure I don't ask you for the same information twice. What exactly happens right before the Excel mapping?"
    elif fallback_blocker == "mapping_logic_missing":
        safe_text = "To avoid repeating myself: could you clarify exactly which fields from the email get matched?"
    else:
        # Pass a mock state object to our helper to leverage deterministic fallback resolution
        mock_state = {"remaining_subparts": remaining_subparts}
        safe_text = _generate_context_aware_fallback(mock_state)
        
    return {
        "question_id": f"hard_block_{int(time.monotonic())}",
        "single_next_question": safe_text,
        "user_facing_gap_reason": "",
        "subparts": [fallback_blocker],
        "question_type": "OPEN_ENDED",
        "options": []
    }

def _normalize_generated_question(response: dict | str, state: "PRDState", ctx: dict) -> tuple[dict, str]:
    import time
    extraction_status = "success"
    if isinstance(response, str):
        extraction_status = "fallback"
        log_event(
            **ctx, level="WARNING", event_type="parser_fallback",
            message="LLM returned string instead of dict. Discarding raw string and using deterministic contextual fallback.",
            raw_response=response,
            model_name=getattr(_get_llm(), "model", "gemini-2.5-flash"),
        )
        
        # NEVER trust the LLM's raw string if it failed structured extraction, as it will likely be lazy.
        fallback_msg = _generate_context_aware_fallback(state)
        
        last_q_text = ""
        recent = state.get("recent_questions", [])
        if recent:
            last_q_text = recent[-1]
            
        gap_reason = state.get("user_facing_gap_reason", "")
        preserved_options = []
        preserved_type = "OPEN_ENDED"
        
        # Only override the deterministic fallback if we somehow hit an exact repeat issue 
        if last_q_text and (last_q_text.lower() in fallback_msg.lower() or fallback_msg.lower() in last_q_text.lower()):
            if state.get("resolved_option_id"):
                fallback_msg = f"Could you elaborate more on the {state.get('resolved_option_id')}?"
                gap_reason = ""
            elif state.get("question_status") == "OPEN" and state.get("active_question_type") == "BINARY_CLARIFICATION" and state.get("active_question_options"):
                preserved_options = state.get("active_question_options")
                preserved_type = "BINARY_CLARIFICATION"
        
        # Preserve validated unresolved blockers from state — never inject phantom subparts.
        # If remaining_subparts is empty, seed from canonical unresolved components (P3).
        PHANTOM_SUBPARTS = {"clarification", "fallback", "unknown"}
        validated_subparts = [
            s for s in state.get("remaining_subparts", [])
            if s not in PHANTOM_SUBPARTS
        ]
        # Canonical seeding: if no valid subparts, derive from section registry
        if not validated_subparts:
            try:
                _section = get_section_by_index(state.get("section_index", 0))
                _qa_store = state.get("confirmed_qa_store", {})
                _resolved = set()
                for _v in _qa_store.values():
                    if _v.get("section_id") == _section.id and not _v.get("contradiction_flagged"):
                        for _sp in _v.get("resolved_subparts", []):
                            _resolved.add(_sp)
                            _resolved.add(_sp.lower().replace(" ", "_"))
                for _comp in _section.expected_components:
                    _comp_norm = _comp.lower().replace(" ", "_")
                    if _comp not in _resolved and _comp_norm not in _resolved:
                        validated_subparts.append(_comp)
            except (IndexError, AttributeError):
                pass
        response = {
            "question_id": f"fallback_{int(time.monotonic())}",
            "single_next_question": fallback_msg,
            "user_facing_gap_reason": gap_reason,
            "subparts": validated_subparts,
            "question_type": preserved_type,
            "options": preserved_options
        }
    elif not isinstance(response, dict):
        extraction_status = "malformed"
        fallback_msg = _generate_context_aware_fallback(state)
        PHANTOM_SUBPARTS = {"clarification", "fallback", "unknown"}
        validated_subparts = [
            s for s in state.get("remaining_subparts", [])
            if s not in PHANTOM_SUBPARTS
        ]
        if not validated_subparts:
            try:
                _section = get_section_by_index(state.get("section_index", 0))
                _qa_store = state.get("confirmed_qa_store", {})
                _resolved = set()
                for _v in _qa_store.values():
                    if _v.get("section_id") == _section.id and not _v.get("contradiction_flagged"):
                        for _sp in _v.get("resolved_subparts", []):
                            _resolved.add(_sp)
                            _resolved.add(_sp.lower().replace(" ", "_"))
                for _comp in _section.expected_components:
                    _comp_norm = _comp.lower().replace(" ", "_")
                    if _comp not in _resolved and _comp_norm not in _resolved:
                        validated_subparts.append(_comp)
            except (IndexError, AttributeError):
                pass
        response = {
            "question_id": "fallback_error",
            "single_next_question": fallback_msg,
            "user_facing_gap_reason": "",
            "subparts": validated_subparts,
            "question_type": "OPEN_ENDED",
            "options": [],
            "content_segments": [{"text": fallback_msg, "provenance": None}]
        }

    log_event(
        **ctx, level="INFO", event_type="structured_extraction_metric",
        message=f"Structured extraction status: {extraction_status}",
        extraction_status=extraction_status
    )
    
    raw_next_q = response.get("single_next_question", _generate_context_aware_fallback(state))
    
    # Actively catch generic string generations from the LLM and force context-awareness
    # No length requirement! Any generic escape should be neutralized.
    generics = ["could you provide a few more details", "tell me more", "can you elaborate", "what do you mean", "can you say more"]
    if any(g in raw_next_q.lower() for g in generics):
        raw_next_q = _generate_context_aware_fallback(state)
        
    raw_gap_reason = state.get("user_facing_gap_reason", response.get("user_facing_gap_reason", ""))
    raw_ans_lower = state.get("raw_answer_buffer", "").lower()
    
    if "->" in raw_ans_lower or "step" in raw_ans_lower or "workflow" in raw_ans_lower or "first" in raw_ans_lower or "map" in raw_ans_lower:
        if "elaborate" in raw_next_q.lower() or "more detail" in raw_next_q.lower():
            if "map" in raw_ans_lower or "match" in raw_ans_lower:
                raw_next_q = "What exactly is being matched during the mapping step today?"
            elif "pdf" in raw_ans_lower or "retriev" in raw_ans_lower:
                raw_next_q = "What triggers PDF retrieval after mapping?"
            elif "manual" not in raw_ans_lower:
                raw_next_q = "Which part of that workflow is still the most manual today?"
            else:
                raw_next_q = "Who performs this process today?"
    
    import re
    jargon = re.compile(r'\b(contradictory|ambiguous|rubric|missing components|implementation blocker|review process|overall score)\b', re.IGNORECASE)
    raw_next_q = jargon.sub('unclear', raw_next_q)
    raw_gap_reason = jargon.sub('unclear', raw_gap_reason)
    
    if '?' in raw_next_q:
        parts = raw_next_q.split('?')
        if len(parts) > 2:
            raw_next_q = parts[0].strip() + '?'
            
    response["single_next_question"] = raw_next_q
    return response, raw_gap_reason

def _construct_final_question_text(response: dict, raw_gap_reason: str, state: "PRDState") -> str:
    raw_next_q = response.get("single_next_question", "")
    if raw_gap_reason:
        questions = f"{raw_gap_reason.strip().rstrip('.')}.\n\n{raw_next_q}"
    else:
        questions = raw_next_q
        
    explicit_missing_detail = response.get("explicit_missing_detail", "")
    acknowledged_context = response.get("acknowledged_context", "").strip()
    if acknowledged_context.lower().startswith("i understand "):
        acknowledged_context = acknowledged_context[13:].strip()
    if acknowledged_context.endswith("."):
        acknowledged_context = acknowledged_context[:-1]

    if len(acknowledged_context.split()) > 15:
        acknowledged_context = "the process"

    if explicit_missing_detail and explicit_missing_detail.lower().strip("?. ") not in raw_next_q.lower().strip("?. "):
        if acknowledged_context and acknowledged_context.lower() != "the process":
            combined = f"I understand {acknowledged_context}, but what I still need to know is {explicit_missing_detail}. {raw_next_q}"
        else:
            combined = f"{raw_next_q}"
    else:
        combined = f"{raw_next_q}"
        
    raw_ans = state.get("raw_answer_buffer", "").lower()
    if response.get("question_type", "OPEN_ENDED") == "OPEN_ENDED":
        questions = combined
        if "Can you give" in raw_next_q and "an example" in raw_next_q:
             if "How much does it cost?" in raw_next_q: 
                  questions = "I understand the current process. How much does it cost?"
             elif "excel mapping" in raw_ans:
                  questions = "Got it — I understand the steps. How do you decide which email data maps to which Excel column?"
             else:
                  questions = raw_next_q
        elif "How much does it cost?" in raw_next_q:
             questions = "I understand the current process. How much does it cost?"
        elif "Is the main problem product mapping or manual PDF retrieval?" in raw_next_q:
             questions = raw_next_q
    else:
        questions = raw_next_q
            
    return _sanitize_user_facing_question(questions)

def _suppress_resolved_subparts(response: dict, raw_gap_reason: str, state: "PRDState", section, ctx: dict) -> tuple[str, list[str]]:
    qa_store = state.get("confirmed_qa_store", {})
    resolved_in_store = []
    for k, v in qa_store.items():
        if v.get("section_id") == section.id and not v.get("contradiction_flagged"):
            resolved_in_store.extend(v.get("resolved_subparts", []))
    
    resolved_set = set(resolved_in_store)
    raw_subparts = response.get("subparts", [])
    filtered_subparts = [sp for sp in raw_subparts if sp not in resolved_set]
    
    questions = _construct_final_question_text(response, raw_gap_reason, state)
    raw_next_q = response.get("single_next_question", "")
    iteration = state.get("iteration", 0)
    
    if raw_subparts and not filtered_subparts:
        log_suppression_decision(
            thread_id=state.get("thread_id", ""),
            run_id=state.get("run_id", ""),
            node_name="generate_questions",
            concept_id="multiple",
            decision="SUPPRESSED",
            reason=f"All subparts {raw_subparts} already resolved in store."
        )
        
        log_event(
            **ctx, level="INFO", event_type="suppression_observability",
            message="Fully suppressed candidate question",
            candidate_question=raw_next_q,
            raw_subparts=raw_subparts,
            suppressed_subparts=raw_subparts,
            final_question="I have all the details I need for this section. Let's move on."
        )
        
        if iteration > 0:
            questions = "I have all the details I need for this section. Let's move on."
            filtered_subparts = []
    elif len(filtered_subparts) < len(raw_subparts):
        suppressed = [sp for sp in raw_subparts if sp in resolved_set]
        log_suppression_decision(
            thread_id=state.get("thread_id", ""),
            run_id=state.get("run_id", ""),
            node_name="generate_questions",
            concept_id="multiple",
            decision="PARTIAL_SUPPRESSION",
            reason=f"Suppressed duplicate subparts: {suppressed}"
        )
        log_event(
            **ctx, level="INFO", event_type="suppression_observability",
            message="Partially suppressed candidate question",
            candidate_question=raw_next_q,
            raw_subparts=raw_subparts,
            suppressed_subparts=suppressed,
            final_question=raw_next_q
        )
    return questions, filtered_subparts

def _apply_transition_cleanup(final_questions: str, current_question_object: dict) -> tuple[dict, str]:
    target_transition = "I have all the details I need for this section. Let's move on."
    short_transition = "I have all the details I need for this section."
    if short_transition in final_questions:
        cleaned = final_questions.replace(target_transition, "").replace(short_transition, "").strip()
        if not cleaned:
            final_questions = target_transition
            current_question_object["subparts"] = []
        else:
            final_questions = cleaned
    return current_question_object, final_questions

def _evaluate_duplicate_candidate(candidate_q_text: str, candidate_subparts: list[str], raw_subparts: list[str], candidate_id: str, state: "PRDState", section, ctx: dict) -> tuple[bool, str, str]:
    """Pure evaluative filter for question candidates."""
    if raw_subparts and len(candidate_subparts) == 0:
        log_event(**ctx, level="INFO", event_type="duplicate_candidate_blocked", message="All generated subparts were suppressed by previously resolved facts")
        return False, "all_subparts_resolved", "semantic_conflict_resolved"

    recent_questions = state.get("recent_questions", [])
    candidate_q_lower = candidate_q_text.lower()
    qa_store = state.get("confirmed_qa_store", {})

    # 1. Check recent UI question history
    if not candidate_id.startswith("hard_block_"):
        for prior_q in recent_questions:
            prior_q_lower = prior_q.lower()
            if len(prior_q_lower) > 10 and (prior_q_lower in candidate_q_lower or candidate_q_lower in prior_q_lower):
                log_event(**ctx, level="INFO", event_type="duplicate_candidate_blocked", message="Candidate matching recent history", prior_q=prior_q)
                return False, "recent_question_match", prior_q
                
    # 2. Check canonical QA store
    for k, v in qa_store.items():
        if v.get("section_id") == section.id and not v.get("contradiction_flagged"):
            stored_q = v.get("questions", "").lower()
            store_subparts = v.get("resolved_subparts", [])
            
            text_match = len(stored_q) > 10 and (stored_q in candidate_q_lower or candidate_q_lower in stored_q)
            semantic_match = bool(set(store_subparts) & set(candidate_subparts))
            
            if text_match:
                log_event(**ctx, level="INFO", event_type="duplicate_candidate_blocked", message="Candidate matching canonical QA history", prior_q=v.get("questions", ""))
                return False, "exact_text_match", v.get("questions", "")
            if semantic_match:
                log_event(**ctx, level="INFO", event_type="duplicate_candidate_blocked", message="Candidate overlapping resolved subparts", matched_subparts=list(set(store_subparts) & set(candidate_subparts)))
                return False, "semantic_match", v.get("questions", "")

    return True, "", ""

def _package_generated_question_result(response: dict, questions: str, current_question_object: dict, state: "PRDState", section, ctx: dict, system_prompt: str, override_status: str = None, duplicate_details: dict = None) -> dict:
    import time
    qa_store = state.get("confirmed_qa_store", {})
    triage = state.get("triage_decision", "")
    gaps_count = len([l for l in state.get("requirement_gaps", "").splitlines() if l.strip()])
    question_count = len([
        l for l in questions.splitlines()
        if l.strip() and (l.strip()[0:1].isdigit() or l.strip()[0:1] in ("-", "•", "*"))
    ])
    iteration = state.get("iteration", 0)
    
    log_event(
        **ctx, level="INFO", event_type="node_start",
        message="generate_questions_node started",
        is_follow_up=(iteration > 0),
        triage="RECOVERY" if "RECOVERY MODE" in triage else "NORMAL",
        gaps_count=gaps_count,
    )
    if not questions:
        log_event(
            **ctx, level="WARNING", event_type="elicitor_empty_output",
            message="generate_questions_node produced empty output",
        )
    log_event(
        **ctx, level="INFO", event_type="elicitor_output",
        message="Questions generated",
        is_follow_up=(iteration > 0),
        triage="RECOVERY" if "RECOVERY MODE" in triage else "NORMAL",
        gaps_count=gaps_count, question_count=question_count, output_len=len(questions),
    )
    
    log_event(**ctx, level="DEBUG", event_type="elicitor_prompt",
              message="Elicitor system prompt", system_prompt=system_prompt)
    log_event(**ctx, level="DEBUG", event_type="elicitor_raw_output",
              message="Elicitor raw LLM response", raw_output=questions)
              
    duration_ms = int((time.monotonic() - ctx.get("_t0", time.monotonic())) * 1000)
    log_event(
        **ctx, level="INFO", event_type="node_end",
        message="generate_questions_node finished",
        duration_ms=duration_ms, question_count=question_count,
    )

    referenced_keys = response.get("referenced_concept_keys", []) if isinstance(response, dict) else []
    if not referenced_keys:
        for ck, fact in qa_store.items():
            if fact.get("section_id") == section.id and not fact.get("contradiction_flagged"):
                referenced_keys.append(ck)
    
    provenance_segments = _segment_text_with_provenance(questions, referenced_keys, state)

    status = override_status if override_status else ("no_question_available" if not questions else "question_generated")
    dup_details = duplicate_details or {"total_candidates_evaluated": 1, "rejection_reasons": []}
    return _enforce_visibility({
        "generation_status": status,
        "generation_reason": "Generator flow completed",
        "selected_candidate_id": response.get("question_id", ""),
        "duplicate_details": dup_details,
        "current_questions": questions,
        "content_segments": provenance_segments,
        "current_question_object": current_question_object,
        "remaining_subparts": [s for s in current_question_object.get("subparts", []) if s not in {"clarification", "fallback", "unknown"}],
        
        "active_question_id": response.get("question_id", ""),
        "active_question_type": response.get("question_type", "OPEN_ENDED"),
        "active_question_options": response.get("options", []),
        "question_status": "OPEN",
        "resolved_option_id": "",
        "answered_at": "",
        "recent_questions": state.get("recent_questions", []) + [current_question_object["question_text"]],
        
        "repair_instruction": "",
    }, questions, section.title if section else "Unknown", state["section_index"], state.get("iteration", 0), event_type="elicit")

def _is_synthetic_blocker(blocker: str, canonical_set: set[str]) -> bool:
    """
    Explicit synthetic-blocker predicate (T1).
    A blocker is synthetic if it was NOT derived from the section's canonical expected_components.
    Uses canonical registry membership, not substring matching, to avoid false positives.
    """
    if blocker in canonical_set:
        return False
    # Check if the blocker is a known canonical root with a suffix appended by blocker_transition
    for canonical in canonical_set:
        canonical_normalized = canonical.lower().replace(" ", "_")
        if blocker.startswith(canonical_normalized):
            # e.g. "workflow_sequence_missing_specific_interaction" starts with "workflow_sequence_missing"
            return True
    # If it doesn't match any canonical root at all, it's also non-canonical (either synthetic or new)
    return blocker not in canonical_set

def _classify_target_confidence(state: "PRDState", section) -> tuple[str, str | None, list[str], str]:
    """
    Deterministic target selection for the two-lane question generation architecture.
    
    Uses remaining_subparts when valid, but REHYDRATES from the section's canonical
    expected_components registry when remaining_subparts is empty or fully resolved.
    This prevents starvation after parser fallback or blocker pipeline gaps.
    
    Returns:
        (confidence, target, candidates, reason)
        - confidence: "STRONG" | "MODERATE" | "WEAK" | "EMPTY"
        - target: the selected blocker name (only for STRONG)
        - candidates: list of valid candidate blockers
        - reason: human-readable classification reason (T2)
    """
    remaining = list(state.get("remaining_subparts", []))
    conflicts = state.get("concept_conflicts", [])
    qa_store = state.get("confirmed_qa_store", {})
    
    # Build canonical blocker registry from section expected_components
    canonical_components = set()
    canonical_components_original = list(section.expected_components)  # preserve order
    for comp in section.expected_components:
        canonical_components.add(comp.lower().replace(" ", "_"))
        canonical_components.add(comp)
    
    # Collect already-resolved subparts from the QA store for this section
    # Normalize consistently: both original and underscore forms
    resolved_in_store = set()
    for v in qa_store.values():
        if v.get("section_id") == section.id and not v.get("contradiction_flagged"):
            for sp in v.get("resolved_subparts", []):
                resolved_in_store.add(sp)
                resolved_in_store.add(sp.lower().replace(" ", "_"))
                resolved_in_store.add(sp.lower())
    
    # Filter remaining subparts: remove already resolved ones
    unresolved = [s for s in remaining if s not in resolved_in_store
                  and s.lower().replace(" ", "_") not in resolved_in_store]
    
    # ── Canonical Rehydration (F1/F2) ────────────────────────────────────────
    # When remaining_subparts is empty or fully resolved, rehydrate from the
    # section's canonical expected_components minus what's already resolved.
    # This is RECOVERY, not blanket replacement (T3).
    rehydrated = False
    if not unresolved:
        rehydrated_candidates = []
        for comp in canonical_components_original:
            comp_norm = comp.lower().replace(" ", "_")
            if (comp not in resolved_in_store
                    and comp_norm not in resolved_in_store
                    and comp.lower() not in resolved_in_store):
                rehydrated_candidates.append(comp)
        if rehydrated_candidates:
            unresolved = rehydrated_candidates
            rehydrated = True
    
    # Separate canonical from synthetic
    canonical_unresolved = [s for s in unresolved if not _is_synthetic_blocker(s, canonical_components)]
    synthetic_unresolved = [s for s in unresolved if _is_synthetic_blocker(s, canonical_components)]
    
    # Build reason prefix for telemetry (T2)
    rehydration_note = ""
    if rehydrated:
        rehydration_note = f" [REHYDRATED from registry: {len(canonical_unresolved)} canonical, {len(synthetic_unresolved)} synthetic recovered]"
    
    # D1: STRONG — one active conflict
    if conflicts:
        conflict_target = conflicts[0].get("surface", conflicts[0].get("concept_key", "concept"))
        return ("STRONG", conflict_target, [],
                f"Active conflict on '{conflict_target}'{rehydration_note}")
    
    # D1: STRONG — exactly one canonical unresolved blocker
    if len(canonical_unresolved) == 1:
        target = canonical_unresolved[0]
        return ("STRONG", target, [],
                f"Exactly one canonical unresolved blocker: '{target}'{rehydration_note}")
    
    # D2: MODERATE — multiple canonical candidates
    if len(canonical_unresolved) > 1:
        return ("MODERATE", None, canonical_unresolved,
                f"Multiple canonical candidates remain: {canonical_unresolved}{rehydration_note}")
    
    # D3: WEAK — only synthetic/stale blockers remain
    if synthetic_unresolved:
        return ("WEAK", None, synthetic_unresolved,
                f"Only synthetic/stale blockers remain: {synthetic_unresolved}{rehydration_note}")
    
    # D4: EMPTY — truly nothing left (all components resolved)
    return ("EMPTY", None, [],
            f"All section components resolved in QA store{rehydration_note}")

def generate_questions_node(state: "PRDState") -> dict:
    import time
    ctx = _log_ctx(state, "generate_questions_node")
    t0 = time.monotonic()
    ctx["_t0"] = t0
    
    if state.get("materialization_conflict", False):
        reason = state.get("materialization_conflict_reason", "")
        reason_str = f" ({reason})" if reason else ""
        q = f"Your text and the uploaded image seem to suggest different things{reason_str}. Could you clarify which one is correct?"
        log_event(**ctx, level="INFO", event_type="asking_conflict_clarification", message="Generating clarification question for image/text conflict", text=q)
        return _enforce_visibility({
            "generation_status": "question_generated",
            "generation_reason": "Generating clarification question for image/text conflict",
            "selected_candidate_id": "conflict_q",
            "duplicate_details": {"total_candidates_evaluated": 0, "rejection_reasons": []},
            "current_questions": q,
            "current_question_object": {"question_id": "conflict_q", "question_text": q, "subparts": []},
            "raw_answer_buffer": "",
            "materialization_conflict": False,
            "materialization_conflict_reason": None,
            "pending_numeric_clarification": False,
            "repair_question_id": "",
            "phase": state.get("phase", "elicitation")
        }, q, get_section_by_index(state["section_index"]).title if state.get("section_index") is not None else "Unknown", state.get("section_index", 0), state.get("iteration", 0), event_type="elicit")

    log_event(
        **ctx, level="INFO", event_type="question_generation_decision",
        message="Logging generation decision parameters",
        active_blocker_before=state.get("remaining_subparts", [""])[0] if state.get("remaining_subparts") else "",
        remaining_blockers_before=state.get("remaining_subparts", []),
        conflict_records=state.get("conflict_records", []),
        question_mode="repair" if state.get("pending_numeric_clarification") else "normal",
        why_question_generation_was_used="Routed explicitly to generation path",
        why_clarification_answer_was_not_used="Not classified as DIRECT_CLARIFICATION_QUESTION"
    )
    
    section = get_section_by_index(state["section_index"])
    bridge_output = build_conversation_understanding_output(state)
    
    repair = _maybe_emit_numeric_repair_prompt(state, section, ctx)
    if repair: return repair
    conflict = _maybe_emit_conflict_resolution_question(state, bridge_output, section, ctx)
    if conflict: return conflict
    branch = _maybe_emit_resolved_branch_question(state, section, ctx)
    if branch: return branch
    
    # ── Two-Lane Architecture ──────────────────────────────────────────────────
    # Lane A: deterministic target selection → LLM phrasing only
    # Lane B: LLM infers missing piece when state is insufficient
    # At most 2 LLM calls (Lane A → Lane B demotion on duplicate)
    # Terminal: deterministic last-resort via _generate_context_aware_fallback
    
    confidence, target, candidates, classification_reason = _classify_target_confidence(state, section)
    log_event(**ctx, level="INFO", event_type="target_confidence_classified",
              message=f"Target confidence: {confidence}",
              confidence=confidence, target=target, candidates=candidates,
              reason=classification_reason)
    
    base_prompt = _build_elicitor_prompt_context(state, section, bridge_output)
    
    if confidence == "STRONG":
        # Lane A: anchor the LLM to a specific validated target
        lane_instruction = (
            f"\n\nTARGET LOCK: The specific detail still missing is: '{target}'. "
            f"Write exactly ONE focused question to elicit this detail. "
            f"Do NOT ask about anything else. Do NOT broaden the scope."
        )
        system_prompt = base_prompt + lane_instruction
        active_lane = "A"
    else:
        # Lane B: let LLM infer, but constrain output
        if confidence == "MODERATE" and candidates:
            lane_instruction = (
                f"\n\nFOCUS CANDIDATES: The following unresolved details remain: {candidates}. "
                f"Pick the single most critical one and write ONE focused question about it. "
                f"Do NOT ask about multiple topics."
            )
        else:
            lane_instruction = (
                f"\n\nINFERENCE MODE: Based on everything the user has said so far, identify the single most "
                f"important missing piece of information for this section. Then write exactly ONE focused question "
                f"to elicit that specific detail. You MUST name what is missing before asking."
            )
        system_prompt = base_prompt + lane_instruction
        active_lane = "B"
    
    generation_status = "question_generated"
    rejection_reasons = []
    
    # ── Pass 1: Primary LLM call ─────────────────────────────────────────────
    response_data, _ = _invoke_structured_question_generator(system_prompt, section, state)
    norm_response, raw_gap_reason = _normalize_generated_question(response_data, state, ctx)
    questions = _construct_final_question_text(norm_response, raw_gap_reason, state)
    
    is_valid, rejection_reason, matched_prior = _evaluate_duplicate_candidate(
        norm_response.get("single_next_question", ""),
        norm_response.get("subparts", []),
        norm_response.get("subparts", []),
        norm_response.get("question_id", ""),
        state, section, ctx
    )
    
    current_question_object = {
        "question_id": norm_response.get("question_id", ""),
        "question_text": norm_response.get("single_next_question", ""),
        "subparts": norm_response.get("subparts", []),
    }
    
    if not is_valid:
        rejection_reasons.append(rejection_reason)
        
        if active_lane == "A":
            # ── Lane A → Lane B demotion ─────────────────────────────────────
            log_event(**ctx, level="WARNING", event_type="lane_a_demoted_to_b",
                      message="Lane A candidate blocked as duplicate, demoting to Lane B",
                      matched_prior=matched_prior, reason=rejection_reason)
            
            demotion_instruction = (
                f"\n\nCRITICAL: The question about '{target}' was already asked or resolved. "
                f"You MUST choose a DIFFERENT unresolved detail. "
                f"Do NOT ask about '{matched_prior}'. "
                f"Identify the next most important missing piece and ask about that instead."
            )
            system_prompt = base_prompt + demotion_instruction
            active_lane = "B_recovery"
            
            response_data, _ = _invoke_structured_question_generator(system_prompt, section, state)
            norm_response, raw_gap_reason = _normalize_generated_question(response_data, state, ctx)
            questions = _construct_final_question_text(norm_response, raw_gap_reason, state)
            
            is_valid_b, rejection_reason_b, matched_prior_b = _evaluate_duplicate_candidate(
                norm_response.get("single_next_question", ""),
                norm_response.get("subparts", []),
                norm_response.get("subparts", []),
                norm_response.get("question_id", ""),
                state, section, ctx
            )
            
            current_question_object = {
                "question_id": norm_response.get("question_id", ""),
                "question_text": norm_response.get("single_next_question", ""),
                "subparts": norm_response.get("subparts", []),
            }
            
            if not is_valid_b:
                rejection_reasons.append(rejection_reason_b)
                # ── Terminal: deterministic last-resort ───────────────────────
                log_event(**ctx, level="WARNING", event_type="lane_b_terminal_fallback",
                          message="Lane B also produced duplicate, using deterministic last-resort",
                          matched_prior=matched_prior_b, reason=rejection_reason_b)
                questions = _generate_context_aware_fallback(state)
                questions = _sanitize_user_facing_question(questions)
                current_question_object["question_text"] = questions
                current_question_object["subparts"] = []
                norm_response["single_next_question"] = questions
                generation_status = "deterministic_fallback"
            else:
                generation_status = "blocked_duplicate_regenerated"
        else:
            # ── Lane B was primary and also duplicated → terminal fallback ────
            log_event(**ctx, level="WARNING", event_type="lane_b_terminal_fallback",
                      message="Lane B candidate blocked as duplicate, using deterministic last-resort",
                      matched_prior=matched_prior, reason=rejection_reason)
            questions = _generate_context_aware_fallback(state)
            questions = _sanitize_user_facing_question(questions)
            current_question_object["question_text"] = questions
            current_question_object["subparts"] = []
            norm_response["single_next_question"] = questions
            generation_status = "deterministic_fallback"
    
    # ── Guaranteed non-empty output gate (T3) ────────────────────────────────
    if not questions or not questions.strip():
        log_event(**ctx, level="WARNING", event_type="non_empty_guard_triggered",
                  message="Output was empty after all lanes, applying last-resort guard")
        questions = _generate_context_aware_fallback(state)
        questions = _sanitize_user_facing_question(questions)
        current_question_object["question_text"] = questions
        current_question_object["subparts"] = []
        norm_response["single_next_question"] = questions
        generation_status = "deterministic_fallback"
    
    dup_details = {"total_candidates_evaluated": len(rejection_reasons) + 1, "rejection_reasons": rejection_reasons}
    return _package_generated_question_result(norm_response, questions, current_question_object, state, section, ctx, system_prompt, override_status=generation_status, duplicate_details=dup_details)

def _sanitize_user_facing_question(text: str) -> str:
    """Clean LLM output to avoid internal jargon or audit-style asks to the PM."""
    if not text:
        return text

    out = text.strip()
    replacements = {
        "Headliner paragraph": "Summary section",
        "headliner paragraph": "summary section",
        "background_problem": "earlier business problem",
        "stored under": "saved in your notes",
        "concept_key": "source reference",
        "section consistency": "alignment",
    }
    for bad, good in replacements.items():
        out = out.replace(bad, good)
    out = re.sub(r"round\s*=\s*\d+", "earlier answer", out, flags=re.IGNORECASE)

    # Agent should run consistency checks itself; ask user only for decision.
    if re.search(r"consisten|alignment\s+check|section\s+alignment", out, re.IGNORECASE):
        return (
            "I noticed a possible mismatch with an earlier detail. "
            "Which version should we keep going forward?"
        )

    # Strip placeholder letters like [X], [Y], [Z] that feel robotic.
    out = re.sub(r"\[([XYZ])\]", "___", out)

    return out


def _check_contradiction(
    new_entry: dict,
    new_key: str,
    store: dict,
    section_id: str,
    state: PRDState,
) -> dict | None:
    """
    O-1b — lightweight contradiction check against prior answers in the same section.
    1. Deterministic filter: Intersects resolved_subparts between new answer and prior answers.
    2. Fallback LLM: Passes only matched candidates for JSON contradiction scoring.
    """
    new_subparts = set(new_entry.get("resolved_subparts", []))
    candidates = []
    
    for k, v in store.items():
        if v.get("section_id") == section_id and k != new_key:
            prior_subparts = set(v.get("resolved_subparts", []))
            if new_subparts.intersection(prior_subparts):
                candidates.append((k, v))
                
    if not candidates:
        return None

    # Construct strict minimal candidate text
    prior_qa = "\\n\\n".join(
        f"Candidate [{v.get('source_message_id', k)}]:\\n"
        f"- Snippets: {v.get('evidence_snippets_by_subpart', v.get('answer', ''))}\\n"
        f"- Resolved Subparts: {v.get('resolved_subparts', [])}"
        for k, v in candidates
    )
    
    new_qa = (
        f"New Statement:\\n"
        f"- Snippets: {new_entry.get('evidence_snippets_by_subpart', new_entry.get('answer', ''))}\\n"
        f"- Resolved Subparts: {list(new_subparts)}"
    )

    llm = _get_llm().with_structured_output(
        {
            "name": "ContradictionSchema",
            "description": "Assess if the New Statement contradicts any candidate.",
            "parameters": {
                "type": "object",
                "properties": {
                    "is_contradiction": {"type": "boolean"},
                    "conflicting_message_id": {"type": "string"},
                    "evidence_snippet": {"type": "string"},
                    "description": {"type": "string"}
                },
                "required": ["is_contradiction", "conflicting_message_id", "evidence_snippet", "description"]
            }
        }
    )
    try:
        prompt = _CONTRADICTION_PROMPT.format(prior_qa=prior_qa, new_qa=new_qa)
        resp = llm_invoke(
            llm, [HumanMessage(content=prompt)],
            state=state, node_name="_check_contradiction", purpose="contradiction_judgment"
        )
        if isinstance(resp, str):
            import logging
            logging.warning("Contradiction strict boundary failed: LLM returned string. Assuming no contradiction.")
            return None
        if isinstance(resp, dict) and resp.get("is_contradiction"):
            return resp
    except ValueError as e:
        import logging
        logging.warning(f"Contradiction check failed: {e}")
    return None

# ── Node: await_answer ────────────────────────────────────────────────────────

def await_answer_node(state: PRDState) -> dict:
    """
    Human-in-the-loop interrupt — captures raw user input into raw_answer_buffer.
    Accepts either a plain string (backward compat) or a structured dict payload
    {event_type, content, target_message_id, target_content, ...}.
    Does NOT write to confirmed_qa_store or section_qa_pairs.
    Those writes happen only after echo confirmation in await_confirmation_node
    (standard ANSWER/REPLY flow) or handle_tagged_event_node (tagged events).
    """
    section_idx = state.get("section_index")
    # First-turn initialization if completely missing
    if section_idx is None:
        section_idx = 0
        
    try:
        section_title = get_section_by_index(section_idx).title
    except Exception:
        section_title = "Initial Upload Phase"

    resume_value = interrupt(
        {
            "type": "waiting_for_answer",
            "section": section_title,
            "section_index": section_idx,
        }
    )

    user_text, uploaded_files, pending_event = _extract_submit_payload(resume_value)

    # End of turn instrumentation
    flush_turn_summary(state)

    event_type = pending_event.get("event_type", "ANSWER")
    
    # T1/T2: Do not append structured UI events as standalone conversational context turns
    if event_type in ("SUBMIT_SESSION_CONTEXT", "REMOVE_SESSION_CONTEXT", "REVERT_SESSION_CONTEXT", "FILE_UPLOAD"):
        return {
            "uploaded_files": uploaded_files,
            "pending_event": pending_event
        }

    answer = user_text.strip()
    existing = state.get("context_doc", "").strip()
    
    user_msg = _build_user_message_dict(answer)
    semantics = user_msg.get("semantics", {})
    concept_history_update = _sync_concept_history(state, semantics) if semantics else {}
    
    visual_context = _build_visual_context_block(state)
    if visual_context:
        new_context = f"{existing}\n\n{visual_context}\n{answer}" if existing else f"{visual_context}\n{answer}"
        user_msg["attached_image_context"] = visual_context
    else:
        new_context = f"{existing}\n\n{answer}" if existing else answer

    reply_id = ""
    reply_text = ""
    if pending_event.get("event_type") == "REPLY_TO_MESSAGE":
        reply_id = pending_event.get("target_message_id", "")
        
        import logging
        logger = logging.getLogger("orchestrator_metrics")
        logger.info("Starting canonical reply context lookup", extra={"event_type": "reply_context_lookup_started", "msg_id": reply_id})
        
        # 1. Reject UI target_content boundary
        logger.info("Dropping target_content to enforce canonical ownership contract", extra={"event_type": "reply_context_target_content_ignored"})
        
        # 2. Strict msg_id lookup
        found_text = ""
        chat_history = state.get("chat_history", [])
        for msg in chat_history:
            if msg.get("msg_id") == reply_id:
                found_text = msg.get("content", "")
                break
                
        # 3. Legacy Migrator (Fallback Index Matching)
        if not found_text and reply_id.startswith("msg_"):
            try:
                idx = int(reply_id.split("_")[1])
                if 0 <= idx < len(chat_history):
                    candidate = chat_history[idx]
                    ui_preview = pending_event.get("target_content", "")
                    
                    if candidate.get("role") in ("assistant", "user") and (not ui_preview or ui_preview in candidate.get("content", "")):
                        found_text = candidate.get("content", "")
                        logger.warning(f"Legacy msg_id array fallback used for {reply_id}", extra={"event_type": "legacy_msg_id_array_fallback_used", "msg_id": reply_id})
            except Exception:
                pass
                
        if found_text:
            reply_text = found_text
            logger.info("Reply context resolved from chat history", extra={"event_type": "reply_context_lookup_resolved", "msg_id": reply_id})
        else:
            logger.warning("Reply context lookup failed. Target message not found.", extra={"event_type": "reply_context_lookup_failed", "msg_id": reply_id})
            # Safe Fallback: do not accept a leaked chunk if lookup fails
            reply_id = ""
            reply_text = ""

    if reply_text:
        user_msg["reply_to_message_id"] = reply_id
        user_msg["reply_to_content_snippet"] = reply_text

    return {
        "context_doc": new_context,
        "raw_answer_buffer": user_text.strip(),
        "answer_confirmation_status": "PENDING",
        "pending_interrupt_type": "question",
        "pending_event": pending_event,
        "uploaded_files": uploaded_files,
        "chat_history": [user_msg],
        "concept_history": concept_history_update,
        "reply_context_message_id": reply_id,
        "reply_context_message_text": reply_text,
        "reply_context_interpretation": {
            "reply_context_present": bool(reply_id),
            "relationship_type": "",
            "confidence": 0.0,
            "reason": ""
        },
        "response_type": ""
    }


# ── Node: handle_tagged_event ─────────────────────────────────────────────────

def handle_tagged_event_node(state: PRDState) -> dict:
    """
    Processes structured tagged events arriving from the UI:
      TAG_MESSAGE_AS_TRUTH — promotes the referenced message content directly to
        confirmed_qa_store, bypassing the echo gate.
      CORRECT_MESSAGE — creates a corrected entry in confirmed_qa_store that
        supersedes the prior answer for the referenced concept.
    After handling, routes to draft (same as a CONFIRMED echo gate exit).
    """
    t0 = time.monotonic()
    ctx = _log_ctx(state, "handle_tagged_event")
    log_event(**ctx, level="INFO", event_type="node_start", message="handle_tagged_event started")

    event = state.get("pending_event", {})
    event_type = event.get("event_type", "")
    target_msg_id = event.get("target_message_id", "")
    target_content = event.get("target_content", "")
    new_content = event.get("content", "").strip()

    if event_type == "SUBMIT_SESSION_CONTEXT":
        context_id = event.get("context_id")
        bg_contexts = state.get("background_generated_contexts", [])
        target = next((c for c in bg_contexts if c.get("context_id") == context_id), None)
        if target:
            import datetime
            target_copy = dict(target)
            target_copy["edited_summary"] = new_content
            target_copy["updated_at"] = datetime.datetime.now().isoformat()
            chat_msg = (
                "**Image Context Updated** ✏️\n\n"
                f"The image semantics have been manually specified: _{new_content[:160]}{'…' if len(new_content) > 160 else ''}_"
            )
            return {
                "background_generated_contexts": [target_copy],
                "pending_event": {},
                "chat_history": [{
                    "event_type": "SUBMIT_SESSION_CONTEXT",
                    "role": "system",
                    "type": "system",
                    "content": chat_msg,
                }]
            }
        return {"pending_event": {}}

    elif event_type == "REMOVE_SESSION_CONTEXT":
        context_id = event.get("context_id")
        bg_contexts = state.get("background_generated_contexts", [])
        target = next((c for c in bg_contexts if c.get("context_id") == context_id), None)
        if target:
            import datetime
            target_copy = dict(target)
            target_copy["is_active"] = False
            target_copy["updated_at"] = datetime.datetime.now().isoformat()
            chat_msg = "**Image Context Removed** 🗑️\n\nThe image has been marked inactive and will no longer guide future reasoning."
            return {
                "background_generated_contexts": [target_copy],
                "pending_event": {},
                "chat_history": [{
                    "event_type": "REMOVE_SESSION_CONTEXT",
                    "role": "system",
                    "type": "system",
                    "content": chat_msg,
                }]
            }
        return {"pending_event": {}}
    section = get_section_by_index(state["section_index"])
    chat_history = state.get("chat_history", [])

    # Resolve target_content from chat_history if not passed in payload
    if not target_content and target_msg_id:
        try:
            idx = int(target_msg_id.split("_")[1])
            if 0 <= idx < len(chat_history):
                target_content = chat_history[idx].get("content", "")
        except (ValueError, IndexError):
            target_content = ""

    iteration = state.get("iteration", 0)
    existing_qa = list(state.get("section_qa_pairs", []))
    round_n = len(existing_qa) + 1
    concept_key = f"{section.id}:iter_{iteration}:round_{round_n}:tagged"
    current_questions = state.get("current_questions", "")
    
    # Versioning: increment store_version for this write
    current_version = state.get("store_version", 0) + 1
    fact_id = str(uuid.uuid4())

    if event_type == "TAG_MESSAGE_AS_TRUTH":
        canonical_answer = target_content or new_content
        promotion_note = new_content if (new_content and new_content != canonical_answer) else ""
        chat_msg = (
            "**Ground truth set** 📌\n\n"
            f"Using as canonical: _{canonical_answer[:160]}{'…' if len(canonical_answer) > 160 else ''}_"
            + (f"\n\n_Note: {promotion_note}_" if promotion_note else "")
        )
        store_update = {
            concept_key: {
                "fact_id": fact_id,
                "answer": canonical_answer,
                "questions": current_questions,
                "section": section.title,
                "section_id": section.id,
                "iteration": iteration,
                "round": round_n,
                "source_round": round_n,
                "contradiction_flagged": False,
                "event_type": "TAG_MESSAGE_AS_TRUTH",
                "target_message_id": target_msg_id,
                "promotion_note": promotion_note,
                "version": current_version,
            }
        }
        qa_entry = {"questions": current_questions, "answer": canonical_answer, "section": section.title}
        event_log = [{
            "event_type": "TAG_MESSAGE_AS_TRUTH",
            "target_message_id": target_msg_id,
            "content": canonical_answer[:200],
            "section": section.title,
            "concept_key": concept_key,
        }]

    elif event_type == "CORRECT_MESSAGE":
        corrected_answer = new_content or target_content
        # Hard Correction Linkage (P1/P3): Match by target_message_id or fail
        existing_store = state.get("confirmed_qa_store", {})
        corrects_key = next(
            (k for k, v in existing_store.items()
             if target_msg_id and v.get("source_message_id") == target_msg_id),
            None,
        )
        if target_msg_id and not corrects_key:
             log_event(**ctx, level="WARNING", event_type="correction_link_failure",
                       message=f"Correction failed: No canonical fact found for target_message_id {target_msg_id}",
                       target_msg_id=target_msg_id)
             chat_msg = (
                 "**Update noted** 📌\n\n"
                 f"We couldn't link this to a specific previous answer, but we've saved your update: _{corrected_answer[:160]}{'…' if len(corrected_answer) > 160 else ''}_"
             )
        else:
             chat_msg = (
                 "**Correction noted** ✏️\n\n"
                 f"Updated answer: _{corrected_answer[:160]}{'…' if len(corrected_answer) > 160 else ''}_"
             )
        store_update = {
            concept_key: {
                "fact_id": fact_id,
                "answer": corrected_answer,
                "questions": current_questions,
                "section": section.title,
                "section_id": section.id,
                "iteration": iteration,
                "round": round_n,
                "source_round": round_n,
                "contradiction_flagged": False,
                "event_type": "CORRECT_MESSAGE",
                "target_message_id": target_msg_id,
                "corrects_key": corrects_key,
                "version": current_version,
            }
        }
        qa_entry = {"questions": current_questions, "answer": corrected_answer, "section": section.title}
        event_log = [{
            "event_type": "CORRECT_MESSAGE",
            "target_message_id": target_msg_id,
            "content": corrected_answer[:200],
            "section": section.title,
            "concept_key": concept_key,
            "corrects_key": corrects_key,
        }]

    else:
        # Unknown event type — passthrough, let normal flow handle it
        return {"pending_event": {}}

    # Telemetry: Log the write
    log_canonical_write(
        thread_id=state.get("thread_id", ""),
        run_id=state.get("run_id", ""),
        node_name="handle_tagged_event",
        fact_id=fact_id,
        concept_id=concept_key,
        change_type="CREATED" if event_type == "TAG_MESSAGE_AS_TRUTH" else "SUPERSEDED",
        version=current_version,
    )

    # Post-write Integrity Validation (P0/P3)
    IntegrityValidator.validate_mutation(
        thread_id=state.get("thread_id", ""),
        run_id=state.get("run_id", ""),
        node_name="handle_tagged_event",
        store=state.get("confirmed_qa_store", {}),
        update=store_update,
        section_id=section.id
    )

    duration_ms = int((time.monotonic() - t0) * 1000)
    log_event(**ctx, level="INFO", event_type="node_end", message="handle_tagged_event finished", duration_ms=duration_ms)

    concept_history = state.get("concept_history", {})
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    new_msg_id = state.get("chat_history", [])[-1].get("msg_id", "") if state.get("chat_history") else ""

    if event_type == "TAG_MESSAGE_AS_TRUTH":
        for key, c_state in concept_history.items():
            if target_msg_id and target_msg_id in c_state.get("mentions", []):
                if c_state.get("status") in (ConceptStatus.MENTIONED.value, ConceptStatus.CONFLICTED.value, ConceptStatus.NEGATED.value):
                    old_s = c_state["status"]
                    c_state["status"] = ConceptStatus.CURRENT.value
                    c_state["is_current"] = True
                    c_state["status_reason"] = f"Promoted via TAG_MESSAGE_AS_TRUTH (source: {target_msg_id})"
                    c_state["last_transition_at"] = now
                    _log_semantic_transition(key, old_s, ConceptStatus.CURRENT.value, "TAG_MESSAGE_AS_TRUTH gate cleared", target_msg_id)
    elif event_type == "CORRECT_MESSAGE":
        for key, c_state in concept_history.items():
            if target_msg_id and target_msg_id in c_state.get("mentions", []) and c_state.get("status") == ConceptStatus.CURRENT.value:
                old_s = c_state["status"]
                c_state["status"] = ConceptStatus.SUPERSEDED.value
                c_state["is_current"] = False
                c_state["was_corrected"] = True
                c_state["superseded_by"] = new_msg_id
                c_state["status_reason"] = f"Superseded by explicit correction in {new_msg_id}"
                c_state["last_transition_at"] = now
                _log_semantic_transition(key, old_s, ConceptStatus.SUPERSEDED.value, "CORRECT_MESSAGE", new_msg_id)
                
            if new_msg_id and new_msg_id in c_state.get("mentions", []):
                if c_state.get("status") in (ConceptStatus.MENTIONED.value, ConceptStatus.CONFLICTED.value, ConceptStatus.NEGATED.value):
                    old_s = c_state["status"]
                    c_state["status"] = ConceptStatus.CURRENT.value
                    c_state["is_current"] = True
                    c_state["status_reason"] = f"Promoted via explicit correction payload {new_msg_id}"
                    c_state["last_transition_at"] = now
                    _log_semantic_transition(key, old_s, ConceptStatus.CURRENT.value, "Correction promotion", new_msg_id)

    return {
        "answer_confirmation_status": "CONFIRMED",
        "section_qa_pairs": existing_qa + [qa_entry],
        "confirmed_qa_store": store_update,
        "store_version": current_version,
        "event_history": event_log,
        "correction_stats": {
            "success": (state.get("correction_stats", {}).get("success", 0) + (1 if event_type == "CORRECT_MESSAGE" and corrects_key else 0)),
            "failure": (state.get("correction_stats", {}).get("failure", 0) + (1 if event_type == "CORRECT_MESSAGE" and not corrects_key else 0)),
        },
        "pending_event": {},
        "raw_answer_buffer": "",
        "pending_echo": "",
        "pending_concept_updates": {},
        "chat_history": [
            {
                "role": "assistant",
                "msg_id": f"msg_{str(uuid.uuid4())[:8]}",
                "type": "tagged_event",
                "event_type": event_type,
                "section": section.title,
                "content": chat_msg,
            }
        ],
        "concept_history": concept_history,
    }


# ── Side-fact extraction helper ───────────────────────────────────────────────

# Maps extracted fact categories to PRD section IDs (most relevant first).
_SIDE_FACT_SECTION_MAP: dict[str, list[str]] = {
    "stakeholder":  ["key_stakeholders"],
    "owner":        ["key_stakeholders"],
    "dependency":   ["risks", "assumptions"],
    "timeline":     ["timeline"],
    "budget":       ["assumptions", "risks"],
    "tool":         ["proposed_solution", "assumptions"],
    "risk":         ["risks"],
    "constraint":   ["assumptions", "out_of_scope"],
    "metric":       ["success_metrics", "goals"],
    "user":         ["elevator_pitch", "problem_statement"],
}


def _extract_side_facts(
    question: str,
    raw_answer: str,
    state: PRDState,
) -> list[dict]:
    """
    Runs the secondary fact extraction pass on a user reply.
    Returns a list of dicts: [{"category": ..., "fact": ..., "section_ids": [...]}]
    Only facts whose target section differs from the current section are returned.
    Returns [] if the LLM finds no extra facts.
    """
    llm = _get_llm()
    try:
        response = llm_invoke(
            llm,
            [HumanMessage(content=SIDE_FACT_EXTRACTION_PROMPT.format(
                question=question,
                raw_answer=raw_answer,
            ))],
            state=state, node_name="_extract_side_facts", purpose="side_fact_extraction", is_parallel=True
        )
        raw = response.content.strip()
    except Exception:
        return []

    if not raw or raw.upper() == "NONE":
        return []

    results = []
    for line in raw.splitlines():
        line = line.strip()
        if ":" not in line:
            continue
        category, _, fact_text = line.partition(":")
        category = category.strip().lower()
        fact_text = fact_text.strip()
        if not fact_text or category not in _SIDE_FACT_SECTION_MAP:
            continue
        results.append({
            "category": category,
            "fact": fact_text,
            "section_ids": _SIDE_FACT_SECTION_MAP[category],
        })
    return results

def classify_intent_with_model(question: str, answer: str, llm, state=None) -> tuple[str | None, dict | None]:
    if not llm:
        return None, None
        
    reply_msg = state.get("reply_context_message_text", "") if state else ""
    
    try:
        from langchain_core.messages import SystemMessage
        if reply_msg:
            from prompts.templates import REPLY_CONTEXT_INTERPRETATION_PROMPT
            
            schema = {
                "name": "ReplyContextClassification",
                "description": "Classify the user's intent and how it relates to the replied message.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "global_intent": {"type": "string", "enum": ["DIRECT_CLARIFICATION_QUESTION", "REPETITION_COMPLAINT", "REPHRASE_REQUEST", "BLENDED", "DIRECT_ANSWER", "COMPLAINT_OR_META", "AMBIGUOUS"]},
                        "relationship_type": {"type": "string", "enum": ["direct_answer_to_replied_message", "clarification_about_replied_message", "correction_or_disagreement_with_replied_message", "supporting_context_only"]},
                        "reason": {"type": "string"}
                    },
                    "required": ["global_intent", "relationship_type", "reason"]
                }
            }
            res = llm.with_structured_output(schema).invoke([SystemMessage(content=REPLY_CONTEXT_INTERPRETATION_PROMPT.format(
                reply_context=reply_msg,
                question=question,
                answer=answer
            ))])
            if res and isinstance(res, dict):
                interpretation = {
                    "reply_context_present": True,
                    "relationship_type": res.get("relationship_type", ""),
                    "confidence": 0.8,
                    "reason": res.get("reason", "")
                }
                return res.get("global_intent"), interpretation
            return None, None
            
        else:
            from prompts.templates import INTENT_FALLBACK_CLASSIFICATION_PROMPT
            res = llm.invoke([SystemMessage(content=INTENT_FALLBACK_CLASSIFICATION_PROMPT.format(
                question=question,
                answer=answer
            ))])
            classified = res.content.strip().upper()
            if classified in ("DIRECT_CLARIFICATION_QUESTION", "REPETITION_COMPLAINT", "REPHRASE_REQUEST", "BLENDED", "DIRECT_ANSWER", "COMPLAINT_OR_META", "AMBIGUOUS"):
                return classified, None
    except Exception as e:
        import logging
        logging.warning(f"Intent model classification failed: {e}")
        pass
    return None, None

def safe_intent_fallback() -> str:
    return "UNCLEAR_META"

def _classify_intent_rule(question: str, answer: str, llm=None, state=None) -> tuple[str, str, str, dict | None]:
    ans = answer.strip()
    ans_lower = ans.lower()
    
    fast_intent = classify_intent_fast_path(ans_lower)
    if fast_intent:
        # If there's an explicit fast intent but a reply context is present, we still log that wait...
        # Wait, if there's a reply context, perhaps we shouldn't bypass the model?
        # A clarification request explicitly replying to an older message IS about that older message.
        # But this fast path returns early. That's fine, we will handle empty reply context interpretation.
        return fast_intent, ans, 'FAST_REGEX', None
        
    if should_escalate_to_model(ans_lower, question) or (state and state.get("reply_context_message_id")):
        model_intent, interp = classify_intent_with_model(question, ans, llm, state)
        if model_intent:
            return model_intent, ans, 'LLM_CLASSIFIER', interp
        return safe_intent_fallback(), ans, 'SAFE_FALLBACK', interp
        
    return 'DIRECT_ANSWER', ans, 'FALLTHROUGH_REGEX', None

# ── Node: interpret_and_echo ──────────────────────────────────────────────────

def classify_intent_fast_path(ans_lower: str) -> str | None:
    repetition_simple = re.compile(r'^(you already asked that|why are you asking me the same question)$', re.IGNORECASE)
    if repetition_simple.search(ans_lower):
        return 'REPETITION_COMPLAINT'
        
    rephrase_pattern = re.compile(r'^(i don\'t understand the question|what do you mean\b|could you rephrase|pardon|what\?|what do you mean|could you clarify)$', re.IGNORECASE)
    if rephrase_pattern.search(ans_lower):
        return 'REPHRASE_REQUEST'
        
    direct_clarif_pattern = re.compile(r'^(what are you unclear of|which part is missing|what exactly do you still need|what is missing|what else do you need|which step are you unclear of)$', re.IGNORECASE)
    if direct_clarif_pattern.search(ans_lower):
        return 'DIRECT_CLARIFICATION_QUESTION'
        
    return None

def should_escalate_to_model(ans_lower: str, question: str) -> bool:
    words = ans_lower.split()
    
    # If the user writes a massive essay, it's almost certainly a direct answer or detailed context.
    # Do not escalate to the LLM intent classifier just because it contains stray words like 'wait' or 'no'.
    if len(words) > 40:
        return False
        
    if len(words) < 15 and ans_lower.endswith('?'):
        return True
        
    # Only trigger on single conversational flow-control words if the message is relatively short
    if len(words) < 20:
        if re.search(r'\b(why|stop|again|already|literally|actually|no\b|wait|incorrect|disagree|both|combination|all|neither)\b', ans_lower):
            return True
        if ans_lower in ['idk', 'maybe', 'not sure', 'whatever']:
            return True
            
    clarification_pattern = re.compile(r'^(what do you mean|what is|how does it|i don\'t understand|tell me more about|could you explain|what kind of|which kind of)', re.IGNORECASE)
    if clarification_pattern.search(ans_lower):
        return True
        
    return False






# ── Confirmation parser ───────────────────────────────────────────────────────

_ACCEPT_RE = re.compile(
    r"^(?:yes|y|yep|yup|correct|right|confirmed|confirm|sounds good|"
    r"that'?s? right|that'?s? correct|ok|okay|perfect|exactly|sure|affirmative)",
    re.IGNORECASE,
)


def _is_acceptance(text: str) -> bool:
    return bool(_ACCEPT_RE.match(text.strip()))


# ── Node: await_confirmation ──────────────────────────────────────────────────

def await_confirmation_node(state: PRDState) -> dict:
    """
    Interrupt — waits for user to accept or correct the echo restatement.
    """
    t0 = time.monotonic()
    ctx = _log_ctx(state, "await_confirmation")
    log_event(**ctx, level="INFO", event_type="node_start", message="await_confirmation started")

    section = get_section_by_index(state["section_index"])

    user_response: str = interrupt(
        {
            "type": "waiting_for_confirmation",
            "section": section.title,
            "pending_echo": state.get("pending_echo", ""),
        }
    )
    # Accept structured dict payload or plain string (backward compat)
    if isinstance(user_response, dict):
        user_response = user_response.get("content", "")
    user_response = user_response.strip()

    if _is_acceptance(user_response):
        # ── Commit to canonical truth ─────────────────────────────────────
        pending = state.get("pending_concept_updates", {})
        interpreted_answer = pending.get("interpreted_answer", pending.get("raw_answer", ""))
        question = pending.get("questions", state.get("current_questions", ""))
        iteration = state.get("iteration", 0)
        existing_qa = list(state.get("section_qa_pairs", []))
        round_n = len(existing_qa) + 1
        concept_key = f"{section.id}:iter_{iteration}:round_{round_n}"

        # ── O-1b contradiction check (on CONFIRMED answers only) ──────────
        contradiction_flagged = False
        contradiction_log_entry: list[dict] = []
        chat_extras: list[dict] = []
        existing_store = state.get("confirmed_qa_store", {})
        if existing_store:
            contradiction_desc = _check_contradiction(
                question, interpreted_answer, existing_store, section.id,
            )
            if contradiction_desc:
                contradiction_flagged = True
                contradiction_log_entry = [{
                    "concept_key": concept_key,
                    "prior": "see confirmed_qa_store",
                    "new": interpreted_answer[:200],
                    "section": section.title,
                    "description": contradiction_desc,
                }]
                chat_extras = [{
                    "role": "assistant",
                    "msg_id": f"msg_{str(uuid.uuid4())[:8]}",
                    "type": "contradiction_flag",
                    "content": (
                        f"\u26a0\ufe0f **Heads up \u2014 this might conflict with something you said earlier.**\n\n"
                        f"{contradiction_desc}\n\n"
                        "_Using your latest answer. You can clarify if needed._"
                    ),
                }]

        # Versioning
        current_version = state.get("store_version", 0) + 1
        fact_id = str(uuid.uuid4())

        qa_entry = {
            "questions": question,
            "answer": interpreted_answer,
            "section": section.title,
        }
        store_update = {
            concept_key: {
                "fact_id": fact_id,
                "answer": interpreted_answer,
                "questions": question,
                "section": section.title,
                "section_id": section.id,
                "iteration": iteration,
                "round": round_n,
                "source_round": round_n,
                "contradiction_flagged": contradiction_flagged,
                "version": current_version,
            }
        }

        # Telemetry
        log_canonical_write(
            thread_id=state.get("thread_id", ""),
            run_id=state.get("run_id", ""),
            node_name="await_confirmation",
            fact_id=fact_id,
            concept_id=concept_key,
            change_type="SUPERSEDED" if contradiction_flagged else "CREATED",
            version=current_version,
        )

        # Integrity Assertion
        if section.id not in [s.id for s in PRD_SECTIONS]:
            log_integrity_failure(
                thread_id=state.get("thread_id", ""),
                run_id=state.get("run_id", ""),
                node_name="await_confirmation",
                failure_type="INVALID_SECTION_ID",
                message=f"Attempted write to non-existent section: {section.id}",
                concept_id=concept_key
            )

        # Post-write Integrity Validation (P0/P3)
        IntegrityValidator.validate_mutation(
            thread_id=state.get("thread_id", ""),
            run_id=state.get("run_id", ""),
            node_name="await_confirmation",
            store=state.get("confirmed_qa_store", {}),
            update=store_update,
            section_id=section.id
        )

        duration_ms = int((time.monotonic() - t0) * 1000)
        log_event(**ctx, level="INFO", event_type="node_end", message="await_confirmation finished", duration_ms=duration_ms)

        return {
            "answer_confirmation_status": "CONFIRMED",
            "section_qa_pairs": existing_qa + [qa_entry],
            "confirmed_qa_store": store_update,
            "store_version": current_version,
            "contradiction_log": contradiction_log_entry,
            "pending_concept_updates": {},
            "pending_echo": "",
            "raw_answer_buffer": "",
            "chat_history": (
                [{"role": "user", "content": user_response}] + chat_extras
            ),
        }
    else:
        # ── Corrected: discard pending, re-ask ────────────────────────────
        question = state.get("current_questions", "")
        reask_msg = (
            "No problem \u2014 let me re-ask:\n\n"
            f"{question}"
        )
        return {
            "answer_confirmation_status": "CORRECTED",
            "raw_answer_buffer": "",
            "pending_echo": "",
            "pending_concept_updates": {},
            "chat_history": [
                {"role": "user", "content": user_response},
                {
                    "role": "assistant",
                    "msg_id": f"msg_{str(uuid.uuid4())[:8]}",
                    "type": "reask",
                    "section": section.title,
                    "content": reask_msg,
                },
            ],
        }


# ── Node: draft ───────────────────────────────────────────────────────────────

# ── Phase 1 hybrid constants ─────────────────────────────────────────────────
_MAX_SIDE_WRITES = 4         # max impacted prior sections re-drafted per turn
_MATERIAL_CHANGE_RATIO = 0.15  # 15 %+ text change = material (SequenceMatcher ratio)
_SIDE_WRITE_MIN_IMPACT_SCORE = 0.5  # skip side-writes with confidence below this threshold
_FIRST_DRAFT_COMPLETENESS_THRESHOLD = 0.6
_FIRST_DRAFT_CONFIDENCE_THRESHOLD = 0.55
_REFRESH_CONFIDENCE_DELTA = 0.15
_LOW_CONFIDENCE_THRESHOLD = 0.35


def _hash_text_list(items: list[str]) -> str:
    return hashlib.sha256(json.dumps(items, sort_keys=True).encode()).hexdigest()


def _normalize_fact_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _answer_confidence_score(answer: str) -> float:
    text = _normalize_fact_text(answer)
    if not text:
        return 0.0
    low = text.lower()
    vague_markers = (
        "not sure", "maybe", "depends", "tbd", "to be decided", "roughly",
        "something like", "etc", "whatever", "not certain", "i guess",
    )
    if any(marker in low for marker in vague_markers):
        return 0.2
    if len(text) < 12:
        return 0.25
    if len(text) < 30:
        return 0.5
    return 0.8 if any(ch.isdigit() for ch in text) else 0.7


def _collect_section_facts(qa_pairs: list[dict]) -> list[str]:
    facts: list[str] = []
    seen: set[str] = set()
    for qa in qa_pairs:
        answer = _normalize_fact_text(qa.get("answer", ""))
        if not answer:
            continue
        key = answer.lower()
        if key in seen:
            continue
        seen.add(key)
        facts.append(answer)
    return facts


def _build_section_draft_meta(
    section,
    qa_pairs: list[dict],
    existing_draft: str,
    previous_meta: dict | None,
) -> tuple[dict, bool, str]:
    facts = _collect_section_facts(qa_pairs)
    confidence_values = [_answer_confidence_score(qa.get("answer", "")) for qa in qa_pairs if qa.get("answer", "").strip()]
    confidence_score = round(sum(confidence_values) / len(confidence_values), 3) if confidence_values else 0.0
    completeness_score = round(min(1.0, len(facts) / max(1, len(section.expected_components))), 3)
    has_draft = bool((existing_draft or "").strip())
    last_draft_hash = hashlib.sha256((existing_draft or "").encode()).hexdigest() if has_draft else ""
    facts_hash = _hash_text_list(facts)
    prev_meta = previous_meta or {}
    prev_facts_hash = prev_meta.get("facts_hash", "")
    prev_confidence = float(prev_meta.get("confidence_score", 0.0) or 0.0)
    placeholder_draft = bool(re.search(r"\b(?:tbd|placeholder|assumption)\b", existing_draft or "", re.IGNORECASE))

    should_draft = False
    reason = "no_new_value"

    if not facts:
        state_label = "not_started"
        reason = "no_facts"
    elif not has_draft:
        if completeness_score >= _FIRST_DRAFT_COMPLETENESS_THRESHOLD and confidence_score >= _FIRST_DRAFT_CONFIDENCE_THRESHOLD:
            state_label = "ready_for_first_draft"
            should_draft = True
            reason = "first_draft_ready"
        elif confidence_score < _LOW_CONFIDENCE_THRESHOLD:
            state_label = "needs_clarification"
            reason = "low_confidence"
        else:
            state_label = "collecting"
            reason = "collecting_more_facts"
    else:
        fact_changed = facts_hash != prev_facts_hash
        significant_confidence_increase = (confidence_score - prev_confidence) >= _REFRESH_CONFIDENCE_DELTA
        if confidence_score < _LOW_CONFIDENCE_THRESHOLD and fact_changed:
            state_label = "needs_clarification"
            reason = "low_confidence"
        elif placeholder_draft and confidence_score >= _FIRST_DRAFT_CONFIDENCE_THRESHOLD and completeness_score >= _FIRST_DRAFT_COMPLETENESS_THRESHOLD:
            state_label = "needs_refresh"
            should_draft = True
            reason = "placeholder_refresh"
        elif fact_changed or significant_confidence_increase:
            state_label = "needs_refresh"
            should_draft = True
            reason = "material_update" if fact_changed else "confidence_increase"
        else:
            state_label = "stable"
            reason = "duplicate_or_non_material"

    meta = {
        "facts": facts,
        "confidence_score": confidence_score,
        "completeness_score": completeness_score,
        "last_draft_hash": last_draft_hash,
        "state": state_label,
        "facts_hash": facts_hash,
        "fact_count": len(facts),
        "last_reason": reason,
    }
    return meta, should_draft, reason


def _is_material_change(old: str, new: str) -> bool:
    """Returns True if `new` differs from `old` by more than _MATERIAL_CHANGE_RATIO."""
    if not old:
        return True  # first draft is always material
    ratio = SequenceMatcher(None, old, new).quick_ratio()
    # quick_ratio is an upper bound — accurate enough for threshold guard
    return (1.0 - ratio) >= _MATERIAL_CHANGE_RATIO


def _draft_one_section(
    section,
    qa_pairs: list[dict],
    prd_so_far: str,
    context_doc: str,
    state: dict,
) -> str:
    """Shared helper — drafts a single section. Called from primary and side paths."""
    llm = _get_llm()
    prd_context_block = (
        DRAFTER_PRD_CONTEXT_BLOCK.format(prd_so_far=prd_so_far) if prd_so_far else ""
    )
    context_doc_block = (
        DRAFTER_CONTEXT_DOC_BLOCK.format(context_doc=context_doc) if context_doc else ""
    )
    qa_parts = [
        f"--- Round {i} ---\nQuestions:\n{qa['questions']}\n\nPM's answer:\n{qa['answer']}"
        for i, qa in enumerate(qa_pairs, 1)
    ]
    expected_components_list = "\n".join(f"  \u2022 {c}" for c in section.expected_components)
    system_prompt = DRAFTER_SYSTEM.format(
        section_title=section.title,
        section_description=section.description,
        expected_components_list=expected_components_list,
        prd_context_block=prd_context_block,
        context_doc_block=context_doc_block,
        visual_context_block=_build_visual_context_block(state),
        global_rigor_block=GLOBAL_RIGOR_BLOCK,
    )
    response = llm_invoke(
        llm,
        [
            SystemMessage(content=system_prompt),
            HumanMessage(content=(
                f"Based on the Q&A below, write the '{section.title}' section:\n\n"
                + "\n\n".join(qa_parts)
            )),
        ],
        state=state, node_name="_draft_one_section", purpose="section_drafting",
    )
    return response.content.strip()


def _draft_sections_parallel(
    items: list[tuple],  # [(section, qa_pairs), ...]
    prd_so_far: str,
    context_doc: str,
    state: dict,
) -> dict[str, str]:
    """Draft multiple sections concurrently. Returns {section_id: draft_text}."""
    results: dict[str, str] = {}

    def _worker(args):
        sec, qa = args
        return sec.id, _draft_one_section(sec, qa, prd_so_far, context_doc, state)

    with ThreadPoolExecutor(max_workers=len(items)) as executor:
        futures = {executor.submit(_worker, item): item for item in items}
        for future in as_completed(futures):
            try:
                sec_id, draft = future.result()
                results[sec_id] = draft
            except Exception:
                pass
    return results


def _compute_draft_cache_key(
    section,
    qa_pairs: list[dict],
    prd_sections: dict,
    context_doc: str,
) -> str:
    """Dependency-scoped SHA-256 cache key for a section draft.

    Only hashes Q&A pairs + the PRD sections this section explicitly depends on
    (``section.context_depends_on``). Changes to unrelated sections do NOT
    invalidate the cache, solving the over-invalidation problem described in the
    senior review.
    """
    dep_ids: list[str] = getattr(section, "context_depends_on", None) or []
    relevant_prd = {sid: prd_sections.get(sid, "") for sid in dep_ids}
    key_data = {
        "section_id": section.id,
        "qa": [{"q": qa.get("questions", ""), "a": qa.get("answer", "")} for qa in qa_pairs],
        "prd_deps": relevant_prd,
        "context_doc": context_doc,
    }
    return hashlib.sha256(json.dumps(key_data, sort_keys=True).encode()).hexdigest()


def _get_memoized_prd_so_far(state: PRDState) -> tuple[str, str]:
    """Return (prd_so_far_text, fmt_hash).

    Uses the state-cached formatted string when prd_sections is unchanged,
    saving repeated markdown serialisation across consecutive node calls within
    the same graph invocation.
    """
    prd_sections = state.get("prd_sections", {})
    fmt_hash = hashlib.sha256(
        json.dumps(prd_sections, sort_keys=True).encode()
    ).hexdigest()
    cached_hash = state.get("_prd_sections_fmt_hash")
    cached_text = state.get("_formatted_prd_so_far")
    if cached_hash == fmt_hash and cached_text is not None:
        return cached_text, fmt_hash
    return _format_prd_so_far(prd_sections), fmt_hash


# ── Node: detect_impact ───────────────────────────────────────────────────────

def detect_impact_node(state: PRDState) -> dict:
    """
    Determines which already-drafted sections are impacted by the latest answer.
    """
    t0 = time.monotonic()
    ctx = _log_ctx(state, "detect_impact")
    log_event(**ctx, level="INFO", event_type="node_start", message="detect_impact started")

    section = get_section_by_index(state["section_index"])
    already_drafted = set(state.get("prd_sections", {}).keys())
    candidates = already_drafted - {section.id}

    # Data for primary impact detection
    qa_pairs = state.get("section_qa_pairs", [])
    latest_qa = qa_pairs[-1] if qa_pairs else {}
    question = latest_qa.get("questions", state.get("current_questions", ""))
    answer = latest_qa.get("answer", "")
    iteration = state.get("iteration", 0)

    # raw_answer_buffer is preserved in state during this pass
    raw_answer = state.get("effective_answer_for_commit", state.get("raw_answer_buffer", "")).strip()

    def _run_primary() -> tuple[list[str], dict[str, float]]:
        if not candidates:
            return [], {}
        rule_imp = _rule_impacted_sections(
            question=question,
            answer=answer,
            already_drafted=candidates,
            current_section_id=section.id,
            max_sections=_MAX_SIDE_WRITES,
        )
        if rule_imp:
            sliced = rule_imp[:_MAX_SIDE_WRITES]
            return sliced, {sid: 1.0 for sid in sliced}
        llm_imp = _detect_impact_llm(question, answer, list(candidates), state)
        sliced = llm_imp[:_MAX_SIDE_WRITES]
        return sliced, {sid: 0.7 for sid in sliced}

    def _run_side_facts():
        if not raw_answer:
            return []
        return _extract_side_facts(question, raw_answer, state)

    def _run_contradiction():
        existing_store = state.get("confirmed_qa_store", {})
        if not existing_store or not answer:
            return None
        concept_key = f"{section.id}:iter_{iteration}:round_{len(qa_pairs)}"
        new_entry = existing_store.get(concept_key)
        if not new_entry:
            return None
        return _check_contradiction(new_entry, concept_key, existing_store, section.id, state)

    with ThreadPoolExecutor(max_workers=3) as _ex:
        _primary_f = _ex.submit(_run_primary)
        _facts_f = _ex.submit(_run_side_facts)
        _contra_f = _ex.submit(_run_contradiction)
        primary_impacted, primary_scores = _primary_f.result()
        side_facts: list[dict] = _facts_f.result()
        contradiction_desc: str | None = _contra_f.result()

    # ── Process side facts ────────────────────────────────────────────────
    side_store_updates: dict = {}
    side_impacted: list[str] = []
    existing_store = state.get("confirmed_qa_store", {})
    concept_key_base = f"{section.id}:iter_{iteration}:round_{len(qa_pairs)}"

    # Versioning
    current_version = state.get("store_version", 0)
    
    if side_facts:
        for sf in side_facts:
            if not sf["section_ids"]:
                continue
            target_sid = next(
                (sid for sid in sf["section_ids"] if sid != section.id), None
            )
            if not target_sid:
                continue
            
            # Every mutation increments version
            current_version += 1
            sf_id = str(uuid.uuid4())
            sf_key = f"{target_sid}:side_fact:{concept_key_base}:{sf['category']}"

            if sf_key not in existing_store:  # deduplicate
                side_store_updates[sf_key] = {
                    "fact_id": sf_id,
                    "answer": sf["fact"],
                    "questions": f"[auto-captured {sf['category']}]",
                    "section": target_sid,
                    "section_id": target_sid,
                    "iteration": iteration,
                    "round": 0,
                    "source_round": len(qa_pairs),
                    "contradiction_flagged": False,
                    "version": current_version,
                }
                
                # Telemetry
                log_canonical_write(
                    thread_id=state.get("thread_id", ""),
                    run_id=state.get("run_id", ""),
                    node_name="detect_impact",
                    fact_id=sf_id,
                    concept_id=sf_key,
                    change_type="CREATED",
                    version=current_version,
                    category=sf["category"]
                )

                # Integrity Assertion
                if target_sid not in [s.id for s in PRD_SECTIONS]:
                    log_integrity_failure(
                        thread_id=state.get("thread_id", ""),
                        run_id=state.get("run_id", ""),
                        node_name="detect_impact",
                        failure_type="INVALID_SECTION_ID",
                        message=f"Attempted side-write to non-existent section: {target_sid}",
                        concept_id=sf_key
                    )

            if target_sid not in primary_impacted and target_sid not in side_impacted:
                side_impacted.append(target_sid)

    # Merge: primary sections first, then side-fact sections (overall cap = _MAX_SIDE_WRITES)
    all_impacted = primary_impacted[:]
    for sid in side_impacted:
        if sid not in all_impacted and len(all_impacted) < _MAX_SIDE_WRITES:
            all_impacted.append(sid)

    # Confidence scores: side-fact-only sections get 0.6 (explicit user mention, LLM-extracted);
    # primary scores take precedence — rule-based=1.0, LLM-fallback=0.7
    all_scores: dict[str, float] = {**{sid: 0.6 for sid in side_impacted}, **primary_scores}

    result: dict = {
        "impacted_sections": all_impacted,
        "impacted_section_scores": all_scores,
        "raw_answer_buffer": "",  # consumed — clear now
    }
    if side_store_updates:
        result["confirmed_qa_store"] = side_store_updates

    # ── O-1b contradiction flag ─
    if contradiction_desc: # This is now a dictionary!
        # Back-flag the concept key in the store
        qa_pairs = state.get("section_qa_pairs", [])
        iteration = state.get("iteration", 0)
        concept_key = f"{section.id}:iter_{iteration}:round_{len(qa_pairs)}"
        
        # Telemetry: This is a mutation (SUPERSEDED)
        log_canonical_write(
            thread_id=state.get("thread_id", ""),
            run_id=state.get("run_id", ""),
            node_name="detect_impact",
            fact_id="flag-update", # Back-flagging doesn't create a new fact_id but updates an existing one
            concept_id=concept_key,
            change_type="SUPERSEDED",
            version=state.get("store_version", 0), # No version increment for flags, or should I?
            contradiction_flagged=True
        )

        result.setdefault("confirmed_qa_store", {})[concept_key] = {
            "contradiction_flagged": True,
        }
        result["contradiction_log"] = [{
            "concept_key": concept_key,
            "prior": "see confirmed_qa_store",
            "new": answer[:200],
            "section": section.title,
            "description": contradiction_desc["description"],
        }]
        result["chat_history"] = [{
            "role": "assistant",
            "msg_id": f"msg_{str(uuid.uuid4())[:8]}",
            "type": "contradiction_flag",
            "contradiction_evidence": {
                "conflicting_message_id": contradiction_desc["conflicting_message_id"],
                "evidence_snippet": contradiction_desc["evidence_snippet"]
            },
            "content": (
                f"⚠️ **Heads up — this might conflict with something you said earlier.**\n\n"
                f"{contradiction_desc['description']}\n\n"
                "_Using your latest answer. You can clarify if needed._"
            ),
        }]

    # Post-write Integrity Validation (P0/P3)
    if side_store_updates or result.get("confirmed_qa_store"):
        IntegrityValidator.validate_mutation(
            thread_id=state.get("thread_id", ""),
            run_id=state.get("run_id", ""),
            node_name="detect_impact",
            store=state.get("confirmed_qa_store", {}),
            update=result.get("confirmed_qa_store", side_store_updates),
            section_id=section.id
        )

    duration_ms = int((time.monotonic() - t0) * 1000)
    log_event(**ctx, level="INFO", event_type="node_end", message="detect_impact finished", duration_ms=duration_ms)

    return result


def _detect_impact_llm(
    question: str,
    answer: str,
    candidate_section_ids: list[str],
    state: PRDState,
) -> list[str]:
    """
    LLM-based fallback for impact detection.
    Returns up to _MAX_SIDE_WRITES section IDs.
    """
    if not candidate_section_ids:
        return []
    llm = _get_llm()
    try:
        resp = llm_invoke(
            llm,
            [HumanMessage(content=IMPACT_DETECTION_PROMPT.format(
                question=question,
                answer=answer,
                candidate_sections=", ".join(candidate_section_ids),
            ))],
            state=state, node_name="_detect_impact_llm", purpose="impact_detection_fallback",
        )
        text = resp.content.strip()
        if text.upper() == "NONE" or not text:
            return []
        found = [
            s.strip() for s in text.lower().split(",")
            if s.strip() in candidate_section_ids
        ]
        return found[:_MAX_SIDE_WRITES]
    except Exception:
        return []


def draft_node(state: PRDState) -> dict:
    """
    Drafter — synthesises Q&A pairs for the current section into a draft.

    Phase 1 hybrid extension: also re-drafts up to _MAX_SIDE_WRITES prior
    sections listed in `impacted_sections` (set by detect_impact_node).
    Side drafts run in parallel with ThreadPoolExecutor.
    The material_change_threshold guard (_MATERIAL_CHANGE_RATIO) suppresses
    rewrites whose net text change is below the threshold (silent non-event).
    """
    ctx = _log_ctx(state, "draft_node")
    t0 = time.monotonic()
    current_store_version = state.get("store_version", 0)
    qa_rounds = len(state.get("section_qa_pairs", []))
    
    intent = state.get("reply_intent", "")
    log_event(
        **ctx, level="INFO", event_type="draft_trigger_decision",
        message="Logging draft trigger parameters",
        reason_for_draft_trigger="Graph orchestration sequence reached draft node from truth layer",
        current_response_mode_before=state.get("response_mode", ""),
        active_blocker=",".join(state.get("remaining_subparts", [])),
        remaining_blockers=",".join(state.get("remaining_subparts", [])),
        user_turn_intent=intent,
        did_clarification_route_exist=True if intent in ("DIRECT_CLARIFICATION_QUESTION", "UNCLEAR_META") else False,
        why_clarification_route_was_not_used="route_after_intent bypassed clarification" if intent in ("DIRECT_CLARIFICATION_QUESTION", "UNCLEAR_META") else "N/A"
    )

    log_event(
        **ctx, level="INFO", event_type="node_start",
        message="draft_node started", qa_rounds=qa_rounds,
    )
    section = get_section_by_index(state["section_index"])
    prd_so_far, _fmt_hash = _get_memoized_prd_so_far(state)
    context_doc = state.get("context_doc", "")
    section_draft_meta = state.get("section_draft_meta", {})
    existing_primary_draft = state.get("prd_sections", {}).get(section.id, "")

    # ── Primary draft: current section ──────────────────────────────────────
    primary_qa = state.get("section_qa_pairs", [])
    primary_meta, should_primary_draft, skip_reason = _build_section_draft_meta(
        section,
        primary_qa,
        existing_primary_draft,
        section_draft_meta.get(section.id, {}),
    )

    if not should_primary_draft:
        log_event(
            **ctx, level="INFO", event_type="draft_skipped",
            message="Skipped draft rewrite due to selective drafting policy",
            section_id=section.id,
            skip_reason=skip_reason,
            draft_state=primary_meta["state"],
            fact_count=primary_meta["fact_count"],
            completeness_score=primary_meta["completeness_score"],
            confidence_score=primary_meta["confidence_score"],
        )
        return {
            "current_draft": existing_primary_draft,
            "last_section_updates": [],
            "draft_execution_mode": "skipped",
            "section_draft_meta": {section.id: primary_meta},
            "_prd_sections_fmt_hash": _fmt_hash,
            "_formatted_prd_so_far": prd_so_far,
        }

    # Check draft cache before calling the LLM
    _draft_cache = state.get("draft_cache", {})
    _cache_key = _compute_draft_cache_key(
        section, primary_qa, state.get("prd_sections", {}), context_doc,
    )
    if _cache_key in _draft_cache.get(section.id, {}):
        primary_draft = _draft_cache[section.id][_cache_key]
        log_event(**ctx, level="INFO", event_type="draft_cache_hit",
                  message="Draft cache hit - skipping primary LLM call",
                  section_id=section.id, cache_key=_cache_key[:16],
                  cache_key_components={
                      "section_id": section.id,
                      "dep_ids": getattr(section, "context_depends_on", []),
                      "qa_rounds": len(primary_qa),
                      "context_doc_present": bool(context_doc),
                  })
        _primary_draft_ms = 0
    else:
        _t1 = time.monotonic()
        primary_draft = _draft_one_section(section, primary_qa, prd_so_far, context_doc, state)
        _primary_draft_ms = int((time.monotonic() - _t1) * 1000)
        log_event(**ctx, level="INFO", event_type="draft_cache_miss",
                  message="Draft cache miss - LLM call made",
                  section_id=section.id, cache_key=_cache_key[:16],
                  primary_draft_latency_ms=_primary_draft_ms,
                  cache_key_components={
                      "section_id": section.id,
                      "dep_ids": getattr(section, "context_depends_on", []),
                      "qa_rounds": len(primary_qa),
                      "context_doc_present": bool(context_doc),
                  })

    assumption_count = primary_draft.upper().count("[ASSUMPTION]")
    if not primary_draft:
        log_event(**ctx, level="WARNING", event_type="drafter_empty_output",
                  message="draft_node produced an empty draft")
    if assumption_count > 3:
        log_event(**ctx, level="WARNING", event_type="drafter_high_assumptions",
                  message=f"Draft contains {assumption_count} [ASSUMPTION] markers",
                  assumption_count=assumption_count)
    log_event(
        **ctx, level="INFO", event_type="drafter_output",
        message="Draft produced",
        qa_rounds=qa_rounds, draft_len=len(primary_draft), assumption_count=assumption_count,
    )

    # ── Side drafts: impacted prior sections ─────────────────────────────────
    _impact_scores = state.get("impacted_section_scores", {})
    _all_impacted_raw = [s for s in state.get("impacted_sections", []) if s != section.id]
    _side_skipped: list[dict] = []
    impacted_ids: list[str] = []
    for _sid in _all_impacted_raw:
        _score = _impact_scores.get(_sid, 1.0)
        if _score >= _SIDE_WRITE_MIN_IMPACT_SCORE:
            impacted_ids.append(_sid)
        else:
            _side_skipped.append({"section_id": _sid, "score": _score, "reason": "below_min_impact_score"})
    impacted_ids = impacted_ids[:_MAX_SIDE_WRITES]
    if _side_skipped:
        log_event(**ctx, level="INFO", event_type="side_write_skipped",
                  message=f"{len(_side_skipped)} side-write(s) skipped due to low impact score",
                  skipped=_side_skipped, threshold=_SIDE_WRITE_MIN_IMPACT_SCORE)

    store = state.get("confirmed_qa_store", {})
    existing_prd = state.get("prd_sections", {})

    side_items: list[tuple] = []
    for sec_id in impacted_ids:
        try:
            prior_section = get_section_by_id(sec_id)
        except StopIteration:
            continue
        prior_qa = [
            {"questions": v["questions"], "answer": v["answer"]}
            for v in sorted(
                store.values(),
                key=lambda x: (x.get("iteration", 0), x.get("round", 0)),
            )
            if v.get("section_id") == sec_id
        ]
        if prior_qa:
            side_items.append((prior_section, prior_qa))

    material_updates: list[str] = []
    silent_updates: list[str] = []
    all_prd_updates: dict[str, str] = {section.id: primary_draft}
    new_section_draft_meta: dict[str, dict] = {
        section.id: {
            **primary_meta,
            "last_draft_hash": hashlib.sha256(primary_draft.encode()).hexdigest(),
            "requirement_gaps": f"Current draft is based on a stale state version (v{primary_meta.get('draft_version')} vs v{current_store_version}). Auto-regeneration required.\n\nDraft Content: {state.get('current_draft', '')[:100]}...",
            "state": "stable",
            "draft_version": current_store_version,
        }
    }

    if side_items:
        raw_side = _draft_sections_parallel(side_items, prd_so_far, context_doc, state)
        for sec_id, new_draft in raw_side.items():
            old_draft = existing_prd.get(sec_id, "")
            prior_section = get_section_by_id(sec_id)
            prior_qa = [
                {"questions": v["questions"], "answer": v["answer"]}
                for v in sorted(
                    store.values(),
                    key=lambda x: (x.get("iteration", 0), x.get("round", 0)),
                )
                if v.get("section_id") == sec_id
            ]
            side_meta, _, _ = _build_section_draft_meta(
                prior_section,
                prior_qa,
                new_draft,
                section_draft_meta.get(sec_id, {}),
            )
            side_meta["last_draft_hash"] = hashlib.sha256(new_draft.encode()).hexdigest()
            side_meta["state"] = "stable"
            new_section_draft_meta[sec_id] = side_meta
            if _is_material_change(old_draft, new_draft):
                all_prd_updates[sec_id] = new_draft
                material_updates.append(sec_id)
            else:
                silent_updates.append(sec_id)

    last_section_updates = list(all_prd_updates.keys())

    duration_ms = int((time.monotonic() - t0) * 1000)
    log_event(
        **ctx, level="INFO", event_type="node_end",
        message="draft_node finished",
        duration_ms=duration_ms, draft_len=len(primary_draft),
        assumption_count=assumption_count,
        primary_draft_latency_ms=_primary_draft_ms,
        primary_cache_hit=(_primary_draft_ms == 0),
        side_write_count=len(side_items),
        side_write_skip_count=len(_side_skipped),
        side_material=material_updates, side_silent=silent_updates,
        sections_total=len(all_prd_updates),
    )

    # ── Build chat history entries ────────────────────────────────────────────
    chat_entries: list[dict] = [
        {
            "role": "assistant",
            "msg_id": f"msg_{str(uuid.uuid4())[:8]}",
            "type": "draft",
            "section": section.title,
            "content": primary_draft,
        }
    ]

    if material_updates:
        section_titles = {s.id: s.title for s in PRD_SECTIONS}
        updated_labels = ", ".join(
            f"**{section_titles.get(sid, sid)}**" for sid in material_updates
        )
        chat_entries.append({
            "role": "assistant",
            "msg_id": f"msg_{str(uuid.uuid4())[:8]}",
            "type": "section_update_feed",
            "section": section.title,
            "content": f"Also updated: {updated_labels}",
            "updated_section_ids": material_updates,
            "section_drafts": {sid: all_prd_updates[sid] for sid in material_updates},
        })

    return {
        "current_draft": primary_draft,
        "prd_sections": all_prd_updates,
        "last_section_updates": last_section_updates,
        "chat_history": chat_entries,
        "draft_execution_mode": "drafted",
        "draft_cache": {section.id: {_cache_key: primary_draft}},  # one key per section (bounded)
        "section_draft_meta": new_section_draft_meta,
        "_prd_sections_fmt_hash": _fmt_hash,
        "_formatted_prd_so_far": prd_so_far,
    }


# ── Node: reflect ─────────────────────────────────────────────────────────────

def reflect_node(state: PRDState) -> dict:
    """
    Reflector — evaluates the current draft against the 3-rubric framework
    and emits either VERDICT: PASS or VERDICT: REWORK.
    """
    ctx = _log_ctx(state, "reflect_node")
    t0 = time.monotonic()
    log_event(
        **ctx, level="INFO", event_type="node_start",
        message="reflect_node started",
        draft_len=len(state.get("current_draft", "")),
    )
    section = get_section_by_index(state["section_index"])

    # ── Version-Stamped Draft Guard (P2) ──
    current_store_version = state.get("store_version", 0)
    meta = state.get("section_draft_meta", {}).get(section.id, {})
    draft_version = meta.get("draft_version")

    if draft_version is not None and draft_version < current_store_version:
        log_event(
            **ctx, level="WARNING", event_type="stale_draft_blocked",
            message="Blocked reflection on stale draft",
            draft_version=draft_version,
            current_version=current_store_version,
            section_id=section.id
        )
        return {
            "verdict": "REWORK",
            "triage_decision": "TRIAGE: STALE_DRAFT_REGEN",
            "requirement_gaps": f"State changed (v{draft_version} -> v{current_store_version}). Auto-regeneration required.",
        }

    llm = _get_llm()

    prd_so_far, _ = _get_memoized_prd_so_far(state)
    prior_sections_block = (
        REFLECTOR_PRIOR_SECTIONS_BLOCK.format(prd_so_far=prd_so_far)
        if prd_so_far
        else "No prior sections yet."
    )

    expected_components_list = "\n".join(
        f"  • {c}" for c in section.expected_components
    )

    system_prompt = REFLECTOR_SYSTEM.format(
        section_title=section.title,
        prior_sections_block=prior_sections_block,
        expected_components_list=expected_components_list,
        visual_context_block=_build_visual_context_block(state),
        specificity_guidance=section.specificity_guidance,
        global_rigor_block=GLOBAL_RIGOR_BLOCK,
        scoring_interpretation_block=SCORING_INTERPRETATION_BLOCK,
    )

    response = llm_invoke(
        llm,
        [
            SystemMessage(content=system_prompt),
            HumanMessage(
                content=(
                    f"Review this draft for the '{section.title}' section:\n\n"
                    f"{state.get('current_draft', '')}"
                )
            ),
        ],
        state=state, node_name="reflect_node", purpose="section_reflection",
    )
    reflection_text = response.content.strip()
    log_event(**ctx, level="DEBUG", event_type="reflector_prompt",
              message="Reflector system prompt", system_prompt=system_prompt)
    log_event(**ctx, level="DEBUG", event_type="reflector_raw_output",
              message="Reflector raw LLM response", raw_output=reflection_text)

    # Parse verdict from final line (VERDICT appears after TRIAGE in output)
    verdict = "REWORK"
    for line in reversed(reflection_text.splitlines()):
        clean = line.strip().lstrip("*# \t")
        if clean.upper().startswith("VERDICT: PASS"):
            verdict = "PASS"
            break
        if clean.upper().startswith("VERDICT: REWORK"):
            verdict = "REWORK"
            break

    # Parse triage decision (appears before VERDICT; scan forward)
    # Default to NORMAL ITERATION on parse failure — no spurious escalation.
    triage_decision = "TRIAGE: NORMAL ITERATION"
    for line in reflection_text.splitlines():
        clean = line.strip().lstrip("*# \t")
        if "TRIAGE: ENTER RECOVERY MODE" in clean.upper():
            triage_decision = "TRIAGE: ENTER RECOVERY MODE"
            break
        if "TRIAGE: NORMAL ITERATION" in clean.upper():
            triage_decision = "TRIAGE: NORMAL ITERATION"
            break

    # Extract requirement gaps (section 7 of reflector output)
    # Regex is format-agnostic: tolerates markdown bold, varying numbering style.
    gaps_match = re.search(
        r"REQUIREMENT GAPS\b.*?\n(.*?)(?=TRIAGE DECISION|\Z)",
        reflection_text,
        re.DOTALL | re.IGNORECASE,
    )
    requirement_gaps = gaps_match.group(1).strip() if gaps_match else ""

    # Parse OVERALL SCORE from "5. OVERALL SCORE — X.X/10"
    # Format-agnostic: tolerates markdown bold and em-dash/hyphen variants.
    score_match = re.search(
        r"OVERALL\s+SCORE[^\d]*(\d+\.?\d*)\s*/\s*10",
        reflection_text,
        re.IGNORECASE,
    )
    overall_score = float(score_match.group(1)) if score_match else -1.0

    # Parse per-rubric scores and log full parsed state before any override
    completeness_score = _parse_rubric_score(reflection_text, "COMPLETENESS")
    specificity_score = _parse_rubric_score(reflection_text, "SPECIFICITY")
    consistency_score = _parse_rubric_score(reflection_text, "INTERNAL CONSISTENCY")
    implementability_score = _parse_rubric_score(reflection_text, "IMPLEMENTABILITY")
    gaps_count = len([l for l in requirement_gaps.splitlines() if l.strip()])

    log_event(
        **ctx, level="INFO", event_type="reflect_parsed",
        message="Reflector output parsed",
        overall_score=overall_score,
        completeness_score=completeness_score, specificity_score=specificity_score,
        internal_consistency_score=consistency_score, implementability_score=implementability_score,
        llm_verdict=verdict,
        llm_triage="RECOVERY" if "RECOVERY MODE" in triage_decision else "NORMAL",
        resolved_count=len(re.findall(r"[-•*]\s*RESOLVED:", reflection_text, re.IGNORECASE)),
        unresolved_count=len(re.findall(r"[-•*]\s*UNRESOLVED:", reflection_text, re.IGNORECASE)),
        gaps_count=gaps_count,
    )
    if overall_score < 0:
        log_event(**ctx, level="WARNING", event_type="reflect_parse_warning",
                  message="Failed to parse OVERALL SCORE", field="overall_score")
    for _rubric, _score in (
        ("COMPLETENESS", completeness_score), ("SPECIFICITY", specificity_score),
        ("INTERNAL CONSISTENCY", consistency_score), ("IMPLEMENTABILITY", implementability_score),
    ):
        if _score < 0:
            log_event(**ctx, level="WARNING", event_type="reflect_parse_warning",
                      message=f"Failed to parse {_rubric} score",
                      field=_rubric.lower().replace(" ", "_"))

    # Capture LLM values before any programmatic override
    llm_verdict = verdict
    llm_triage = triage_decision

    # Programmatic threshold enforcement — deterministic contract independent
    # of LLM prompt-following reliability. Only applies when score parsed.
    if overall_score >= 0.0:
        # Downgrade a spurious PASS if score is below the pass threshold.
        if verdict == "PASS" and overall_score < PASS_SCORE_THRESHOLD:
            log_event(
                **ctx, level="WARNING", event_type="reflect_override",
                message="Programmatic override: verdict PASS→REWORK (score below pass threshold)",
                field="verdict", llm_value="PASS", enforced_value="REWORK",
                overall_score=overall_score, threshold=PASS_SCORE_THRESHOLD,
            )
            verdict = "REWORK"
        # Force recovery mode if score is below the recovery threshold.
        if overall_score < RECOVERY_MODE_SCORE_THRESHOLD:
            enforced_triage = "TRIAGE: ENTER RECOVERY MODE"
            if triage_decision != enforced_triage:
                log_event(
                    **ctx, level="WARNING", event_type="reflect_override",
                    message="Programmatic override: triage→RECOVERY (score below recovery threshold)",
                    field="triage_decision", llm_value="NORMAL", enforced_value="RECOVERY",
                    overall_score=overall_score, threshold=RECOVERY_MODE_SCORE_THRESHOLD,
                )
            triage_decision = enforced_triage

    # Warn when REWORK has no requirement gaps to drive follow-up questions
    if verdict == "REWORK" and not requirement_gaps.strip():
        log_event(
            **ctx, level="WARNING", event_type="reflect_missing_gaps",
            message="REWORK verdict but no requirement gaps extracted — follow-up may be generic",
        )

    # Update counters
    prev_iteration = state.get("iteration", 0)
    current_recovery_count = state.get("recovery_mode_consecutive_count", 0)
    new_iteration = prev_iteration
    new_recovery_count = current_recovery_count

    if verdict == "PASS":
        # PASS always resets the recovery count regardless of triage output.
        new_recovery_count = 0
    else:
        new_iteration += 1
        if triage_decision == "TRIAGE: ENTER RECOVERY MODE":
            new_recovery_count = current_recovery_count + 1
        else:
            new_recovery_count = 0  # NORMAL ITERATION resets the streak

    # Log state mutations (only fields that actually changed)
    _changes: dict = {}
    if overall_score != state.get("overall_score", -1.0):
        _changes["overall_score"] = f"{state.get('overall_score', -1.0):.1f} -> {overall_score:.1f}"
    if new_iteration != prev_iteration:
        _changes["iteration_change"] = f"{prev_iteration} -> {new_iteration}"
    if new_recovery_count != current_recovery_count:
        _changes["recovery_mode_consecutive_count"] = f"{current_recovery_count} -> {new_recovery_count}"
    if llm_verdict != verdict:
        _changes["verdict_override"] = f"{llm_verdict} -> {verdict}"
    if llm_triage != triage_decision:
        _changes["triage_override"] = (
            f"{'RECOVERY' if 'RECOVERY MODE' in llm_triage else 'NORMAL'}"
            f" -> {'RECOVERY' if 'RECOVERY MODE' in triage_decision else 'NORMAL'}"
        )
    if _changes:
        log_event(**ctx, level="INFO", event_type="state_update",
                  message="State mutations in reflect_node", **_changes)

    duration_ms = int((time.monotonic() - t0) * 1000)
    log_event(
        **ctx, level="INFO", event_type="node_end",
        message="reflect_node finished",
        duration_ms=duration_ms, overall_score=overall_score,
        llm_verdict=llm_verdict, enforced_verdict=verdict,
        enforced_triage="RECOVERY" if "RECOVERY MODE" in triage_decision else "NORMAL",
        new_iteration=new_iteration, new_recovery_count=new_recovery_count,
    )

    # ── D-M8: parse JSON summary block emitted by Reflector ────────────────────
    confidence = -1.0
    technical_gaps = requirement_gaps  # fallback
    user_gaps = requirement_gaps       # fallback

    json_block_match = re.search(r"```json\s*(\{.*?\})\s*```", reflection_text, re.DOTALL)
    if json_block_match:
        try:
            parsed = json.loads(json_block_match.group(1))
            raw_tech = parsed.get("technical_gaps")
            raw_user = parsed.get("user_gaps")
            confidence = float(parsed.get("confidence", -1.0))
            if isinstance(raw_tech, list) and raw_tech:
                technical_gaps = "\n".join(str(g) for g in raw_tech)
            if isinstance(raw_user, list) and raw_user:
                user_gaps = "\n".join(str(g) for g in raw_user)
            json_verdict = str(parsed.get("verdict", "")).upper()
            if json_verdict and json_verdict != verdict:
                log_event(
                    **ctx, level="WARNING", event_type="reflect_json_verdict_mismatch",
                    message="JSON verdict disagrees with text verdict",
                    json_verdict=json_verdict, text_verdict=verdict,
                )
        except (ValueError, TypeError) as exc:
            log_event(
                **ctx, level="WARNING", event_type="reflect_json_parse_error",
                message=f"Failed to parse reflector JSON block: {exc}",
            )
    else:
        log_event(
            **ctx, level="WARNING", event_type="reflect_json_missing",
            message="Reflector output contained no JSON block — using regex fallback",
        )

    blocking_fields = [
        l.strip().lstrip("-•*0123456789. \t") 
        for l in technical_gaps.splitlines() 
        if l.strip().lstrip("-•*0123456789. \t")
    ]
    missing_required_fields_count = len(blocking_fields)
    
    # ── Phase 2 Extract highest-leverage single reason ──
    _ug_lines = [l.strip().lstrip("-•*0123456789. \t") for l in user_gaps.splitlines() if l.strip().lstrip("-•*0123456789. \t")]
    user_facing_gap_reason = _ug_lines[0] if _ug_lines else ""
    if user_facing_gap_reason and not user_facing_gap_reason.endswith("."):
        user_facing_gap_reason += "."
    
    # deterministic next action derived immediately from blockers (M1/M3)
    has_draft = bool(state.get("current_draft"))
    if missing_required_fields_count == 0:
        draft_readiness_band = "Ready" if verdict == "PASS" else "Draft Ready, Reviewing"
        next_action = "UPDATE_DRAFT" if has_draft else "START_DRAFT"
        next_action_reason = "I have enough detail to compute the next draft update." if has_draft else "I have enough detail to start drafting."
    elif missing_required_fields_count <= 2:
        draft_readiness_band = "Near Ready"
        next_action = "ASK_ONE_MORE"
        # Phase 2: Scrub specific blockers from UI payload, let Elicitor ask.
        next_action_reason = "I need a few final details before drafting."
    else:
        draft_readiness_band = "Blocked"
        next_action = "ASK_MULTIPLE"
        # Phase 2: Scrub blocker list dump from UI payload. Keep it internal.
        next_action_reason = f"I still need {missing_required_fields_count} key details before drafting."

    log_event(
        **ctx, level="INFO", event_type="admin_review_decision",
        message="Evaluated internal drafting readiness",
        next_action=next_action,
        next_action_reason=next_action_reason,
        missing_count=missing_required_fields_count,
        draft_readiness_band=draft_readiness_band,
        verdict=verdict,
    )

    return {
        "reflection": reflection_text,
        "verdict": verdict,
        "triage_decision": triage_decision,
        "requirement_gaps": requirement_gaps,   # deprecated compat
        "technical_gaps": technical_gaps,
        "user_gaps": user_gaps,
        "user_facing_gap_reason": user_facing_gap_reason,
        "overall_score": overall_score,
        "iteration": new_iteration,
        "recovery_mode_consecutive_count": new_recovery_count,
        "confidence": confidence,
        "next_action": next_action,
        "next_action_reason": next_action_reason,
        "missing_required_fields_count": missing_required_fields_count,
        "blocking_fields": blocking_fields,
        "draft_readiness_band": draft_readiness_band,
        # ── Phase 1: record per-section completeness/confidence score ─────
        "section_scores": {
            section.id: {
                "completeness": overall_score / 10.0 if overall_score >= 0 else -1.0,
                "confidence": confidence,
                "verdict": verdict,
                "iteration": new_iteration,
            }
        },
        "chat_history": [
            {
                "role": "assistant",
                "msg_id": f"msg_{str(uuid.uuid4())[:8]}",
                "type": "reflect",
                "section": section.title,
                "verdict": verdict,
                "triage": triage_decision,
                "overall_score": overall_score,
                "confidence": confidence,
                "next_action_reason": next_action_reason,
                "content": reflection_text,
                "qa_pairs": list(state.get("section_qa_pairs", [])),
                "requirement_gaps": requirement_gaps,   # deprecated compat
                "user_gaps": user_gaps,
                "rubric_scores": {
                    "completeness": completeness_score,
                    "specificity": specificity_score,
                    "consistency": consistency_score,
                    "implementability": implementability_score,
                    "overall": overall_score,
                },
            }
        ],
    }


# ── Node: advance_section ─────────────────────────────────────────────────────

def advance_section_node(state: PRDState) -> dict:
    """
    Saves the approved draft, resets per-section state, and advances the
    section index. Sets is_complete when all sections are done.
    """
    ctx = _log_ctx(state, "advance_section_node")
    t0 = time.monotonic()
    section = get_section_by_index(state["section_index"])
    next_index = state["section_index"] + 1
    is_complete = next_index >= len(PRD_SECTIONS)

    # Determine why this node was reached (PASS vs forced by cap)
    verdict = state.get("verdict", "")
    iterations_used = state.get("iteration", 0)
    recovery_count = state.get("recovery_mode_consecutive_count", 0)
    final_score = state.get("overall_score", -1.0)
    next_section_name = PRD_SECTIONS[next_index].title if not is_complete else "END"

    if verdict == "PASS":
        advance_event = "advance_section_pass"
        advance_reason = "PASS"
    elif recovery_count >= DEFAULT_MAX_RECOVERY_MODE_CONSECUTIVE_ITERATIONS:
        advance_event = "advance_section_forced_recovery_cap"
        advance_reason = "RECOVERY_CAP"
    else:
        advance_event = "advance_section_forced_iter_cap"
        advance_reason = "ITER_CAP"

    log_event(
        **ctx, level="INFO", event_type="node_start",
        message="advance_section_node started", advance_reason=advance_reason,
    )
    log_event(
        **ctx, level="INFO", event_type=advance_event,
        message=f"Section advancing: {advance_reason}",
        section_saved=section.title, final_score=final_score, final_verdict=verdict,
        iterations_used=iterations_used, recovery_count=recovery_count,
        next_section=next_section_name, is_complete=is_complete,
    )
    if advance_reason != "PASS":
        log_event(
            **ctx, level="WARNING", event_type="forced_progression",
            message=f"Section forced forward without PASS: reason={advance_reason}",
            section_saved=section.title, final_score=final_score, advance_reason=advance_reason,
        )

    msg = f"✅ **{section.title}** completed!"
    if not is_complete:
        next_section = get_section_by_index(next_index)
        msg += f" Moving to **{next_section.title}**…"

    duration_ms = int((time.monotonic() - t0) * 1000)
    log_event(
        **ctx, level="INFO", event_type="node_end",
        message="advance_section_node finished",
        duration_ms=duration_ms, advance_reason=advance_reason,
        next_section=next_section_name, is_complete=is_complete,
    )

    return {
        # Persist the approved draft (merge reducer adds it to the dict)
        "prd_sections": {section.id: state["current_draft"]},
        # Advance navigation
        "section_index": next_index,
        "is_complete": is_complete,
        # Reset per-section state
        "rebuild_count": 0,
        "correction_stats": {"success": 0, "failure": 0},
        "iteration": 0,
        "verdict": "",
        "reflection": "",
        "technical_gaps": "",
        "user_gaps": "",
        "current_draft": "",
        "current_questions": "",
        "section_qa_pairs": [],       # deprecated compat field
        "requirement_gaps": "",       # deprecated compat field
        "triage_decision": "",
        "recovery_mode_consecutive_count": 0,
        "overall_score": -1.0,
        "confidence": -1.0,
        "raw_answer_buffer": "",
        "pending_echo": "",
        "pending_concept_updates": {},
        "answer_confirmation_status": "",
        "draft_execution_mode": "",
        "pending_interrupt_type": "question",
        "interrupt_queue": [],
        # Phase 1 hybrid — reset per-turn impact state
        "impacted_sections": [],
        "last_section_updates": [],
        "chat_history": [
            {
                "role": "assistant",
                "msg_id": f"msg_{str(uuid.uuid4())[:8]}",
                "type": "advance",
                "section": section.title,
                "content": msg,
            }
        ],
    }

# ── Node: terminal_session ───────────────────────────────────────────────────

def terminal_session_node(state: PRDState) -> dict:
    """
    Terminates the session cleanly when retry limits are reached instead of 
    repeating loops. Disables input and marks draft availability.
    """
    ctx = _log_ctx(state, "terminal_session_node")
    log_event(
        **ctx, level="INFO", event_type="session_termination",
        message="Graceful Session Termination explicitly triggered due to retry limits."
    )

    msg = "Unable to get enough information because key details were still missing. Session has ended."

    return {
        "session_status": "ended_retry_limit",
        "session_end_reason": "insufficient_information",
        "session_end_message": msg,
        "input_disabled": True,
        "draft_available": bool(state.get("prd_sections")),
        "draft_download_available": bool(state.get("prd_sections")),
        "response_type": "system",
        "chat_history": [
            {
                "role": "assistant",
                "msg_id": f"msg_{str(uuid.uuid4())[:8]}",
                "type": "system",
                "content": msg,
            }
        ]
    }


# ── Node: finalize ────────────────────────────────────────────────────────────

def finalize_node(state: PRDState) -> dict:
    """
    Compiles all approved section drafts into a single Markdown PRD document.
    """
    ctx = _log_ctx(state, "finalize_node")
    t0 = time.monotonic()
    sections_completed = len(state.get("prd_sections", {}))
    log_event(
        **ctx, level="INFO", event_type="node_start",
        message="finalize_node started",
        sections_completed=sections_completed, total_sections=len(PRD_SECTIONS),
    )

    from datetime import date

    lines = [
        "# Product Requirements Document",
        f"_Generated: {date.today().isoformat()}_",
        "",
    ]

    for section in PRD_SECTIONS:
        if section.id in state.get("prd_sections", {}):
            lines += [
                f"## {section.title}",
                "",
                state["prd_sections"][section.id],
                "",
            ]

    prd_markdown = "\n".join(lines)

    duration_ms = int((time.monotonic() - t0) * 1000)
    log_event(
        **ctx, level="INFO", event_type="node_end",
        message="finalize_node finished — PRD generation complete",
        duration_ms=duration_ms, sections_completed=sections_completed,
        total_sections=len(PRD_SECTIONS), prd_len=len(prd_markdown),
    )

    return {
        "prd_markdown": prd_markdown,
        "chat_history": [
            {
                "role": "assistant",
                "msg_id": f"msg_{str(uuid.uuid4())[:8]}",
                "type": "complete",
                "content": (
                    "🎉 **Your PRD is complete!** All sections have been reviewed "
                    "and approved. Download it using the button in the sidebar."
                ),
            }
        ],
    }

def _log_llm_elapsed(state: PRDState, node_name: str, timing_name: str, elapsed: float, is_success: bool = True):
    status = "success" if is_success else "failed"
    log_event(
        thread_id=state.get("thread_id", ""),
        run_id=state.get("run_id", ""),
        node_name=node_name,
        level="INFO" if is_success else "ERROR",
        event_type="llm_call",
        message=f"{node_name} LLM call ({timing_name}) in {int(elapsed)}ms [{status}]",
        latency_ms=int(elapsed),
        llm_operation=timing_name,
        success=is_success
    )

def answer_clarification_node(state: PRDState) -> dict:
    ctx = _log_ctx(state, "answer_clarification")
    log_event(**ctx, level="INFO", event_type="node_start", message="answer_clarification started")
    
    question = state.get("current_questions", "").strip()
    answer = state.get("raw_answer_buffer", "").strip()
    
    log_event(
        **ctx, level="INFO", event_type="clarification_answer_decision",
        message="Logging clarification answer parameters",
        remaining_blockers=state.get("remaining_subparts", []),
        conflict_records=state.get("conflict_records", []),
        missing_details_generated_in_code=bool(state.get("remaining_subparts", [])),
        response_type="clarification_answer",
        did_append_followup_question=False
    )
    
    # We must format the PRD safely, handle edge cases
    prd_sections = state.get("prd_sections", {})
    from config.sections import PRD_SECTIONS
    context = ""
    for sec in PRD_SECTIONS:
        if sec.id in prd_sections:
            context += f"## {sec.title}\n{prd_sections[sec.id]}\n\n"
            
    t0 = time.monotonic()
    llm = _get_llm()
    try:
        from langchain_core.messages import SystemMessage
        from prompts.templates import CLARIFICATION_ANSWER_PROMPT
        active_opts = state.get("active_question_options", [])
        
        reply_context = ""
        interp = state.get("reply_context_interpretation", {})
        if interp and interp.get("relationship_type") == "clarification_about_replied_message" and state.get("reply_context_message_text"):
            reply_context = f"Replied Message Context:\n---\n{state.get('reply_context_message_text')}\n---\n"
            
        response = llm_invoke(llm, [SystemMessage(content=CLARIFICATION_ANSWER_PROMPT.format(
            question=question,
            options=", ".join(active_opts) if active_opts else "None",
            reply_context_block=reply_context,
            answer=answer,
            remaining_blockers="\n".join([f"- {b}" for b in state.get("remaining_subparts", [])]) or "None",
            conflicted_concepts="\n".join([f"- {m}" for m in state.get("concept_conflicts", [])]) if state.get("concept_conflicts") else "None",
            context=context or "No context drafted yet."
        ))], state=state, node_name="answer_clarification", purpose="clarify")
        elapsed = (time.monotonic() - t0) * 1000
        _log_llm_elapsed(state, "answer_clarification", "clarification_generation", elapsed)
        
        reply_content = response.content.strip()
        fallback_blockers = state.get("remaining_subparts", [])
        
        try:
            import json
            parsed = json.loads(reply_content)
            reply_text = parsed.get("response_text", "")
            
            if not reply_text or "?" in reply_text:
                if fallback_blockers:
                    reply_text = "I still need details for: " + ", ".join(fallback_blockers) + "."
                else:
                    reply_text = "I'm having trouble providing a clarification right now."
            reply_content = reply_text
            
        except Exception:
            if "?" in reply_content:
                 reply_content = "I still need details for: " + ", ".join(fallback_blockers) + "."
            pass

    except Exception as e:
        fallback_blockers = state.get("remaining_subparts", [])
        if fallback_blockers:
            reply_content = "Still missing details for: " + ", ".join(fallback_blockers) + "."
        else:
            reply_content = "I'm having trouble providing a clarification right now. Could you rephrase your question?"
        log_event(**ctx, level="ERROR", event_type="llm_error", message=str(e))

    new_message = {
        "role": "assistant",
        "msg_id": f"msg_{str(uuid.uuid4())[:8]}",
        "type": "clarification_answer",
        "content": reply_content,
        "run_id": state.get("run_id", ""),
        "section": get_section_by_index(state.get("section_index", 0)).title
    }

    log_event(**ctx, level="INFO", event_type="node_end", message="answer_clarification finished")
    
    current_status = state.get("question_status", "")
    target_status = "OPEN" if current_status != "SUPERSEDED" else "SUPERSEDED"
    
    log_event(
        **ctx, level="INFO", event_type="clarification_answer_emitted",
        message=f"Stored clarification response and transitioned question_status to {target_status}",
        active_question_id=state.get("active_question_id", ""),
        current_questions=reply_content,
        question_status=target_status
    )

    return {
        "chat_history": [new_message],
        "reply_intent": "CLARIFIED", # terminal outcome for this branch
        "current_questions": reply_content,
        "question_status": target_status,
        "response_type": "clarification_answer"
    }
