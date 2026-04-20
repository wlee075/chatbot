import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from graph.nodes import generate_questions_node

def capture_payload():
    state = {
        "section_index": 0,
        "confirmed_qa_store": {},
        "context_doc": "it is troublesome and time consuming so we are trying to reduce manual processing of these PDF files by forwarding them to the group mailbox",
        "thread_id": "test",
        "run_id": "test"
    }
    
    # generate_questions_node is modified to use isinstance(str) containment, so it will log the raw response.
    res = generate_questions_node(state)
    print("Result captured:", res)

if __name__ == "__main__":
    capture_payload()
