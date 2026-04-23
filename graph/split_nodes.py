import time
import re
import datetime
import uuid
import logging
from typing import Optional
from graph.state import PRDState, ConceptStatus
from graph.nodes import (
    _log_ctx, log_event, _get_llm, _classify_intent_rule, get_section_by_index, 
    _word_tokenize, _sent_tokenize, _STEMMER, _STOPWORDS_EN, 
    PRD_SECTIONS, build_conversation_understanding_output,
    log_canonical_write, log_integrity_failure, IntegrityValidator, _log_semantic_transition
)
from utils.adjudicator import invoke_llm_adjudicator

_NEG_POLARITY = frozenset({"not", "no", "never", "without"})
_POS_POLARITY = frozenset({"fine", "correct", "fixed", "resolved"})
_POLARITY_MARKERS = _NEG_POLARITY | _POS_POLARITY
_REVERSAL_MARKERS = ("used to", "not anymore", "no longer", "changed to")

def _tokenize_norm(text: str) -> set[str]:
    def _pre(t: str) -> str:
        if len(t) > 2 and t[-1] == "s" and t[:-1] == t[:-1].upper() and t[:-1].isalpha():
            t = t[:-1]
        return t.lower()
    toks = _word_tokenize(text)
    if _STEMMER is not None:
        return {_STEMMER.stem(_pre(t)) for t in toks if t.replace("'", "").isalpha() and _pre(t) not in _STOPWORDS_EN and len(t) > 1}
    return {_pre(t) for t in toks if t.replace("'", "").isalpha() and _pre(t) not in _STOPWORDS_EN and len(t) > 1}

def _polarity_near(clause: str, matched: set[str]) -> bool:
    words = _word_tokenize(clause.lower())
    stemmed = [(_STEMMER.stem(w) if _STEMMER else w) for w in words]
    for i, s in enumerate(stemmed):
        if s in matched:
            window = words[max(0, i - 3):i + 4]
            if any(p in window for p in _POLARITY_MARKERS):
                return True
    return False

def _has_reversal(text: str) -> bool:
    tl = text.lower()
    return any(r in tl for r in _REVERSAL_MARKERS)


def answer_validity_node(state: PRDState) -> dict:
    """Pre-commit response quality gate.

    Runs immediately after await_answer and before numeric_validation.
    Prevents accidental keystrokes (e.g. 'ñ', '.', 'aaaa') from advancing
    section state or being echoed as confirmed facts.

    Decision:
      PASSED  → no-op; downstream nodes proceed normally
      REJECTED → gentle clarification emitted; raw_answer_buffer cleared;
                 downstream numeric_validation / intent_classifier skipped
                 via route_after_answer_validity router.
    """
    import uuid as _uuid
    from utils.answer_guardrail import check_answer_quality

    ctx = _log_ctx(state, "answer_validity")
    raw_answer = state.get("raw_answer_buffer", "").strip()
    question   = state.get("current_questions", "").strip()

    # Image-only submissions are always valid (no text required)
    if not raw_answer and state.get("uploaded_files"):
        log_event(**ctx, level="INFO", event_type="answer_guardrail_passed",
                  message="Image-only submission — guardrail bypassed", reason="image_only")
        return {"answer_guardrail_status": "PASSED"}

    result = check_answer_quality(raw_answer, question)

    if result.passed:
        log_event(**ctx, level="INFO", event_type="answer_guardrail_passed",
                  message="Answer quality check passed",
                  reason=result.reason, score=result.score,
                  input_length=len(raw_answer))
        return {"answer_guardrail_status": "PASSED"}

    # ── Reject path ──────────────────────────────────────────────────────────
    log_event(**ctx, level="WARNING", event_type="answer_guardrail_triggered",
              message="Answer quality check REJECTED input — clarification prompted",
              reason=result.reason, score=result.score,
              input_length=len(raw_answer),
              **{f"signal_{k}": v for k, v in result.signals.items()})

    section_index = state.get("section_index", 0)
    try:
        section_title = get_section_by_index(section_index).title
    except Exception:
        section_title = ""

    clarification_msg = {
        "role":     "assistant",
        "msg_id":   f"msg_{str(_uuid.uuid4())[:8]}",
        "type":     "answer_validity_clarification",
        "content":  result.clarification_prompt,
        "run_id":   state.get("run_id", ""),
        "section":  section_title,
    }

    return {
        "answer_guardrail_status": "REJECTED",
        "answer_guardrail_reason": result.reason,
        # Clear the buffer so echo_generation / truth_commit cannot touch it
        "raw_answer_buffer": "",
        "chat_history": [clarification_msg],
    }


def numeric_validation_node(state: PRDState) -> dict:
    """1a. Numeric Validation Node"""
    raw_answer = state.get("raw_answer_buffer", "").strip()
    from utils.validator import _check_numeric_plausibility
    flag, reason = _check_numeric_plausibility(raw_answer)
    if flag:
        return {
            "validation_flag": flag,
            "validation_reason": reason,
            "pending_numeric_clarification": True,
            "question_status": "OPEN",
            "reply_intent": "NUMERIC_ERROR"
        }
    return {}


