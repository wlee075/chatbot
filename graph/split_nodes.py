import time
import re
import datetime
import uuid
import logging
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

def clarification_router_node(state: PRDState) -> dict:
    """1a. Clarification Router Node"""
    ctx = _log_ctx(state, "clarification_router")
    log_event(**ctx, level="INFO", event_type="node_start", message="clarification_router started")
    
    reply_intent = state.get("reply_intent", "")
    route = "option_resolution"
    fallback_state = "proceeding to normal extraction"
    
    if reply_intent in ("DIRECT_CLARIFICATION_QUESTION", "UNCLEAR_META"):
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

def semantic_assessor_node(state: PRDState) -> dict:
    """2. Semantic Assessor Node"""
    ctx = _log_ctx(state, "semantic_assessor")
    log_event(**ctx, level="INFO", event_type="node_start", message="semantic_assessor started")

    raw_answer = state.get("raw_answer_buffer", "").strip()
    question = state.get("current_questions", "").strip()
    
    interp = state.get("reply_context_interpretation", {})
    if interp and interp.get("relationship_type") == "direct_answer_to_replied_message" and state.get("reply_context_message_text"):
        log_event(**ctx, level="INFO", event_type="semantic_assessor_context_shift", message="Evaluating answer against explicitly bounded reply context instead of current active question")
        question = state.get("reply_context_message_text")
        
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

    section = get_section_by_index(state.get("section_index", 0))
    iteration = state.get("iteration", 0)
    raw_answer = state.get("raw_answer_buffer", "").strip()
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
