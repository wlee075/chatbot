import sys
import os
import uuid
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.abspath('.'))

from graph.builder import build_graph
from langgraph.types import Command
from pydantic import BaseModel

os.environ["GOOGLE_API_KEY"] = "dummy"

def reset_graph(thread_id):
    from langgraph.checkpoint.memory import MemorySaver
    g = build_graph(MemorySaver())
    config = {"configurable": {"thread_id": thread_id}}
    
    from app import _build_initial_state
    try:
        init_state = _build_initial_state([])
        init_state["framing_mode"] = "clear"
        init_state["phase"] = "elicitation"
        g.invoke(init_state, config)
    except Exception:
        pass
    return g, config

class MockRawVisualObservation(BaseModel):
    high_level_description: str
    distinct_visible_elements: list[str]
    unreadable_or_uncertain_areas: list[str]

def run_multimodal(thread_id, payload, message):
    with patch('graph.split_nodes._get_llm') as mock_get_llm:
        # Mock LLM for image description
        mock_llm_instance = MagicMock()
        mock_llm_instance.with_structured_output.return_value.invoke.return_value = MockRawVisualObservation(
            high_level_description="A test diagram outline.",
            distinct_visible_elements=[],
            unreadable_or_uncertain_areas=[]
        )
        mock_get_llm.return_value = mock_llm_instance

        print(f"\n--- Running Trace: {message} ---")
        g, config = reset_graph(thread_id)
        try:
            trace = []
            for update in g.stream(Command(resume=payload), config):
                for node_name in update:
                    trace.append(node_name)
                    print(f"NODE EXECUTED: {node_name}")
        except Exception as e:
            print(f"Trace stopped early: {e}")

if __name__ == "__main__":
    payload_image_only = {
        "event_type": "ANSWER",
        "content": "",
        "uploaded_files": [{"file_id": "1", "filename": "test.png", "mime_type": "image/png", "bytes": b"mock", "size_bytes": 100}]
    }
    run_multimodal("thread_image_only", payload_image_only, "Image-Only Submit")
