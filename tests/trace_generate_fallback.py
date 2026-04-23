import os
import dotenv
dotenv.load_dotenv()

import logging
logging.basicConfig(level=logging.INFO)

from graph.nodes import generate_questions_node
from graph.state import PRDState

def run_trace():
    print("Testing generate_questions_node with the exact user input...")
    
    text = "First we extract data from the Outlook email -> then map it in Excel -> then trigger the PDF retrieval system. The pain point is manual assignment. I want to automate tracking logic. We match exceptions. Send to Jira."
    
    state = PRDState(
        section_index=0,
        raw_answer_buffer=text,
        thread_id="test_run",
        run_id="test_run",
        question_status="OPEN",
        active_question_type="OPEN_ENDED",
        current_questions="Can you describe the current manual workflow today?",
        remaining_subparts=["mapping_logic", "review_workflow", "success_metric"],
        recent_questions=["Can you describe the current manual workflow today?"],
    )
    
    result = generate_questions_node(state)
    print("\n--- TRACE RESULT ---")
    print(result.get("current_questions"))
    print("\n--- NEW STATE ---")
    print(result.keys())

if __name__ == "__main__":
    run_trace()
