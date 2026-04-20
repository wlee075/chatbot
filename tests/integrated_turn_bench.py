import time
import os
import sys
from unittest.mock import patch

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from graph.builder import build_graph
from graph.state import PRDState

def run_integrated_bench():
    print("--- Phase 2 Integrated Turn Benchmark ---")
    
    app = build_graph()
    
    state = {
        "thread_id": "integ_thread",
        "run_id": "integ_run",
        "context_doc": "Mock doc",
        "max_iterations": 3,
        "phase": "elicitation",
        "framing_mode": "clear",
        "discovery_turn_count": 0,
        "section_index": 0,
        "iteration": 0,
        "current_questions": "",
        "current_draft": "",
        "verdict": "",
        "triage_decision": "",
        "recovery_mode_consecutive_count": 0,
        "overall_score": 0.0,
        "reflection": "",
        "technical_gaps": "",
        "user_gaps": "",
        "requirement_gaps": "",
        "confirmed_qa_store": {},
        "store_version": 1,
        "rebuild_count": 0,
        "section_qa_pairs": [],
        "pending_interrupt_type": "",
        "interrupt_queue": [],
        "image_context": [],
        "forward_hints": [],
        "contradiction_log": [],
        "tbd_fields": [],
        "prd_sections": {},
        "chat_history": [],
        "confidence": 0.0,
        "raw_answer_buffer": "Mock answer to questions",
        "current_question_object": {
            "question_id": "q1",
            "question_text": "What is the problem?",
            "subparts": ["problem"]
        },
        "remaining_subparts": ["problem"],
        "repair_instruction": "",
        "pending_echo": "So the problem is X?",
        "pending_concept_updates": {
             "key": {"answer": "Mock answer"}
        },
        "answer_confirmation_status": "CONFIRMED", # pretend echo just confirmed
        "pending_event": {},
        "event_history": [],
        "correction_stats": {"success": 0, "failure": 0},
        "section_scores": {},
        "draft_cache": {},
        "section_draft_meta": {},
        "draft_execution_mode": "",
        "impacted_section_scores": {},
        "_prd_sections_fmt_hash": "",
        "_formatted_prd_so_far": "",
        "impacted_sections": [],
        "last_section_updates": [],
        "prd_markdown": "",
        "is_complete": False,
    }

    class MockLLMResponse:
         def __init__(self, content):
             self.content = content

    # We mock llm_invoke to return fake results almost instantly
    # We will measure the graph executing from draft -> reflect -> advance
    with patch("graph.nodes.llm_invoke") as mock_invoke:
         mock_invoke.return_value = MockLLMResponse("Mock draft / reflection text")
         
         iterations = 50
         latencies = []
         
         # The start node will be draft (because answer_confirmation_status is CONFIRMED)
         for _ in range(iterations):
             state_copy = state.copy()
             t0 = time.perf_counter()
             # We invoke specifically starting from draft to run draft -> reflect -> route
             # Or just run from draft
             result = app.invoke(state_copy, {"configurable": {"thread_id": "integ_thread"}})
             t1 = time.perf_counter()
             latencies.append((t1 - t0) * 1000)

         latencies.sort()
         p50 = latencies[len(latencies)//2]
         p95 = latencies[int(len(latencies)*0.95)]
         
         print(f"Sample Size: {iterations} full pipeline turns (mocked LLM)")
         print(f"Path executed: from start to end of graph via routing")
         print(f"P50 Latency: {p50:.4f} ms")
         print(f"P95 Latency: {p95:.4f} ms")
         print(f"Max Latency: {max(latencies):.4f} ms")
         print("-" * 50)

if __name__ == "__main__":
    run_integrated_bench()
