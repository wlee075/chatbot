import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from graph.builder import build_graph
from graph.state import PRDState

def reproduce_bug():
    app = build_graph()
    
    # Simulating a new session where user writes their first business problem
    state = {
        "thread_id": "test_crash",
        "run_id": "test_run",
        "chat_history": [
             {"role": "user", "content": "it is troublesome and time consuming so we are trying to reduce manual processing of these PDF files by forwarding them to the group mailbox", "id": "msg_001"}
        ],
        "pending_event": {"event_type": "FIRST_MESSAGE"},
    }
    
    try:
        print("Invoking graph via stream...")
        from langgraph.types import Command
        for chunk, metadata in app.stream(Command(resume=state["chat_history"]), {"configurable": {"thread_id": "test_crash"}}, stream_mode="messages"):
            pass
        print("Success! Did not crash.")
    except Exception as e:
        import traceback
        print("CRASH DETECTED!")
        traceback.print_exc()

if __name__ == "__main__":
    reproduce_bug()
