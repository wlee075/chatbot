import sys
import os
import unittest
from unittest.mock import MagicMock, patch

# Set dummy API key to satisfy Pydantic/LangChain validation on import
os.environ["GOOGLE_API_KEY"] = "fake-key-for-testing"

# Mock the entire langchain_google_genai module before nodes.py can import it
sys.modules["langchain_google_genai"] = MagicMock()
sys.modules["langgraph"] = MagicMock()
sys.modules["langgraph.types"] = MagicMock()

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from graph.nodes import generate_questions_node, interpret_and_echo_node, handle_tagged_event_node
from graph.state import PRDState
from utils.telemetry import log_integrity_failure
from utils.validator import IntegrityValidator

class Phase1Validation(unittest.TestCase):
    def setUp(self):
        self.base_state = {
            "section_index": 2, # Key Stakeholders
            "iteration": 0,
            "confirmed_qa_store": {},
            "section_qa_pairs": [],
            "current_questions": "Who are the stakeholders?",
            "current_question_object": {"question_id": "q1", "question_text": "...", "subparts": ["list stakeholders", "identify roles"]},
            "remaining_subparts": ["list stakeholders", "identify roles"],
            "store_version": 0,
            "thread_id": "test_thread",
            "run_id": "test_run"
        }

    @patch("graph.nodes.llm_invoke")
    def test_suppression_regression_full(self, mock_llm):
        """Test that full overlap suppresses the question."""
        state = self.base_state.copy()
        state["confirmed_qa_store"] = {
            "stakeholders:iter_0:round_1": {
                "section_id": "key_stakeholders",
                "resolved_subparts": ["list stakeholders", "identify roles"]
            }
        }
        state["iteration"] = 1
        
        mock_llm.return_value = {
            "question_id": "q2",
            "question_text": "Please list the stakeholders and roles again.",
            "subparts": ["list stakeholders", "identify roles"]
        }
        
        result = generate_questions_node(state)
        self.assertIn("move on", result["chat_history"][0]["content"])
        self.assertEqual(result["current_questions"], "I have all the details I need for this section. Let's move on.")
        self.assertEqual(result["remaining_subparts"], [])

    @patch("graph.nodes.llm_invoke")
    def test_suppression_regression_partial(self, mock_llm):
        """Test that partial overlap filters subparts."""
        state = self.base_state.copy()
        state["confirmed_qa_store"] = {
            "stakeholders:iter_0:round_1": {
                "section_id": "key_stakeholders",
                "resolved_subparts": ["list stakeholders"]
            }
        }
        
        mock_llm.return_value = {
            "question_id": "q2",
            "question_text": "Who are the stakeholders and their roles?",
            "subparts": ["list stakeholders", "identify roles"]
        }
        
        result = generate_questions_node(state)
        # Should filter out "list stakeholders"
        self.assertEqual(result["remaining_subparts"], ["identify roles"])

    @patch("utils.validator.log_integrity_failure")
    def test_integrity_fault_injection_duplicate(self, mock_log):
        """Fault injection: duplicate write of same subparts."""
        store = {
            "key1": {
                "section_id": "headliner",
                "resolved_subparts": ["part1"]
            }
        }
        update = {
            "key2": {
                "section_id": "headliner",
                "resolved_subparts": ["part1"],
                "event_type": "TAG_MESSAGE_AS_TRUTH"
            }
        }
        
        IntegrityValidator.validate_mutation(
            thread_id="t1", run_id="r1", node_name="test",
            store=store, update=update, section_id="headliner"
        )
        mock_log.assert_called_once()
        self.assertEqual(mock_log.call_args[1]["failure_type"], "DUPLICATE_ACTIVE_FACT")

    @patch("utils.validator.log_integrity_failure")
    def test_integrity_fault_injection_invalid_section(self, mock_log):
        """Fault injection: invalid section ID."""
        IntegrityValidator.validate_mutation(
            thread_id="t1", run_id="r1", node_name="test",
            store={}, update={"k1": {}}, section_id="invalid_id"
        )
        mock_log.assert_called_once()
        self.assertEqual(mock_log.call_args[1]["failure_type"], "SEMANTIC_CORRUPTION")

    def test_rebuild_mirror_idempotency(self):
        """Test that rebuild_mirror_node is deterministic and idempotent."""
        from graph.nodes import rebuild_mirror_node
        state = self.base_state.copy()
        state["confirmed_qa_store"] = {
            "key1": {"section_id": "key_stakeholders", "answer": "Alice", "questions": "Who?", "round": 1, "iteration": 0},
            "key2": {"section_id": "key_stakeholders", "answer": "Bob", "questions": "Roles?", "round": 2, "iteration": 0},
            "key3": {"section_id": "headliner", "answer": "Wrong", "round": 1} # Different section
        }
        
        result = rebuild_mirror_node(state)
        # Should only contain 2 pairs for key_stakeholders, sorted by round
        self.assertEqual(len(result["section_qa_pairs"]), 2)
        self.assertEqual(result["section_qa_pairs"][0]["answer"], "Alice")
        self.assertEqual(result["section_qa_pairs"][1]["answer"], "Bob")
        self.assertEqual(result["rebuild_count"], 1)

    def test_version_guard_reflect_stale(self):
        """Test that reflect_node blocks stale drafts."""
        from graph.nodes import reflect_node
        state = self.base_state.copy()
        state["store_version"] = 5
        state["section_draft_meta"] = {
            "key_stakeholders": {"draft_version": 4} # STALE
        }
        
        result = reflect_node(state)
        self.assertEqual(result["triage_decision"], "TRIAGE: STALE_DRAFT_REGEN")
        self.assertEqual(result["verdict"], "REWORK")

    def test_hard_link_correction_success(self):
        """Test that correction links exactly by message_id."""
        from graph.nodes import handle_tagged_event_node
        state = self.base_state.copy()
        state["confirmed_qa_store"] = {
            "old_key": {"source_message_id": "msg_1", "answer": "Old", "section_id": "key_stakeholders"}
        }
        state["pending_event"] = {
            "event_type": "CORRECT_MESSAGE",
            "target_message_id": "msg_1",
            "content": "New"
        }
        
        result = handle_tagged_event_node(state)
        # Check that corrects_key was found
        store_update = result["confirmed_qa_store"]
        update_val = list(store_update.values())[0]
        self.assertEqual(update_val["corrects_key"], "old_key")
        self.assertEqual(result["correction_stats"]["success"], 1)

    def test_false_active_suppression_adversarial(self):
        """Test that contradiction_flagged facts are NOT suppressed."""
        state = self.base_state.copy()
        state["confirmed_qa_store"] = {
            "key1": {
                "section_id": "key_stakeholders",
                "resolved_subparts": ["list stakeholders"],
                "contradiction_flagged": True # ADVERSARIAL: flagged as wrong
            }
        }
        
        # Mock LLM returning "list stakeholders" as a subpart of the new question
        with patch("graph.nodes.llm_invoke") as mock_llm:
            mock_llm.return_value = {
                "question_id": "q2",
                "question_text": "Who are the stakeholders?",
                "subparts": ["list stakeholders"]
            }
            result = generate_questions_node(state)
            # Should NOT suppress because the fact is flagged
            self.assertEqual(result["remaining_subparts"], ["list stakeholders"])

if __name__ == "__main__":
    unittest.main()