def intent_classifier_node(state: PRDState) -> dict:
    """1. Intent Classifier Node"""
    ctx = _log_ctx(state, "intent_classifier")
    raw_answer = state.get("raw_answer_buffer", "").strip()
    
    import logging, re
    metrics_logger = logging.getLogger("orchestrator_metrics")
    metrics_logger.info("first_consumer_of_input", extra={
        "event_type": "first_consumer_of_input",
        "turn_id": state.get("run_id", "unknown"),
        "node_name": "intent_classifier",
        "input_length": len(raw_answer),
        "looks_like_replayed_conversation": bool(re.search(r"(?i)\buser:.*\bassistant:", raw_answer)) if raw_answer else False
    })
    
    log_event(**ctx, level="INFO", event_type="node_start", message="intent_classifier started")
    
    question = state.get("current_questions", "").strip()
    raw_answer = state.get("raw_answer_buffer", "").strip()

    t0 = time.monotonic()
    
    current_q_obj = state.get("current_question_object", {})
    subparts = current_q_obj.get("subparts", [])
    remaining_subparts = list(state.get("remaining_subparts", subparts))
    last_assistant = ""
    for m in reversed(state.get("chat_history", [])):
        if m.get("role") == "assistant":
            last_assistant = m.get("content", "")
            break
            
    # IM1/IM3: Image-only bypass
    if not raw_answer and state.get("uploaded_files"):
        log_event(**ctx, level="INFO", event_type="intent_fallback_image_only", message="Bypassing intent classifier for image-only submission")
        return {"reply_intent": "ANSWER"}

    log_event(
        **ctx, level="INFO", event_type="intent_classifier_input", message="Capturing classifier inputs",
        latest_user_message=raw_answer,
        latest_assistant_message=last_assistant,
        active_question_text=question,
        active_blocker=remaining_subparts[0] if remaining_subparts else "",
        remaining_blockers_summary=",".join(remaining_subparts),
        current_response_mode=state.get("response_mode", "")
    )

    intent, _, classifier_source, interpretation = _classify_intent_rule(question, raw_answer, llm=_get_llm(), state=state)
    duration_ms = int((time.monotonic() - t0) * 1000)
    
    log_event(
        **ctx, level="INFO", event_type="intent_classifier_result", 
        message=f"Intent classified as {intent} via {classifier_source}", 
        primary_intent=intent, 
        secondary_intent="",
        classifier_source=classifier_source,
        regex_matches=True if classifier_source=="FAST_REGEX" else False,
        confidence=1.0 if classifier_source=="FAST_REGEX" else 0.8,
        reason="Matched explicit intent rule or LLM fallback",
        duration_ms=duration_ms
    )

    res = {"reply_intent": intent}
    if interpretation:
        res["reply_context_interpretation"] = interpretation
    return res

MIN_SUPPORTING_CONTEXT_CONFIDENCE = 0.5

def target_context_selector_node(state: PRDState) -> dict:
    """1a. Target Context Selector Node"""
    ctx = _log_ctx(state, "target_context_selector")
    log_event(**ctx, level="INFO", event_type="node_start", message="target_context_selector started")
    
    interp = state.get("reply_context_interpretation", {})
    replied_text = state.get("reply_context_message_text", "").strip()
    replied_id = state.get("reply_context_message_id")
    reply_intent = state.get("reply_intent", "")
    
    active_target = {
        "target_type": "latest_question",
        "target_message_id": None,
        "target_text": state.get("current_questions", "").strip() or "",
        "relationship_type": "",
        "confidence": 0.0
    }
    secondary_context = {
        "target_available": False,
        "message_id": None,
        "text": None
    }
    context_route_hint = "normal_answer"
    
    if interp and interp.get("reply_context_present"):
        rel_type = interp.get("relationship_type", "")
        conf = interp.get("confidence", 0.0)
        
        if not replied_text and rel_type in ("correction_or_disagreement_with_replied_message", "direct_answer_to_replied_message", "clarification_about_replied_message", "supporting_context_only"):
            log_event(**ctx, level="WARNING", event_type="target_context_selector_missing_text", message=f"Replied text missing for relationship {rel_type}, falling back to latest question.")
        else:
            if rel_type in ("direct_answer_to_replied_message", "correction_or_disagreement_with_replied_message"):
                active_target.update({
                    "target_type": "replied_message",
                    "target_message_id": replied_id,
                    "target_text": replied_text,
                    "relationship_type": rel_type,
                    "confidence": conf
                })
            elif rel_type == "clarification_about_replied_message":
                context_route_hint = "clarification_target"
                active_target.update({
                    "target_type": "replied_message",
                    "target_message_id": replied_id,
                    "target_text": replied_text,
                    "relationship_type": rel_type,
                    "confidence": conf
                })
            elif rel_type == "supporting_context_only":
                if conf >= MIN_SUPPORTING_CONTEXT_CONFIDENCE:
                    secondary_context.update({
                        "target_available": True,
                        "message_id": replied_id,
                        "text": replied_text
                    })
                    
    if reply_intent == "DIRECT_ANSWER" and context_route_hint == "clarification_target":
        log_event(**ctx, level="INFO", event_type="target_context_conflict_override", message="Relationship type overrode general intent to force clarification routing.")

    log_event(**ctx, level="INFO", event_type="target_context_selector_output", message="Emitted target context overrides", 
        active_target_type=active_target["target_type"],
        has_secondary=secondary_context["target_available"],
        route_hint=context_route_hint)

    return {
        "active_semantic_target": active_target,
        "secondary_semantic_context": secondary_context,
        "context_route_hint": context_route_hint
    }

def clarification_router_node(state: PRDState) -> dict:
    """1b. Clarification Router Node"""
    ctx = _log_ctx(state, "clarification_router")
    log_event(**ctx, level="INFO", event_type="node_start", message="clarification_router started")
    
    reply_intent = state.get("reply_intent", "")
    context_route_hint = state.get("context_route_hint", "normal_answer")
    
    route = "option_resolution"
    fallback_state = "proceeding to normal extraction"
    
    if context_route_hint == "clarification_target" or reply_intent in ("DIRECT_CLARIFICATION_QUESTION", "UNCLEAR_META"):
        route = "answer_clarification"
        fallback_state = "diverting to clarify user question"
    elif reply_intent in ("REPHRASE_REQUEST", "AMBIGUOUS", "REPETITION_COMPLAINT", "COMPLAINT_OR_META"):
        route = "repair_mode"
        fallback_state = "diverting to repair state"
    elif reply_intent == "NUMERIC_ERROR":
        route = "handle_numeric_error"
        fallback_state = "diverting to numeric error handler"
        
    log_event(**ctx, level="INFO", event_type="clarification_route_selected", message=f"Routed to {route}", route_selected=route, fallback_state=fallback_state)
    
    return {
        "clarification_route_id": route
    }

