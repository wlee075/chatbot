import unittest
import sys
import os
from unittest.mock import patch, MagicMock

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from graph.nodes import generate_questions_node
from graph.state import PRDState

class TestStructuredFallbacks(unittest.TestCase):
    def setUp(self):
        self.state = {
            "section_index": 0,
            "confirmed_qa_store": {},
            "context_doc": "it is troublesome and time consuming so we are trying to reduce manual processing"
        }

    @patch("graph.nodes.llm_invoke")
    def test_free_text_business_problem_test(self, mock_invoke):
        # Mocks a successful outcome when handling a long business problem
        mock_invoke.return_value = {
            "question_id": "q1",
            "question_text": "What are the exact manual steps?",
            "subparts": ["process"]
        }
        res = generate_questions_node(self.state)
        self.assertIn("What are the exact manual steps?", str(res["chat_history"]))

    @patch("graph.nodes.llm_invoke")
    def test_string_payload_contract_test(self, mock_invoke):
        # Mocks the LLM failing structured parse and returning raw string
        mock_invoke.return_value = "This is a raw string, not a dictionary."
        res = generate_questions_node(self.state)
        history_text = str(res["chat_history"])
        self.assertIn("This is a raw string", history_text)

    def test_ui_error_masking_test(self):
        # Validates our UI exception block masking in Streamlit logic (pseudo-test)
        # Without mocking streamlit full cycle, we assert the string was added to app.py.
        with open("app.py", "r") as f:
            app_code = f.read()
            self.assertIn("We hit an unexpected snag parsing your request.", app_code)
            self.assertIn("traceback.format_exc()", app_code)

if __name__ == "__main__":
    unittest.main()
