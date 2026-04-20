import unittest
import os
import sys
from unittest.mock import patch

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from graph.nodes import generate_questions_node

class TestInputSensitivity(unittest.TestCase):
    def setUp(self):
        self.state = {
            "section_index": 0,
            "confirmed_qa_store": {},
            "thread_id": "test_thread",
            "run_id": "test_run"
        }

    def _run_with_context(self, context: str):
        state = self.state.copy()
        state["context_doc"] = context
        return generate_questions_node(state)

    @patch("graph.nodes.llm_invoke")
    def test_short_input(self, mock_invoke):
        mock_invoke.return_value = {"question_id": "q1", "question_text": "Q?", "subparts": ["x"]}
        res = self._run_with_context("Need DB")
        self.assertTrue(isinstance(res, dict))

    @patch("graph.nodes.llm_invoke")
    def test_long_input(self, mock_invoke):
        mock_invoke.return_value = {"question_id": "q2", "question_text": "Q?", "subparts": ["y"]}
        res = self._run_with_context("A" * 5000)
        self.assertTrue(isinstance(res, dict))

    @patch("graph.nodes.llm_invoke")
    def test_complaint_heavy_input(self, mock_invoke):
        mock_invoke.return_value = "RAW STRING FALLBACK"
        res = self._run_with_context("This system is awful, everything crashes, I hate manual work!")
        # Asserts our safe degradation fallback is active when parser returns string
        history_text = str(res["chat_history"])
        self.assertIn("RAW STRING FALLBACK", history_text)
        
    @patch("graph.nodes.llm_invoke")
    def test_multilingual_input(self, mock_invoke):
        mock_invoke.return_value = {"question_id": "q3", "question_text": "¿Qué?", "subparts": ["z"]}
        res = self._run_with_context("Necesito una base de datos")
        self.assertTrue(isinstance(res, dict))

if __name__ == "__main__":
    unittest.main()
