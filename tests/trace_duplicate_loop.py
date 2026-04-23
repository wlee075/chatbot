import os
import dotenv
dotenv.load_dotenv()

import logging
logging.basicConfig(level=logging.INFO)

from graph.nodes import generate_questions_node
from graph.state import PRDState

def run_trace():
    print("Testing generate_questions_node with the exact user input and duplicate guard logic...")
    
    # Simulating the exact state leading up to the infinite loop
    # Previous turn question:
    prior_question = "Can you give me a specific example of how this process works in practice today?"
    
    # User's robust answer solving the blockers:
    text = "The user replied with a long, concrete explanation covering the current workflow, roles involved, pain points, and how automation would help."
    
    state = PRDState(
        section_index=0,
        raw_answer_buffer=text,
        thread_id="test_duplicate_loop",
        run_id="test_duplicate_loop",
        question_status="OPEN",
        active_question_type="OPEN_ENDED",
        current_questions=prior_question,
        recent_questions=[prior_question],
        
        # Simulating that QA store already saved the previous response and its associated subparts
        confirmed_qa_store={
            "qa_1": {
                "section_id": "workflow", # generic section
                "questions": prior_question,
                "resolved_subparts": ["mapping_logic"], # The subpart it resolved
                "contradiction_flagged": False
            }
        },
        remaining_subparts=["review_workflow"], # Assuming 1 subpart left
    )
    
    # We must patch the state section lookup logic if necessary, or just rely on the node defaults
    class DummySection:
        id: str = "workflow"
        
    # We patch context
    result = generate_questions_node(state)
    print("\n--- TRACE RESULT ---")
    print("Final selected question:", result.get("current_questions"))

if __name__ == "__main__":
    run_trace()