def repair_mode_node(state: PRDState) -> dict:
    """1b. Repair Mode Node"""
    ctx = _log_ctx(state, "repair_mode")
    log_event(**ctx, level="INFO", event_type="node_start", message="repair_mode started")
    
    intent = state.get("reply_intent", "")
    raw_answer = state.get("raw_answer_buffer", "").strip()
    
    if intent == 'REPETITION_COMPLAINT':
        repair_instruction = 'REPETITION_COMPLAINT'
    elif intent == 'REPHRASE_REQUEST':
        repair_instruction = 'REPHRASE_REQUEST'
    else:
        is_repetition = ("repeat" in raw_answer.lower() or "again" in raw_answer.lower() or "same" in raw_answer.lower() or "already" in raw_answer.lower())
        repair_instruction = "DUPLICATE_SUPPRESSED" if (intent == 'COMPLAINT_OR_META' and is_repetition) else "REPHRASE_REQUIRED"
        
    res = {
        "repair_instruction": repair_instruction,
        "active_question_id": "",
        "pending_echo": "",
        "raw_answer_buffer": ""
    }
    
    log_event(**ctx, level="INFO", event_type="repair_mode_evaluated", message="Repair mode evaluated", repair_instruction=repair_instruction)
    
    return res


def option_resolution_node(state: PRDState) -> dict:
    """1b. Option Resolution Node"""
    raw_answer = state.get("raw_answer_buffer", "").strip()
    active_q_type = state.get("active_question_type", "OPEN_ENDED")
    active_options = state.get("active_question_options", [])
    q_status = state.get("question_status", "")
    
    if active_q_type == "BINARY_CLARIFICATION" and q_status == "OPEN" and active_options:
        ans_norm = set([w for w in re.split(r'\W+', raw_answer.lower()) if len(w) > 2])
        best_overlap = 0
        matched_option = None
        for opt in active_options:
            opt_norm = set([w for w in re.split(r'\W+', opt.lower()) if len(w) > 2])
            overlap = len(ans_norm & opt_norm)
            if overlap > best_overlap:
                best_overlap = overlap
                matched_option = opt
                
        if matched_option:
            return {"resolved_option_id": matched_option, "matched_option": matched_option}
    return {}

def multimodal_answer_materialization_node(state: PRDState) -> dict:
    """1c. Multimodal Answer Materialization Node"""
    ctx = _log_ctx(state, "multimodal_answer_materialization")
    log_event(**ctx, level="INFO", event_type="node_start", message="multimodal_answer_materialization started")
    
    raw_answer = state.get("raw_answer_buffer", "")
    uploaded_files = state.get("pending_event", {}).get("uploaded_files", [])
    
    if not uploaded_files:
        return {
            "effective_answer_for_commit": raw_answer,
            "answer_provenance": "user_text",
            "materialization_status": "user_text_passthrough",
            "matched_context_id": None,
            "materialization_conflict": False,
            "materialization_conflict_reason": None
        }
        
    if len(uploaded_files) > 1:
        log_event(**ctx, level="WARNING", event_type="unsupported_multi_file", message="Multiple files not supported for direct materialization. Degrading to user text.")
        return {
            "effective_answer_for_commit": raw_answer,
            "answer_provenance": "user_text",
            "materialization_status": "multi_file_unsupported",
            "matched_context_id": None,
            "materialization_conflict": False,
            "materialization_conflict_reason": None
        }
        
    target_file_id = uploaded_files[0].get("file_id")
    bg_contexts = state.get("background_generated_contexts", [])
    matching_ctx = next((c for c in bg_contexts if c.get("image_file_id") == target_file_id), None)
    
    if not matching_ctx or not matching_ctx.get("generated_summary"):
        log_event(**ctx, level="WARNING", event_type="image_missing_context", message=f"No summary found for file_id {target_file_id}. Degrading to user text.")
        return {
            "effective_answer_for_commit": raw_answer,
            "answer_provenance": "user_text",
            "materialization_status": "image_missing",
            "matched_context_id": None if not matching_ctx else matching_ctx.get("context_id"),
            "materialization_conflict": False,
            "materialization_conflict_reason": None
        }
        
    summary = matching_ctx.get("generated_summary")
    
    conflict = False
    conflict_reason = None
    
    if raw_answer.strip():
        llm = _get_llm()
        try:
            schema = {
                "name": "ConflictCheck",
                "description": "Output schema for conflict check",
                "type": "object",
                "properties": {
                    "material_conflict": {"type": "boolean"},
                    "reason": {"type": "string"}
                },
                "required": ["material_conflict", "reason"]
            }
            sys_msg = (
                "You adjudicate if there is a material conflict between a user's literal text and an automated image description.\n"
                "A material conflict exists if they suggest different workflows, classify the image oppositely, or imply contradictory answers.\n"
                "Reply with exactly JSON holding `material_conflict` (bool) and `reason` (str)."
            )
            user_msg = f"User Text:\n{raw_answer}\n\nImage Vision Summary:\n{summary}\n\nDo they fundamentally conflict?"
            
            structured_llm = llm.with_structured_output(schema)
            res = structured_llm.invoke([
                {"role": "system", "content": sys_msg},
                {"role": "user", "content": user_msg}
            ])
            conflict = res.get("material_conflict", False) if isinstance(res, dict) else False
            conflict_reason = res.get("reason", "") if isinstance(res, dict) else ""
            log_event(**ctx, level="INFO", event_type="image_text_conflict_evaluated", conflict=conflict, reason=conflict_reason)
        except Exception as e:
            log_event(**ctx, level="ERROR", event_type="conflict_check_failed", message=str(e))
            conflict = False
            conflict_reason = None

    if conflict:
        return {
            "effective_answer_for_commit": raw_answer,
            "answer_provenance": "user_text",
            "materialization_status": "image_bound",
            "matched_context_id": matching_ctx.get("context_id"),
            "materialization_conflict": True,
            "materialization_conflict_reason": conflict_reason,
            "reply_intent": "UNCLEAR_META"
        }

    return {
        "effective_answer_for_commit": f"<image_derived_context>\n{summary}\n</image_derived_context>",
        "answer_provenance": "image_derived",
        "materialization_status": "image_bound",
        "matched_context_id": matching_ctx.get("context_id"),
        "materialization_conflict": False,
        "materialization_conflict_reason": None
    }

