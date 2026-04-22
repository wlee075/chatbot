import time
from streamlit.testing.v1 import AppTest

def run_trace():
    print("Initializing AppTest...")
    at = AppTest.from_file("app.py", default_timeout=15).run()
    
    st_chat = at.chat_input[0]
    st_chat.set_value("tell me what is wrong").submit()
    at.run()
    
    # We should have a user message now.
    buttons = at.button
    reply_btn = None
    for b in buttons:
        if "Reply" in str(b.label):
            reply_btn = b
            break
            
    if not reply_btn:
        print("Failed to find reply button.")
        return
        
    print(f"Clicking reply button: {reply_btn.key}")
    reply_btn.click().run()
    
    st_chat.set_value("I mean what is right").submit()
    at.run()
    
    print("\n--- DOM OUTPUT ---")
    for cm in at.chat_message:
        print(f"[{cm.name}]")
        for e in cm.children:
            try:
                if e.type == "markdown":
                    print(f"  Markdown: {e.value}")
                elif e.type == "caption":
                    print(f"  Caption: {e.value}")
            except Exception:
                pass
                
    print("\n--- CHAT HISTORY DICTS ---")
    sv = at.session_state.get("_global_state_cache", {})
    if sv:
        hist = sv.get("chat_history", [])
        for i, h in enumerate(hist):
            if h.get("role") == "user":
                print(f"U{i}: id={h.get('msg_id')} reply_id={h.get('reply_to_message_id')} snippet='{h.get('reply_to_content_snippet')}' text='{h.get('content')}'")

if __name__ == "__main__":
    run_trace()
