import sys
import os

# Ensure graph and prompts are importable
sys.path.insert(0, os.path.abspath('.'))

from graph.builder import build_graph
from langgraph.types import Command

os.environ["GOOGLE_API_KEY"] = "dummy"

g = build_graph()

# We want to see what happens after intent_classifier returns DIRECT_CLARIFICATION_QUESTION
payload = {
    "event_type": "ANSWER",
    "content": "What do you mean by that?"
}

state = {
    "section_index": 0,
    "remaining_subparts": ["Database type"],
    "current_questions": "What database?",
    "chat_history": [],
    "reply_intent": "DIRECT_CLARIFICATION_QUESTION"
}

print("Running graph from intent_classifier_node...")
for chunk, metadata in g.stream(state, stream_mode="messages"):
    node = metadata.get("langgraph_node")
    if node:
        print(f"NODE EXECUTED: {node}")
