import time
import os
import sys
import json
import uuid

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from graph.nodes import rebuild_mirror_node
from graph.state import PRDState

def run_latency_benchmark():
    print("--- Phase 2 Latency Benchmark (Scalability Check) ---")
    
    # 1. Simulate a large state (100 facts)
    store = {}
    for i in range(100):
        fid = str(uuid.uuid4())
        store[f"concept_{i}"] = {
            "fact_id": fid,
            "answer": "This is a long answer to simulate real world text volume. " * 5,
            "questions": "Standard elicitation question for concept " + str(i),
            "section_id": "key_stakeholders" if i < 50 else "proposed_solution",
            "section": "Key Stakeholders" if i < 50 else "Proposed Solution",
            "iteration": i // 20,
            "round": i % 20,
            "contradiction_flagged": False,
            "source_message_id": f"msg_{i}"
        }
    
    state = {
        "section_index": 2, # Key Stakeholders
        "confirmed_qa_store": store,
        "section_qa_pairs": [],
        "rebuild_count": 0,
        "thread_id": "bench_thread",
        "run_id": "bench_run",
        "iteration": 0
    }
    
    # 2. Measure rebuild_mirror_node
    iterations = 1000
    latencies = []
    
    import platform
    machine = f"{platform.system()} {platform.machine()} ({platform.processor()})"
    
    for _ in range(iterations):
        t0 = time.perf_counter()
        rebuild_mirror_node(state)
        t1 = time.perf_counter()
        latencies.append((t1 - t0) * 1000) # ms
    
    latencies.sort()
    p50 = latencies[len(latencies)//2]
    p95 = latencies[int(len(latencies)*0.95)]
    p99 = latencies[int(len(latencies)*0.99)]
    max_lat = max(latencies)
    
    print(f"Machine Context: {machine}")
    print(f"Sample Size: {iterations} iterations")
    print(f"Dataset Size: 100 facts (50 relevant to current section)")
    print(f"P50 Latency: {p50:.4f} ms")
    print(f"P95 Latency: {p95:.4f} ms")
    print(f"P99 Latency: {p99:.4f} ms")
    print(f"Max Latency: {max_lat:.4f} ms")
    print(f"Excluded: LLM invocations, serialization to disk, network I/O.")
    print(f"Included: Local Python logic, list filtering, dict access, sorting.")
    
    # Verdict
    if p95 < 10:
        print("Verdict: PASS (Headroom remains > 90% for sub-100ms node budget)")
    else:
        print("Verdict: CAUTION (Higher than expected overhead)")

if __name__ == "__main__":
    run_latency_benchmark()
