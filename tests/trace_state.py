from graph.builder import build_graph
from langgraph.checkpoint.memory import MemorySaver
from langchain_core.runnables import RunnableConfig
from pprint import pprint

memory = MemorySaver()
graph = build_graph(memory)
config = {"configurable": {"thread_id": "test_thread"}}

# Start graph
print("Starting graph...")
res = graph.invoke({}, config)

# Wait for first message interrupt
from langgraph.types import Command
print("Submitting first message...")
graph.invoke(Command(resume="This is the first message."), config)

# Let graph run through questions. Eventually it interrupts for user answer.
state = graph.get_state(config)
chat_history = state.values.get("chat_history", [])
print(f"Chat history length: {len(chat_history)}")

if len(chat_history) > 0:
    msg_id = chat_history[0].get("msg_id")
    print(f"Replying to {msg_id}...")
    
    # Force interrupt by advancing state if needed, or wait until interrupted
    state = graph.get_state(config)
    if state.next:
        reply_payload = {
            "event_type": "REPLY_TO_MESSAGE",
            "target_message_id": msg_id,
            "target_content": chat_history[0].get("content", ""),
            "content": "This is a reply!"
        }
        
        graph.invoke(Command(resume={"user_input": {"text": "This is a reply!"}, "pending_event": reply_payload}), config)
        
        # Check chat history again!
        new_state = graph.get_state(config)
        new_hist = new_state.values.get("chat_history", [])
        print("\n\n--- LATEST MESSAGE ---")
        pprint(new_hist[-1])
        print("----------------------")
    else:
        print("Graph did not interrupt for answer.")
