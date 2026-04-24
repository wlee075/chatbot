import os
import sys

from streamlit.testing.v1 import AppTest

def run_streamlit_test():
    print("Running Streamlit test...")
    at = AppTest.from_file("app.py", default_timeout=30)
    at.run()
    
    # Enter the message
    at.chat_input[0].set_value("it is troublesome and time consuming so we are trying to reduce manual processing of these PDF files by forwarding them to the group mailbox").run()
    
    for err in at.error:
        print("UI ERROR Found:", err.value)

if __name__ == "__main__":
    run_streamlit_test()
