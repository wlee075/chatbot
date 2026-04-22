import operator
from typing import Annotated, Any, Literal, TypedDict, Optional


class QuestionObject(TypedDict):
    question_id: str
    question_text: str
    subparts: list[str]


from enum import Enum

class ConceptStatus(str, Enum):
    MENTIONED = "mentioned"
    CURRENT = "current"
    HISTORICAL = "historical"
    NEGATED = "negated"
    EXAMPLE_ONLY = "example_only"
    SUPERSEDED = "superseded"
    CONFLICTED = "conflicted"

class ActionGraphEdge(TypedDict):
    verb: str
    object: str
    destination_if_any: str | None
    confidence: float
    source_span: tuple[int, int]
    extraction_method: Literal["dependency_parse", "phrase_proximity_fallback"]

class SemanticCandidate(TypedDict):
    surface: str
    normalized: str
    type: str  # ExtractionCandidateType
    confidence: float
    source_span: tuple[int, int]
    is_negated: bool
    is_historical: bool
    is_example: bool

class MessageSemantics(TypedDict):
    message_id: str
    timestamp_utc: str
    raw_text: str
    candidates: list[SemanticCandidate]
    action_graph: list[ActionGraphEdge]

class ConceptHistoryEntry(TypedDict):
    concept_key: str
    mentions: list[str]
    source_message_ids: list[str]
    status: ConceptStatus
    status_reason: str
    is_current: bool
    is_negated: bool
    is_historical: bool
    is_example: bool
    was_corrected: bool
    superseded_by: str | None
    corrected_from: str | None
    last_seen_at: str
    last_transition_at: str


class ConceptRecord(TypedDict):
    concept_key: str
    surface: str
    scope_type: str | None
    scope_value: str | None
    confidence: float
    status_reason: str
    source_message_ids: list[str]


class BlockerRecord(TypedDict):
    blocker_type: str
    target: str
    reason: str
    severity: Literal["hard", "advisory_warning"]
    source: str      # e.g., "section_gap"
    suggested_question_type: str


class CorrectionRecord(TypedDict):
    old_concept: str
    new_concept: str
    reason: str
    source_message_id: str
    timestamp_utc: int
    trigger_type: str


class ActionCandidateRecord(TypedDict):
    verb: str
    object: str
    destination: str | None
    confidence: float
    extraction_method: str
    is_complete: bool
    missing_parts: list[str]


class DraftReadinessDict(TypedDict):
    is_ready: bool
    hard_blockers: list[str]
    advisory_warnings: list[str]


class ConversationUnderstandingOutput(TypedDict):
    current_concepts: list[ConceptRecord]
    historical_concepts: list[ConceptRecord]
    negated_concepts: list[ConceptRecord]
    example_only_concepts: list[ConceptRecord]
    future_or_planned_concepts: list[ConceptRecord]
    conflicted_concepts: list[ConceptRecord]
    unresolved_blockers: list[BlockerRecord]
    draft_readiness: DraftReadinessDict
    corrections_recently_applied: list[CorrectionRecord]
    action_candidates_if_any: list[ActionCandidateRecord]


class ReplyContextInterpretation(TypedDict):
    reply_context_present: bool
    relationship_type: Literal[
        "direct_answer_to_replied_message",
        "clarification_about_replied_message",
        "correction_or_disagreement_with_replied_message",
        "supporting_context_only",
        ""
    ]
    confidence: float
    reason: str


class TargetedContext(TypedDict):
    target_type: Literal["latest_question", "replied_message"]
    target_message_id: Optional[str]
    target_text: str
    relationship_type: str
    confidence: float


class SecondaryContext(TypedDict):
    target_available: bool
    message_id: Optional[str]
    text: Optional[str]

class UploadedFileParams(TypedDict):
    """Input payload from client upload."""
    file_id: str
    filename: str
    mime_type: str
    size_bytes: int

class BackgroundContext(TypedDict):
    context_id: str             # Stable unique ID (uuid)
    image_file_id: str          # ID of the original uploaded file for thumbnail mapping
    source_turn_id: str         # Binds to the msg_id of the user turn containing the upload
    created_at: str             # ISO timestamp
    updated_at: str             # ISO timestamp
    generated_summary: str      # Pure machine-generated text
    edited_summary: str | None  # Optional user override
    is_active: bool             # Status flag for prompt injection

def _merge_background_contexts(existing: list[BackgroundContext] | None, new: list[BackgroundContext] | None) -> list[BackgroundContext]:
    existing = existing or []
    new = new or []
    if not existing:
        return new
    result = {item["context_id"]: item for item in existing}
    for item in new:
        result[item["context_id"]] = item
    return list(result.values())