def semantic_assessor_node(state: PRDState) -> dict:
    """2. Semantic Assessor Node"""
    ctx = _log_ctx(state, "semantic_assessor")
    log_event(**ctx, level="INFO", event_type="node_start", message="semantic_assessor started")

    raw_answer = state.get("effective_answer_for_commit", state.get("raw_answer_buffer", "")).strip()
    
    # TC3: Unconditionally read from the FULL active_semantic_target object.
    active_target = state.get("active_semantic_target", {})
    question = active_target.get("target_text", state.get("current_questions", "")).strip()
        
    current_question_object = state.get("current_question_object", {})
    subparts = current_question_object.get("subparts", [])
    remaining_subparts = list(state.get("remaining_subparts", subparts))
    is_numeric_repair = bool(state.get("parent_question_id"))
    intent = state.get("reply_intent", "")

    # Clauses
    sentences = _sent_tokenize(raw_answer)
    candidates = []
    g_idx = 0
    for sent in sentences:
        raw_clauses = [c.strip() for c in re.split(r'(?<=[,;])\s+', sent) if c.strip()]
        merged = []
        for cl in raw_clauses:
            if cl.lower().startswith(("which ", "because ", "since ")) and merged:
                merged[-1] += ", " + cl
            else:
                merged.append(cl)
        for cl in merged:
            candidates.append((cl, g_idx, sent))
            g_idx += 1

    subpart_evidence_candidates = []
    snippets_by_subpart = {}
    ans_lower = raw_answer.lower()

    if is_numeric_repair or intent == "DIRECT_ANSWER" or "both" in ans_lower or "all" in ans_lower:
        subpart_evidence_candidates = list(remaining_subparts)
        for sp in subpart_evidence_candidates:
            snippets_by_subpart[sp] = raw_answer
    else:
        for sp in remaining_subparts:
            sp_norm = _tokenize_norm(sp)
            best_cl, best_sent = None, None
            best_s, best_p, best_d, best_i = 0, False, 0.0, 9999
            for (cl, idx, parent_sent) in candidates:
                cl_norm = _tokenize_norm(cl)
                b = len(sp_norm & cl_norm)
                if b == 0: continue
                p = 1 if _polarity_near(cl, sp_norm) else 0
                score = b + p
                density = b / max(len(cl_norm), 1)
                pf = p > 0
                better = (
                    score > best_s or
                    (score == best_s and pf and not best_p) or
                    (score == best_s and pf == best_p and density > best_d) or
                    (score == best_s and pf == best_p and density == best_d and idx < best_i)
                )
                if better:
                    best_s, best_cl, best_sent = score, cl, parent_sent
                    best_p, best_d, best_i = pf, density, idx

            if best_s == 0:
                fb_sent, fb_score = None, 0
                for sent in sentences:
                    sb = len(sp_norm & _tokenize_norm(sent))
                    if sb > fb_score: fb_score, fb_sent = sb, sent
                snippets_by_subpart[sp] = fb_sent
                if fb_sent: subpart_evidence_candidates.append(sp)
            else:
                subpart_evidence_candidates.append(sp)
                base_b = best_s - (1 if best_p else 0)
                overlap_ratio = base_b / max(len(sp_norm), 1)
                if overlap_ratio < 0.5: snippets_by_subpart[sp] = best_sent
                elif best_cl and _has_reversal(best_cl): snippets_by_subpart[sp] = best_sent
                else: snippets_by_subpart[sp] = best_cl

    _new_remaining_raw = [s for s in remaining_subparts if s not in subpart_evidence_candidates]

    chat_hist = state.get("chat_history", [])
    latest_semantics = {}
    for m in reversed(chat_hist):
        if m.get("role") == "user" and "semantics" in m:
            latest_semantics = m["semantics"]
            break

    action_verbs = []
    has_mapping_mentions = False
    if "action_graph" in latest_semantics:
        action_verbs = [e.get("verb", "").lower() for e in latest_semantics["action_graph"]]
    if "candidates" in latest_semantics:
        cand_surfaces = [c.get("surface", "").lower() for c in latest_semantics["candidates"]]
        has_mapping_mentions = any(w in " ".join(cand_surfaces) for w in ["product code", "match", "id", "name", "criteria", "email"])
    if not has_mapping_mentions:
        has_mapping_mentions = any(w in raw_answer.lower() for w in ["product code", "match", "id", "name", "criteria", "email"])

    return {
        "subpart_evidence_candidates": subpart_evidence_candidates,
        "snippets_by_subpart": snippets_by_subpart,
        "new_remaining_raw": _new_remaining_raw,
        "action_verbs": action_verbs,
        "has_mapping_mentions": has_mapping_mentions
    }

