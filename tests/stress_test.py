import time
import os
import sys
import uuid

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from graph.nodes import rebuild_mirror_node

def run_stress_test():
    print("--- Phase 2 Stress Test at Scale (500, 1000, 5000 facts) ---")
    scales = [500, 1000, 5000]
    
    import platform
    machine = f"{platform.system()} {platform.machine()} ({platform.processor()})"
    print(f"Machine Context: {machine}\n")

    for size in scales:
        store = {}
        for i in range(size):
            fid = str(uuid.uuid4())
            store[f"concept_{i}"] = {
                "fact_id": fid,
                "answer": "Simulated real world fact size text. " * 3,
                "questions": f"Question {i}",
                "section_id": "key_stakeholders" if i % 2 == 0 else "proposed_solution",
                "section": "Key Stakeholders" if i % 2 == 0 else "Proposed Solution",
                "iteration": i // 100,
                "round": i % 100,
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
        
        iterations = 100
        latencies = []
        for _ in range(iterations):
            state_copy = state.copy()
            t0 = time.perf_counter()
            rebuild_mirror_node(state_copy)
            t1 = time.perf_counter()
            latencies.append((t1 - t0) * 1000)

        latencies.sort()
        p50 = latencies[len(latencies)//2]
        p95 = latencies[int(len(latencies)*0.95)]
        print(f"Scale: {size} facts")
        print(f"  P50 Latency: {p50:.4f} ms")
        print(f"  P95 Latency: {p95:.4f} ms")
        print(f"  Max Latency: {max(latencies):.4f} ms")
        print("-" * 50)

if __name__ == "__main__":
    run_stress_test()
