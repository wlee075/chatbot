import os
import sys
import unittest
from unittest.mock import patch
from pprint import pprint

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from graph.builder import build_graph
from langgraph.checkpoint.memory import MemorySaver
from graph.nodes import generate_questions_node

class TestE2EFallbackIncident(unittest.TestCase):
    def test_exact_business_problem_e2e_test(self):
        payload = "it is troublesome and time consuming so we are trying to reduce manual processing"
        state = {
            "thread_id": "e2e_test_thread",
            "run_id": "run_test",
            "section_index": 0,
            "raw_answer_buffer": payload,
            "chat_history": []
        }
        
        try:
            graph = build_graph(MemorySaver())
            res = graph.invoke(state, {"configurable": {"thread_id": "test"}})
            self.assertTrue(True)
        except Exception as e:
            self.fail(f"Graph raised exception: {e}")

    @patch("graph.nodes.llm_invoke")
    def test_generate_questions_raw_string_response_test(self, mock_invoke):
        mock_invoke.return_value = "RAW STRING FALLBACK"
        state = {
            "section_index": 0,
            "confirmed_qa_store": {},
            "context_doc": "test",
            "thread_id": "t1",
            "run_id": "r1"
        }
        res = generate_questions_node(state)
        self.assertTrue("chat_history" in res)
        self.assertIn("RAW STRING FALLBACK", str(res["chat_history"]))

    @patch("graph.nodes.llm_invoke")
    def test_generate_questions_complex_business_problem_test(self, mock_invoke):
        mock_invoke.return_value = "It is troublesome and time consuming"
        state = {
            "section_index": 0,
            "confirmed_qa_store": {},
            "context_doc": "it is troublesome and time consuming so we are trying to reduce manual processing, previously we have been retrieving the pdf manually for every transaction and its very troublesome. the specific mailbox is a group mailbox, the person in the group will forward the pdf to our client",
            "thread_id": "t1",
            "run_id": "r1"
        }
        res = generate_questions_node(state)
        self.assertTrue(isinstance(res, dict))
        self.assertIn("fallback_", str(res))

if __name__ == "__main__":
    unittest.main()