def blocker_transition_node(state: PRDState) -> dict:
    """Phase 2b: Blocker Transition Node"""
    ctx = _log_ctx(state, "blocker_transition")
    log_event(**ctx, level="INFO", event_type="node_start", message="blocker_transition started")

    subpart_evidence_candidates = state.get("subpart_evidence_candidates", [])
    _new_remaining_raw = state.get("new_remaining_raw", [])
    action_verbs = state.get("action_verbs", [])
    has_mapping_mentions = state.get("has_mapping_mentions", False)
    raw_answer = state.get("raw_answer_buffer", "").strip()
    question = state.get("current_questions", "").strip()
    
    current_blocking = list(state.get("blocking_fields", []))
    resolved_subparts = []

    transitioned_remaining = []
    for s in _new_remaining_raw:
        transitioned_remaining.append(s)

    for resolved_b in subpart_evidence_candidates:
        b_lower = resolved_b.lower()
        transitioned_item = None
        if "workflow" in b_lower or "sequence" in b_lower:
            if action_verbs: transitioned_item = "mapping_logic_missing"
            elif len(raw_answer.split()) > 10:
                decision = invoke_llm_adjudicator(
                    task_type="blocker_clearing",
                    context_data={
                        "current_blocker": resolved_b,
                        "previous_question": question,
                        "user_answer": raw_answer,
                        "run_id": state.get("run_id"),
                        "thread_id": state.get("thread_id")
                    },
                    llm=_get_llm(),
                )
                if decision and decision.decision_result: transitioned_item = decision.recommended_next_blocker_if_any or "mapping_logic_missing"
                else: transitioned_item = resolved_b + "_specific_interaction"
            else: transitioned_item = resolved_b + "_specific_interaction"
        elif "mapping" in b_lower or "logic" in b_lower:
            if has_mapping_mentions: transitioned_item = "destination_handling_missing"
            elif len(raw_answer.split()) > 10:
                decision = invoke_llm_adjudicator(
                    task_type="blocker_clearing",
                    context_data={
                        "current_blocker": resolved_b,
                        "previous_question": question,
                        "user_answer": raw_answer,
                        "run_id": state.get("run_id"),
                        "thread_id": state.get("thread_id")
                    },
                    llm=_get_llm(),
                )
                if decision and decision.decision_result: transitioned_item = decision.recommended_next_blocker_if_any or "destination_handling_missing"
                else: transitioned_item = resolved_b + "_specific_fields"
            else: transitioned_item = resolved_b + "_specific_fields"
        elif "owner" in b_lower:
            transitioned_item = "escalation_path_missing"

        if transitioned_item:
            transitioned_remaining.append(transitioned_item)
            log_event(**ctx, level="INFO", event_type="blocker_evaluation", field=resolved_b, verdict="transitioned", message=f"Blocker {resolved_b} transitioned to {transitioned_item}")
        else:
            resolved_subparts.append(resolved_b)
            log_event(**ctx, level="INFO", event_type="blocker_evaluation", field=resolved_b, verdict="cleared", message=f"Blocker {resolved_b} cleared")

    new_remaining = []
    for s in transitioned_remaining:
        if s not in new_remaining: new_remaining.append(s)

    # Mutate blocking fields by removing any cleared subparts (resolved_subparts)
    removed_count = 0
    for sp in resolved_subparts:
        if sp in current_blocking:
            current_blocking.remove(sp)
            removed_count += 1
            
    return_payload = {
        "remaining_subparts": new_remaining,
        "resolved_subparts": resolved_subparts,
        "blocking_fields": current_blocking
    }
    
    current_count = state.get("missing_required_fields_count", 0)
    return_payload["missing_required_fields_count"] = max(0, current_count - removed_count)

    return return_payload


def contradiction_validator_node(state: PRDState) -> dict:
    """3. Contradiction Validator Node"""
    ctx = _log_ctx(state, "contradiction_validator")
    log_event(**ctx, level="INFO", event_type="node_start", message="contradiction_validator started")
    bridge = build_conversation_understanding_output(state)
    
    has_conflicts = "conflicted_concepts" in bridge["draft_readiness"]["hard_blockers"]
    conflict_records = bridge.get("conflicted_concepts", [])
    
    return {
        "has_conflicts": has_conflicts,
        "conflict_records": conflict_records,
        "draft_readiness": bridge["draft_readiness"],
        "current_concepts": bridge["current_concepts"]
    }

def truth_eligibility_node(state: PRDState) -> dict:
    """3b. Truth Eligibility Node"""
    ctx = _log_ctx(state, "truth_eligibility")
    log_event(**ctx, level="INFO", event_type="node_start", message="truth_eligibility started")
    
    conflict_records = state.get("conflict_records", [])
    has_conflicts = len(conflict_records) > 0
    is_eligible = not has_conflicts
    reason = "conflicted concepts must be resolved before committing to canonical truth" if has_conflicts else "no conflicts detected"
    
    log_event(
        **ctx, level="INFO", event_type="truth_gate_evaluated",
        message=f"Truth eligibility evaluated to {is_eligible}",
        is_eligible=is_eligible,
        conflict_count=len(conflict_records),
        reason=reason
    )
    
    return {
        "is_eligible": is_eligible,
        "eligibility_reason": reason
    }

def truth_commit_node(state: PRDState) -> dict:
    """4. Truth Commit Node"""
    ctx = _log_ctx(state, "truth_commit")
    log_event(**ctx, level="INFO", event_type="node_start", message="truth_commit started")

    # Eligibility check payload provided directly by the Graph State via earlier nodes
    if not state.get("is_eligible", False):
        log_event(
            **ctx, level="INFO", event_type="commit_truth_blocked",
            reason="conflicted concepts must be resolved before committing to canonical truth",
            message="Commit truth blocked due to conflicts"
        )
        return {"chat_history": [{
            "role": "assistant",
            "type": "system",
            "content": "I see a conflict with what we discussed earlier. Let me verify.",
        }]}
        
    if state.get("materialization_conflict", False):
        log_event(
            **ctx, level="INFO", event_type="commit_truth_blocked_by_materialization",
            reason="image and text explicitly conflict",
            message="Commit truth blocked due to materialization conflict"
        )
        return {"chat_history": [{
            "role": "assistant",
            "type": "system",
            "content": "Before I save that, I need to make sure I understand correctly since the image and text seem different.",
        }]}

    section = get_section_by_index(state.get("section_index", 0))
    iteration = state.get("iteration", 0)
    raw_answer = state.get("effective_answer_for_commit", state.get("raw_answer_buffer", "")).strip()
    interpreted = state.get("interpreted_answer", raw_answer)
    resolved_subparts = state.get("resolved_subparts", [])
    snippets_by_subpart = state.get("snippets_by_subpart", {})
    is_numeric_repair = bool(state.get("parent_question_id"))
    question = state.get("current_questions", "").strip()

    current_question_object = state.get("current_question_object", {})
    stored_question = question
    if is_numeric_repair:
        stored_question = current_question_object.get("question_text", question)

    resolution_source = "numeric_repair" if is_numeric_repair else "direct_answer"

    existing_qa = list(state.get("section_qa_pairs", []))
    round_n = len(existing_qa) + 1
    concept_key = f"{section.id}:iter_{iteration}:round_{round_n}"

    last_msg = state.get("chat_history", [])[-1] if state.get("chat_history") else {}
    source_message_id = last_msg.get("msg_id", f"msg_{len(state.get('chat_history', [])) - 1}")

    current_version = state.get("store_version", 0) + 1
    fact_id = str(uuid.uuid4())

    promoted_concepts = [c for c in state.get("current_concepts", []) if c.get("confidence", 0) >= 0.85]

    qa_entry = {
        "questions": stored_question,
        "answer": interpreted,
        "section": section.title,
        "resolution_source": resolution_source,
    }

    semantic_concepts = [c["concept_key"] for c in promoted_concepts]
    
    store_update: dict = {
        concept_key: {
            "fact_id": fact_id,
            "answer": interpreted,
            "questions": stored_question,
            "resolved_subparts": resolved_subparts,
            "evidence_snippets_by_subpart": snippets_by_subpart,
            "semantic_concepts": semantic_concepts,
            "source_message_id": source_message_id,
            "source_snippet": raw_answer,
            "provenance_confidence": 10.0,
            "display_time": "",
            "section": section.title,
            "section_id": section.id,
            "iteration": iteration,
            "round": round_n,
            "source_round": round_n,
            "contradiction_flagged": False,
            "version": current_version,
            "resolution_source": resolution_source,
            "parent_question_id": state.get("parent_question_id", ""),
            "answer_provenance": state.get("answer_provenance", "user_text"),
        }
    }

    log_canonical_write(
        thread_id=state.get("thread_id", ""),
        run_id=state.get("run_id", ""),
        node_name="truth_commit",
        fact_id=fact_id,
        concept_id=concept_key,
        change_type="CREATED",
        version=current_version,
    )

    IntegrityValidator.validate_mutation(
        thread_id=state.get("thread_id", ""),
        run_id=state.get("run_id", ""),
        node_name="truth_commit",
        store=state.get("confirmed_qa_store", {}),
        update=store_update,
        section_id=section.id
    )
    
    if store_update:
        logging.getLogger("orchestrator_metrics").info(f"TRUTH_COMMIT_APPROVED | concepts={list(store_update.keys())}")

    return_payload = {
        "pending_echo": "",
        "pending_concept_updates": {},
        "answer_confirmation_status": "CONFIRMED",
        "section_qa_pairs": existing_qa + [qa_entry],
        "confirmed_qa_store": store_update,
        "store_version": current_version,
        "contradiction_log": [],
    }
    
    return return_payload