class AcceptedFile(TypedDict):
    """Normalized accepted file format."""
    file_id: str
    filename: str
    file_type: Literal["jpg", "png", "pdf"]

class RejectedFile(TypedDict):
    """Reasoning block for rejected uploads."""
    filename: str
    reason: Literal["no_files_uploaded", "unsupported_file_type", "malformed_file_payload", "missing_required_metadata", "empty_file"]

class DescribedImage(TypedDict):
    """Normalized image description output."""
    file_id: str
    filename: str
    high_level_description: str
    visible_elements: list[str]
    uncertainties: list[str]
    


RepairInstruction = Literal["", "DUPLICATE_SUPPRESSED", "REPHRASE_REQUIRED", "CLARIFY_TARGET"]


def _merge_recent_questions(old: list[str], new: list[str] | str) -> list[str]:
    res = list(old) if old else []
    if not isinstance(new, list):
        new = [new]
    for q in new:
        if q and q not in res:
            res.append(q)
    while len(res) > 3:
        res.pop(0)
    return res


def _merge_dicts(a: dict, b: dict) -> dict:
    """Reducer: merges dict updates rather than replacing the entire dict."""
    return {**a, **b}


class PRDState(TypedDict):
    # ── Session identity ─────────────────────────────────────────────────────
    thread_id: str   # stable session identifier (set once per Streamlit session)
    run_id: str      # one graph invocation — UUID generated per .invoke() call

    # ── Terminal Session State ───────────────────────────────────────────────
    session_status: str
    session_end_reason: str
    session_end_message: str
    input_disabled: bool
    draft_available: bool
    draft_download_available: bool

    validation_flag: str
    validation_reason: str
    pending_numeric_clarification: bool
    parent_question_id: str
    repair_question_id: str

    # ── Static configuration ─────────────────────────────────────────────────
    context_doc: str        # raw text of the optional uploaded document (kept for compat)
    max_iterations: int     # max reflection loops per section (default: 3)

    # ── Session phase (D-M12) ─────────────────────────────────────────────────
    # "discovery"   — probing PATH_2/3 users before PRD work starts (max 3 turns)
    # "elicitation" — normal PRD section Q&A
    phase: str
    framing_mode: str       # "clear" | "symptom_only" | "confused" — set by detect_framing
    discovery_turn_count: int  # number of discovery turns used; does not count against iteration

    # ── Section navigation ────────────────────────────────────────────────────
    section_index: int      # index into PRD_SECTIONS list
    iteration: int          # current reflection iteration for this section

    # ── Current section workflow ──────────────────────────────────────────────
    current_questions: str  # questions generated by Elicitor
    term_provenance: dict[str, list[dict]] # maps "term" to list of origin payload dicts
    current_draft: str      # draft produced by Drafter
    verdict: str            # "PASS" | "REWORK" | ""
    triage_decision: str    # "TRIAGE: ENTER RECOVERY MODE" | "TRIAGE: NORMAL ITERATION"
    recovery_mode_consecutive_count: int  # consecutive ENTER RECOVERY MODE verdicts
    overall_score: float    # parsed OVERALL SCORE from reflector (-1.0 = not parsed)

    # ── UX Deterministic Routing (Decision Transparency) ──────────────────────
    next_action: str             # "START_DRAFT" | "ASK_ONE_MORE" | "ASK_MULTIPLE" | "UPDATE_DRAFT" | "ADVANCE_SECTION" | "WAITING_CONFIRMATION"
    next_action_reason: str      # concise 1-line rationale for user AI helper text
    missing_required_fields_count: int # 0 means drafting block cleared
    blocking_fields: list        # names of drafting blockers
    draft_readiness_band: str    # internal band name (e.g., "Ready", "Near Ready", "Blocked")

    # ── Reflector outputs (v2: JSON schema) ───────────────────────────────────
    # technical_gaps: internal only — fed to Elicitor for follow-up questions
    # user_gaps:      plain English — shown to user in feedback panel
    # reflection:     raw reflector JSON string (kept for logging/debug)
    reflection: str
    technical_gaps: str     # internal, never shown to user
    user_gaps: str          # plain English, shown to user
    requirement_gaps: str   # DEPRECATED: kept for backward compat during migration

    # ── Confirmed answers store (D-M5) ────────────────────────────────────────
    # Source of truth for all confirmed user answers.
    # Keys are canonical concept keys (e.g. "team_size", "problem_statement").
    # Each value: {
    #   "fact_id": str (UUID),
    #   "answer": str,
    #   "section": str,
    #   "section_id": str,
    #   "contradiction_flagged": bool,
    #   "version": int (turn-level version of the store when this was written)
    # }
    # Uses merge reducer so individual concepts can be updated without full replace.
    confirmed_qa_store: Annotated[dict, _merge_dicts]
    store_version: int      # incremented on every turn with a canonical write
    rebuild_count: int      # total number of state reconciliation events

    # ── Semantic concept history (Phase 4) ───────────────────────────────────
    # Normalized track of concepts over time, distinct from confirmed_qa_store.
    # Maps concept_key -> ConceptHistoryEntry
    concept_history: Annotated[dict, _merge_dicts]

    # ── section_qa_pairs (DEPRECATED as source of truth) ─────────────────────
    # Kept as a derived view written simultaneously with confirmed_qa_store.
    # Reflector reads this for backward compat until Reflector is fully migrated.
    # Format: [{"questions": str, "answer": str, "section": str}]
    section_qa_pairs: list

    # ── Interrupt routing (D-M6) ──────────────────────────────────────────────
    # Set before every interrupt(). Routing edge reads on resume to dispatch correctly.
    # "question"     — user answering an Elicitor question
    # "bounds_check" — user confirming an implausible number (rule-based, no LLM)
    # ""             — no interrupt pending
    pending_interrupt_type: str
    # Queue for secondary interrupts when two triggers fire on the same answer.
    # Each item: {"type": str, "payload": dict}
    interrupt_queue: list

    # ── Image context (D-M3) ─────────────────────────────────────────────────
    # Gemini Vision text descriptions of uploaded images.
    # Injected into Elicitor context block when non-empty.
    # Capped at 3 most recent descriptions at injection time.
    image_context: Annotated[list, operator.add]

    # ── Forward hints (D-M13) ────────────────────────────────────────────────
    # User-volunteered facts about future PRD sections captured early.
    # Surfaced as confirmation reminders when that section is reached.
    # NOT written to confirmed_qa_store until user re-confirms in context.
    # Each item: {"section_id": str, "hint": str}
    forward_hints: Annotated[list, operator.add]

    # ── Contradiction log (O-1b) ──────────────────────────────────────────────
    # Observability log of detected contradictions this session.
    # Each item: {"concept_key": str, "prior": str, "new": str, "section": str}
    # Used to append reviewer note to final PRD if any contradiction_flagged remain.
    contradiction_log: Annotated[list, operator.add]

    # ── TBD fields (D-M12 zero-context fallback) ─────────────────────────────
    # Concept keys that could not be captured (user consistently unable to answer).
    # ── Split Node Payloads (D-M14) ─────────────────────────────────────────────
    # Fields strictly used for passing parameters explicitly between Intent Classifier ->
    # Semantic Assessor -> Contradiction Validator -> Truth Commit instead of all in one node.
    interpreted_answer: str
    echo_text: str
    subpart_evidence_candidates: list
    resolved_subparts: list
    snippets_by_subpart: dict
    matched_option: str
    has_conflicts: bool
    conflict_records: list
    current_concepts: list
    is_eligible: bool
    eligibility_reason: str
    clarification_route_id: str
    
    rephrased_question_text: str
    narrowed_question_spec: str
    numeric_validation_error_message: str
    tbd_fields: list

    # ── Accumulated PRD content ───────────────────────────────────────────────
    # Uses merge reducer so nodes can write one section at a time.
    prd_sections: Annotated[dict, _merge_dicts]

    # ── UI chat history (append-only) ─────────────────────────────────────────
    # Display only — never used as source of truth for answer content.
    # Each item: {"role": str, "type": str, "content": str, ...extra}
    chat_history: Annotated[list, operator.add]

    # ── Reflector confidence (Step 6 D-M8 JSON output) ──────────────────────
    confidence: float    # 0.0–1.0 from JSON block; -1.0 = not parsed

    # ── Provisional answer state (confirmation gate) ───────────────────────
    # Answers are provisional until explicitly confirmed by the user.
    # confirmed_qa_store must only receive CONFIRMED values.
    raw_answer_buffer: str       # latest unconfirmed raw user response
    effective_answer_for_commit: str      # commit-ready representation (raw or image-derived)
    answer_provenance: Literal["user_text", "image_derived"] 
    materialization_status: Literal["user_text_passthrough", "image_bound", "image_missing", "multi_file_unsupported", ""]
    matched_context_id: str | None
    materialization_conflict: bool
    materialization_conflict_reason: str | None
    current_question_object: QuestionObject  # structured question from Elicitor
    document_summaries: dict[str, str]  # dict mapping semantic group to text
    
    # ── File Upload Intake State ──
    uploaded_files: list[UploadedFileParams]
    upload_status: Literal["accepted", "accepted_partial", "rejected"]
    accepted_files: list[AcceptedFile]
    rejected_files: list[RejectedFile]
    downstream_analysis_allowed: bool

    # ── Uploaded Image Description State ──
    image_description_status: Literal["described", "no_accepted_images", "failed", ""]
    described_images: list[DescribedImage]
    needs_followup: bool

    # ── Image Description Session Context State ──
    background_generated_contexts: Annotated[list[BackgroundContext], _merge_background_contexts]

    # ── Elicitation ──clarification loop tracking ───────────────────────────────────
    active_question_id: str
    active_question_type: str
    active_question_options: list[str]
    resolved_option_id: str
    question_status: str             # "OPEN" | "ANSWERED" | "SUPERSEDED"
    answered_at: str
    recent_questions: Annotated[list[str], _merge_recent_questions]
    
    # ── Phase 2 UX Tone Tracking ───────────────────────────────────────────────
    user_facing_gap_reason: str
    single_next_question: str

    reply_intent: str
    repair_instruction: str    # Guidance passed to Elicitor if a repair hit
    
    # Track terminal state for routing and UI evaluation rendering exclusively
    response_type: str

    
    # ── Phase 5 Reply-to-Older-Message Context Inference (D-M15) ─────────────
    reply_context_message_id: str
    reply_context_message_text: str
    reply_context_interpretation: ReplyContextInterpretation
    active_semantic_target: TargetedContext
    secondary_semantic_context: SecondaryContext
    context_route_hint: Literal["normal_answer", "clarification_target", "no_override"]
    pending_echo: str            # system restatement awaiting user confirmation
    pending_concept_updates: dict  # candidate Q&A not yet promoted to canonical truth
    answer_confirmation_status: str  # "" | "PENDING" | "CONFIRMED" | "CORRECTED"

    # ── Structured event payload (message tagging, D-M14) ─────────────────
    # Set by await_answer_node when user submits a structured tagged event.
    # Cleared by handle_tagged_event_node or the semantic evaluation pipeline.
    # Fields: {event_type, content, target_message_id, target_content, ...optional}
    pending_event: dict

    # ── Event history (provenance log) ────────────────────────────────────
    # Append-only log of processed TAG_MESSAGE_AS_TRUTH and CORRECT_MESSAGE events.
    # Each item: {event_type, target_message_id, content, section, concept_key}
    event_history: Annotated[list, operator.add]
    correction_stats: Annotated[dict, _merge_dicts]

    # ── Hybrid opportunistic updater (Phase 1) ────────────────────────────
    # section_scores: per-section completeness/confidence written by reflect_node.
    #   {section_id: {"completeness": float, "confidence": float, "verdict": str}}
    #   Uses merge reducer so individual sections can be updated independently.
    section_scores: Annotated[dict, _merge_dicts]

    # ── Draft output cache (performance optimization) ─────────────────────
    # Keyed {section_id: {cache_key_hash: draft_text}}.
    # Cache key is SHA-256 of section-relevant Q&A + dependency-scoped PRD context.
    # Only the latest key per section is retained to keep size bounded.
    draft_cache: Annotated[dict, _merge_dicts]

    # Section-level draft policy state.
    # {section_id: {
    #   "facts": list[str],
    #   "confidence_score": float,
    #   "completeness_score": float,
    #   "last_draft_hash": str,
    #   "state": str,
    #   ...internal bookkeeping
    # }}
    section_draft_meta: Annotated[dict, _merge_dicts]

    # Result of the most recent draft node for routing.
    # "drafted" → continue to reflect, "skipped" → go back to question generation.
    draft_execution_mode: str

    # Impact confidence scores set by detect_impact_node.
    # {section_id: float} — 1.0 = rule-based, 0.7 = LLM-fallback, 0.4 = side-fact.
    # Side-writes below _SIDE_WRITE_MIN_IMPACT_SCORE are skipped in draft_node.
    impacted_section_scores: Annotated[dict, _merge_dicts]

    # Memoised formatted PRD context — avoids re-serialising prd_sections each node.
    _prd_sections_fmt_hash: str   # sha256 of prd_sections used in last format call
    _formatted_prd_so_far: str    # last output of _format_prd_so_far

    # impacted_sections: set by detect_impact_node each turn.
    #   List of already-drafted section IDs that should be re-drafted this turn
    #   (excludes current section; capped at MAX_SIDE_WRITES=2).
    impacted_sections: list

    # last_section_updates: section IDs written in the most recent draft_node call.
    #   Overwritten each turn (not append-reduced). Used by UI for badges.
    last_section_updates: list

    # ── Final output ──────────────────────────────────────────────────────────
    prd_markdown: str
    is_complete: bool
