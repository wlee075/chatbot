from typing import Optional, Literal
from pydantic import BaseModel, Field
from langchain_core.messages import SystemMessage
from utils.llm_logger import llm_invoke
from utils.logger import log_event

class AdjudicatorDecision(BaseModel):
    task_type: Literal["blocker_clearing", "semantic_repeat"] = Field(..., description="The type of adjudication task")
    decision_result: bool = Field(..., description="Boolean outcome of the decision. For blocker_clearing: True means user cleared it. For semantic_repeat: True means questions are the same intent.")
    confidence_score: float = Field(..., description="Confidence score from 0.0 to 1.0", ge=0.0, le=1.0)
    reason: str = Field(..., description="Short explanation for the decision.")
    recommended_next_blocker_if_any: Optional[str] = Field(None, description="If blocker cleared, the recommended next subpart to switch to")

def invoke_llm_adjudicator(
    task_type: str,
    context_data: dict,
    llm
) -> Optional[AdjudicatorDecision]:
    """
    Decoupled final-fallback adjudicator.
    Returns AdjudicatorDecision if confident, otherwise None (falling back to strict clarification safe paths).
    """
    if not llm:
        return None
        
    prompt = "You are a rigid analytical rules engine doing simple adjudications. Return ONLY the requested JSON structure.\n\n"
    
    if task_type == "blocker_clearing":
        prompt += f"Task: Determine if the user's latest answer clears the active blocker.\n\n"
        prompt += f"Active Blocker: {context_data.get('current_blocker')}\n"
        prompt += f"Previous Question: {context_data.get('previous_question')}\n"
        prompt += f"User Answer: {context_data.get('user_answer')}\n\n"
        prompt += "Guidelines: If the user provides explicit, actionable routing patterns, conditions, sequences, or specific mapping nouns solving the active blocker, set decision_result to True. If it's a generic word salad, 'it depends', 'I usually email them', without any specific structural context, set decision_result to False."
    elif task_type == "semantic_repeat":
        prompt += f"Task: Determine if the new candidate question is semantically identical or broader than the previous question, without offering any newly scoped refinement.\n\n"
        prompt += f"Previous Question: {context_data.get('previous_question')}\n"
        prompt += f"Candidate Question: {context_data.get('candidate_next_question')}\n\n"
        prompt += "Guidelines: If the candidate asks exactly the same logical question or broader, set decision_result to True (it IS a repeat). If it explicitly narrows to a smaller subset or directly confronts a new detail un-addressed by the first, set decision_result to False."
    else:
        return None
        
    messages = [SystemMessage(content=prompt)]
    try:
        response = llm_invoke(
            llm.with_structured_output(AdjudicatorDecision),
            messages,
            state={"run_id": context_data.get("run_id", "adjudicator"), "thread_id": context_data.get("thread_id", "adjudicator")},
            node_name="invoke_llm_adjudicator",
            purpose=f"adjudicator_{task_type}"
        )
        if isinstance(response, AdjudicatorDecision) or (isinstance(response, dict) and 'decision_result' in response):
            # If it comes back as dict (structured parser variations)
            if isinstance(response, dict):
                response = AdjudicatorDecision(**response)
                
            if response.confidence_score >= 0.7:
                # Log success
                log_event(
                    run_id=context_data.get("run_id", "fallback_run"),
                    thread_id=context_data.get("thread_id", "fallback_thread"),
                    level="INFO",
                    event_type="llm_fallback_decision",
                    message=f"Adjudicator decided: {response.decision_result}",
                    decision_type=task_type,
                    decision_result=response.decision_result,
                    confidence=response.confidence_score,
                    reason=response.reason
                )
                return response
            else:
                log_event(
                    run_id=context_data.get("run_id", "fallback_run"),
                    thread_id=context_data.get("thread_id", "fallback_thread"),
                    level="WARN",
                    event_type="llm_fallback_low_confidence",
                    message="Adjudicator returned low confidence, rejecting."
                )
    except Exception as e:
        log_event(
            run_id=context_data.get("run_id", "fallback_run"),
            thread_id=context_data.get("thread_id", "fallback_thread"),
            level="ERROR", 
            event_type="adjudicator_failure", 
            message=f"Adjudicator failed: {e}"
        )
        pass
        
    return None