def concept_history_update_node(state: PRDState) -> dict:
    """Phase 4a: Concept History Update Node"""
    concept_history = state.get("concept_history", {})
    last_msg = state.get("chat_history", [])[-1] if state.get("chat_history") else {}
    source_message_id = last_msg.get("msg_id", f"msg_{len(state.get('chat_history', [])) - 1}")
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    for key, c_state in concept_history.items():
        if source_message_id in c_state.get("mentions", []) and c_state.get("status") in (ConceptStatus.MENTIONED.value, ConceptStatus.CONFLICTED.value, ConceptStatus.NEGATED.value):
            old_s = c_state["status"]
            c_state["status"] = ConceptStatus.CURRENT.value
            c_state["is_current"] = True
            c_state["status_reason"] = f"Promoted via direct positive assertion (source: {source_message_id})"
            c_state["last_transition_at"] = now
            _log_semantic_transition(key, old_s, ConceptStatus.CURRENT.value, "Promotion gate cleared", source_message_id)

    return {"concept_history": concept_history}

def echo_generation_node(state: PRDState) -> dict:
    """Phase 4b: Optional policy-driven echo generation"""
    ctx = _log_ctx(state, "echo_generation")
    log_event(**ctx, level="INFO", event_type="node_start", message="echo_generation started")
    
    intent = state.get("reply_intent", "")
    raw_answer = state.get("raw_answer_buffer", "").strip()
    matched_option = state.get("matched_option")
    
    echo_text = ""
    interpreted = raw_answer

    # Policy 1: If it's a blended intent, we compose a special echo.
    if intent == 'BLENDED':
        echo_answer = "both matter. I'll proceed on that basis"
        interpreted = "Both options matter: " + raw_answer
        echo_text = f"You're right — I should have captured that. It sounds like {echo_answer} unless one is clearly the priority."
    else:
        # Policy 2: Explicit options
        if matched_option:
            interpreted = matched_option
            echo_text = f"I understand, the focus is {interpreted}."
        else:
            # Policy 3: Length gating for optional free-text
            clean_ans = raw_answer.strip()
            if len(clean_ans) > 60 or "->" in clean_ans or "\n" in clean_ans or len(clean_ans.split()) > 15:
                # Do NOT generate an echo if it's too long or structured
                echo_text = ""
            else:
                if clean_ans and clean_ans[0].islower():
                    clean_ans = clean_ans[0].upper() + clean_ans[1:]
                echo_text = f"Got it. {clean_ans.rstrip('.')}."
                
    return_payload = {
        "interpreted_answer": interpreted,
        "echo_text": echo_text
    }
    
    if echo_text:
        # Assuming the section can be retrieved or was stored in `echo_confirmation` type message if required.
        # But we append to chat_history explicitly.
        section_index = state.get("section_index", 0)
        section = get_section_by_index(section_index)
        return_payload["chat_history"] = [
            {
                "role": "assistant",
                "msg_id": f"msg_{str(uuid.uuid4())[:8]}",
                "type": "echo_confirmation",
                "section": section.title if section else "General",
                "content": echo_text,
            }
        ]
        
    return return_payload

def state_cleanup_node(state: PRDState) -> dict:
    """Phase 4b: State Cleanup Node"""
    matched_option = state.get("matched_option", "")
    q_status = state.get("question_status", "")
    resolved_subparts = state.get("resolved_subparts", [])
    is_numeric_repair = bool(state.get("parent_question_id"))
    return_payload = {
        "raw_answer_buffer": "",
        "question_status": "ANSWERED" if matched_option else q_status,
        "resolved_option_id": matched_option if matched_option else state.get("resolved_option_id", ""),
        "answered_at": str(time.time()) if matched_option else state.get("answered_at", ""),
        "subpart_evidence_candidates": [],
        "matched_option": "",
        "echo_text": "",
        "reply_intent": "",
        "interpreted_answer": ""
    }
    
    # State cleared items telemetry check
    ctx = _log_ctx(state, "state_cleanup")
    log_event(
        **ctx, 
        level="INFO", 
        event_type="state_zeroed", 
        message="State zeroed",
        cleared_keys=list(return_payload.keys())
    )
    
    if is_numeric_repair:
        return_payload["parent_question_id"] = ""
        return_payload["repair_question_id"] = ""
        return_payload["pending_numeric_clarification"] = False
        return_payload["question_status"] = "ANSWERED"
        
    return return_payload



