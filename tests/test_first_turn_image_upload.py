import os
import sys
from dotenv import load_dotenv

load_dotenv()

# Add the project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from graph.builder import build_graph
from langgraph.types import Command

def test_first_turn_image_upload_crash():
    graph = build_graph()
    thread_id = "test_first_turn_crash_5"
    config = {"configurable": {"thread_id": thread_id}}
    
    print("Starting graph...")
    state = graph.invoke({"input_disabled": False}, config)
    
    payload = {
        "event_type": "FILE_UPLOAD",
        "uploaded_files": [{
            "file_id": "file_123",
            "filename": "test.png",
            "mime_type": "image/png",
            "size_bytes": 1024,
            "bytes": b"fake_bytes"
        }]
    }
    
    print("\nResuming graph with first-turn file upload...")
    try:
        updated_state = graph.invoke(Command(resume=payload), config)
        print("Graph halted at node normally!")
    except Exception as e:
        print("\nCRASH DETECTED!")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_first_turn_image_upload_crash()
