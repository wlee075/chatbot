from streamlit.testing.v1 import AppTest
from unittest.mock import patch, MagicMock

print("Initializing AppTest...")
try:
    at = AppTest.from_file("app.py", default_timeout=5).run()
    
    # Emulate the starting graph state slightly
    # Wait, the app.py relies on actual SQLite/memory backend inside builder!
    # If we type text and submit, the graph starts!
    st_chat = at.chat_input[0]
    st_chat.set_value("Initialize starting message").submit()
    at.run()
    
    # Now we have 1 user message
    # Let's hit the reply button on msg_0 (or just click the actual button)
    buttons = at.button
    reply_btn = None
    for b in buttons:
        if "Reply" in str(b.label):
            reply_btn = b
            break

    if reply_btn:
        print(f"Found Reply Button: {reply_btn.key}")
        reply_btn.click().run()
        print("Active Reference set in Session State:")
        print(at.session_state.get("active_reference", "NONE!"))
        
        # Submit the new message
        st_chat.set_value("This is my reply message").submit()
        at.run()
        
        # Check chat history
        chat_hist = at.session_state.get("_cached_history", [])  # or where is it stored?
        sv = at.session_state  # Maybe sv is stored in session_state?
        # Actually I can just dump at.chat_message[1].markdown inside.
        print("\n--- Output Trace ---")
        for cm in at.chat_message:
            print(f"CHAT MESSAGE (Role: {cm.name})")
            for e in cm.children:
                try:
                    if hasattr(e, "value"):
                        print(f"  [MARKDOWN]: {e.value}")
                except Exception:
                    print(f"  [E]: {e}")
        
    else:
        print("Reply button not found.")
except Exception as e:
    print(f"Error tracing: {e}")