def handle_numeric_error_node(state: PRDState) -> dict:
    """Numeric Error Node: Protect boundary checks for quantity answers."""
    ctx = _log_ctx(state, "handle_numeric_error")
    log_event(**ctx, level="INFO", event_type="node_start", message="handle_numeric_error started")
    
    reason = state.get("validation_reason", "Invalid numeric input.")
    
    # [TECH DEBT] chat_history injection is currently required for Streamlit rendering compatibility 
    # to maintain the "assistant" bubble grouping. The final render layer (UI) owns the actual English wording.
    return {
        "response_type": "numeric_validation_error",
        "validation_reason": reason,
        "pending_numeric_clarification": True,
        "question_status": "OPEN",
        "chat_history": [
            {
                "role": "assistant",
                "type": "numeric_validation_error",
                "content": ""  # UI / Assembly layer formats the text
            }
        ]
    }


def file_upload_intake_node(state: PRDState) -> dict:
    """Processes uploaded files uniformly before semantic processing."""
    ctx = _log_ctx(state, "file_upload_intake")
    log_event(**ctx, level="INFO", event_type="node_start", message="file_upload_intake started")
    
    uploaded_files = state.get("uploaded_files", [])
    if not uploaded_files:
        return {
            "upload_status": "rejected",
            "accepted_files": [],
            "rejected_files": [{"filename": "", "reason": "no_files_uploaded"}],
            "downstream_analysis_allowed": False
        }
    
    accepted = []
    rejected = []
    
    ALLOWED_MIME_TYPES = {
        "image/jpeg": "jpg",
        "image/png": "png",
        "application/pdf": "pdf"
    }
    ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "pdf"}
    
    for f in uploaded_files:
        # Validate payload structure
        if not isinstance(f, dict) or "file_id" not in f or "filename" not in f or "mime_type" not in f or "size_bytes" not in f:
            rejected.append({"filename": f.get("filename", ""), "reason": "malformed_file_payload"})
            continue
            
        if f["size_bytes"] == 0:
            rejected.append({"filename": f["filename"], "reason": "empty_file"})
            continue
            
        mime = str(f["mime_type"]).lower()
        ext = f["filename"].split(".")[-1].lower() if "." in f.get("filename", "") else ""
        
        file_type = ""
        # MIME type is primary validation signal (FU4)
        if mime in ALLOWED_MIME_TYPES:
            file_type = ALLOWED_MIME_TYPES[mime]
        elif ext in ALLOWED_EXTENSIONS:
            file_type = "jpg" if ext == "jpeg" else ext
            
        if not file_type:
            rejected.append({"filename": f["filename"], "reason": "unsupported_file_type"})
        else:
            accepted.append({
                "file_id": f["file_id"],
                "filename": f["filename"],
                "file_type": file_type,
                "bytes": f.get("bytes")
            })
            
    if not accepted and rejected:
        upload_status = "rejected"
        downstream = False
    elif accepted and rejected:
        upload_status = "accepted_partial"
        downstream = True
    else:
        upload_status = "accepted"
        downstream = True
        
    log_event(**ctx, level="INFO", event_type="file_upload_intake_result", message="file_upload_intake_result", 
              upload_status=upload_status, accepted_count=len(accepted), rejected_count=len(rejected))
              
    return {
        "upload_status": upload_status,
        "accepted_files": accepted,
        "rejected_files": rejected,
        "downstream_analysis_allowed": downstream,
        "uploaded_files": []
    }
    

def file_upload_rejection_node(state: PRDState) -> dict:
    """Emits rejection feedback to the user and halts downstream pipeline."""
    ctx = _log_ctx(state, "file_upload_rejection")
    log_event(**ctx, level="INFO", event_type="node_start", message="file_upload_rejection started")
    
    # We will format a user-friendly error message based on the rejected files.
    rejected = state.get("rejected_files", [])
    
    # The UI layer handles rendering, but we provide the structured message payload here
    return {
        "chat_history": [
            {
                "role": "assistant",
                "msg_id": f"msg_{str(uuid.uuid4())[:8]}",
                "type": "file_upload_rejection_error",
                "rejected_files": rejected,
                "content": ""  # UI assembly layer formats this into human-readable text
            }
        ]
    }

