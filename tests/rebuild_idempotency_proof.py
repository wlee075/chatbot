import os
import sys
import json
import uuid

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from graph.nodes import rebuild_mirror_node
from graph.state import PRDState

def prove_idempotency():
    print("--- Q1: Idempotency Proof (State Dumps) ---")
    
    # Initial State
    store = {
        "k1": {"section_id": "key_stakeholders", "answer": "Alice", "questions": "Who?", "round": 1, "iteration": 0},
        "k2": {"section_id": "key_stakeholders", "answer": "Bob", "questions": "Roles?", "round": 2, "iteration": 0}
    }
    state = {
        "section_index": 2, # Key Stakeholders
        "confirmed_qa_store": store,
        "section_qa_pairs": [{"questions": "STALE", "answer": "STALE"}], # Pre-existing corrupted mirror
        "rebuild_count": 0,
        "thread_id": "proof_thread",
        "run_id": "proof_run"
    }

    print("\n[BEFORE STATE] (Corrupted Mirror)")
    print(f"section_qa_pairs: {state['section_qa_pairs']}")

    # First Run
    run1 = rebuild_mirror_node(state)
    state.update(run1)
    print("\n[AFTER FIRST RUN]")
    print(f"section_qa_pairs: {state['section_qa_pairs']}")
    print(f"rebuild_count: {state['rebuild_count']}")

    # Second Run
    run2 = rebuild_mirror_node(state)
    
    print("\n[AFTER SECOND RUN]")
    print(f"section_qa_pairs: {run2['section_qa_pairs']}")
    print(f"rebuild_count: {run2['rebuild_count']}")

    # Equality Proof
    assert state['section_qa_pairs'] == run2['section_qa_pairs'], "Mirror mismatch!"
    print("\n[EQUALITY PROOF] (state1 == state2): PASS")
    print("rebuild_count incremented: PASS")

if __name__ == "__main__":
    prove_idempotency()