def uploaded_image_description_node(state: PRDState) -> dict:
    """Converts accepted JPG and PNG images into bounded visual descriptions."""
    import io
    import base64
    from pydantic import BaseModel, Field
    
    class RawVisualObservation(BaseModel):
        high_level_description: str = Field(description="A concise summary of what this image fundamentally is (e.g. screenshot, whiteboard diagram, form).")
        distinct_visible_elements: list[str] = Field(description="List of explicitly distinct elements visible in the frame.")
        unreadable_or_uncertain_areas: list[str] = Field(description="List of areas marked intentionally vague or unreadable.")
        
    ctx = _log_ctx(state, "uploaded_image_description")
    log_event(**ctx, level="INFO", event_type="node_start", message="uploaded_image_description started")
    
    accepted_files = state.get("accepted_files", [])
    image_files = [f for f in accepted_files if f.get("file_type") in ("jpg", "png")]
    
    if not image_files:
        log_event(**ctx, level="INFO", event_type="image_description_result", message="No accepted images found")
        return {
            "image_description_status": "no_accepted_images",
            "described_images": [],
            "needs_followup": True
        }
        
    described_images = []
    
    try:
        from PIL import Image, ImageOps, UnidentifiedImageError
        from langchain_core.messages import HumanMessage
        llm = _get_llm().with_structured_output(RawVisualObservation)
    except Exception as e:
        log_event(**ctx, level="ERROR", event_type="image_description_setup_error", message=str(e))
        return {"image_description_status": "failed", "described_images": [], "needs_followup": True}
        
    MAX_IMAGE_DIMENSION = 2048
    
    for img in image_files:
        fid = img["file_id"]
        
        # 1. Binary Retrieval (IP1)
        if "bytes" not in img or not img["bytes"]:
            described_images.append({
                "file_id": fid,
                "image_description_status": "failed",
                "error_code": "missing_binary",
                "high_level_description": None,
                "visible_elements": [],
                "uncertainties": []
            })
            continue
            
        raw_bytes = img["bytes"]
        bytes_io = io.BytesIO(raw_bytes)
        
        # 2. PIL Preprocessing Sequence (IP2, IP3)
        try:
            Image.open(bytes_io).verify()
        except Exception:
            described_images.append({
                "file_id": fid,
                "image_description_status": "failed",
                "error_code": "corrupted_image",
                "high_level_description": None,
                "visible_elements": [],
                "uncertainties": []
            })
            continue
            
        try:
            bytes_io.seek(0)
            pil_img = Image.open(bytes_io)
            pil_img = ImageOps.exif_transpose(pil_img)
            
            orig_size = pil_img.size
            pil_img.thumbnail((MAX_IMAGE_DIMENSION, MAX_IMAGE_DIMENSION))
            normalized_bytes = io.BytesIO()
            pil_img.save(normalized_bytes, format="PNG")
            img_b64 = base64.b64encode(normalized_bytes.getvalue()).decode("utf-8")
            
            log_event(**ctx, level="INFO", event_type="preprocessing_complete", 
                      message="Preprocessing completed successfully",
                      file_id=fid, orig_size=orig_size, norm_size=pil_img.size)
        except Exception as e:
            log_event(**ctx, level="ERROR", event_type="preprocessing_error", message=str(e), file_id=fid)
            described_images.append({
                "file_id": fid,
                "image_description_status": "failed",
                "error_code": "corrupted_image",
                "high_level_description": None,
                "visible_elements": [],
                "uncertainties": []
            })
            continue

        instruction = (
            "You are a strict, objective visual observation engine.\n"
            "Below is a single image file. Provide a structured description purely based on what is visibly seen.\n"
            "Do NOT attempt to guess hidden meanings, user goals, or context outside this image.\n"
            "Do NOT transcribe entire paragraphs, but you may quote short phrases or headers (< 15 words) if clearly readable.\n"
            "Ensure unreadable sections are strictly flagged as uncertainties.\n"
            "Do not invent details."
        )
        
        msg = HumanMessage(
            content=[
                {"type": "text", "text": instruction},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{img_b64}"}
                }
            ]
        )
        
        retries = 1
        
        for attempt in range(retries + 1):
            try:
                # API Boundary Log
                log_event(**ctx, level="INFO", event_type="llm_call_start", message="Invoking multimodal API", purpose="multimodal_image_observation", file_id=fid, attempt=attempt)
                raw_obs = llm.invoke([msg])
                
                # Output Schema Alignment (IP4)
                described_images.append({
                    "file_id": fid,
                    "filename": img["filename"],
                    "image_description_status": "described",
                    "error_code": "",
                    "high_level_description": raw_obs.high_level_description,
                    "visible_elements": raw_obs.distinct_visible_elements,
                    "uncertainties": raw_obs.unreadable_or_uncertain_areas
                })
                break
            except Exception as e:
                err_str = str(e).lower()
                error_code = "parse_error" if "validation" in err_str or "pydantic" in err_str else "api_timeout"
                log_event(**ctx, level="ERROR", event_type="llm_call_error", message=str(e), file_id=fid, error_code=error_code, attempt=attempt, error_str=str(e))
                if attempt == retries:
                    described_images.append({
                        "file_id": fid,
                        "image_description_status": "failed",
                        "error_code": error_code,
                        "high_level_description": None,
                        "visible_elements": [],
                        "uncertainties": []
                    })
                    
    log_event(**ctx, level="INFO", event_type="image_description_result", 
              message="Successfully processed images via multimodal API", count=len(described_images))
              
    return {
        "image_description_status": "described",
        "described_images": described_images,
        "needs_followup": False
    }

def image_description_session_context_node(state: PRDState) -> dict:
    """Converts described images into structured, non-blocking BackgroundContext objects."""
    import uuid
    from datetime import datetime, timezone
    
    ctx = _log_ctx(state, "image_description_session_context")
    log_event(**ctx, level="INFO", event_type="node_start", message="image_description_session_context started")
    
    status = state.get("image_description_status", "")
    described_images = state.get("described_images", [])

    if status != "described" or not described_images:
        log_event(**ctx, level="INFO", event_type="session_context_skipped", message="Skipped context generation due to invalid status")
        return {}

    # Identify the user turn that triggered this context
    chat_history = state.get("chat_history", [])
    user_msgs = [msg for msg in chat_history if msg.get("role") == "user"]
    source_turn_id = user_msgs[-1].get("msg_id") if user_msgs else str(uuid.uuid4())
    
    background_contexts = []
    
    try:
        now = datetime.now(timezone.utc).isoformat()
        
        for img in described_images:
            if img.get("image_description_status") == "failed":
                continue
                
            block = [
                "[what_is_going_on]",
                img.get("high_level_description") or "",
                "",
                "[entities]"
            ]
            
            entities = img.get("visible_elements", [])
            for e in entities:
                block.append(f"- {e}")
            if not entities:
                block.append("- No specific entities identified")
                
            block.extend([
                "",
                "[uncertainties]"
            ])
            
            uncerts = img.get("uncertainties", [])
            for u in uncerts:
                block.append(f"- {u}")
            if not uncerts:
                block.append("- No explicit uncertainties logged")
                
            generated_summary = "\n".join(block)
            
            bg_ctx = {
                "context_id": str(uuid.uuid4()),
                "image_file_id": img.get("file_id", "unknown"),
                "source_turn_id": source_turn_id,
                "created_at": now,
                "updated_at": now,
                "generated_summary": generated_summary,
                "edited_summary": None,
                "is_active": True
            }
            background_contexts.append(bg_ctx)

        log_event(**ctx, level="INFO", event_type="session_context_generated", message=f"Generated {len(background_contexts)} background contexts")
        
        return {
            "background_generated_contexts": background_contexts
        }
    except Exception as e:
        log_event(**ctx, level="ERROR", event_type="session_context_error", message=str(e))
        return {}
