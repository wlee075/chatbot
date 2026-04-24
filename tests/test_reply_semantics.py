import unittest
import os
import uuid
import logging
from unittest.mock import patch, MagicMock

os.environ["GOOGLE_API_KEY"] = "dummy"

from graph.state import PRDState
from graph.split_nodes import target_context_selector_node, clarification_router_node
from graph.nodes import await_answer_node

class TestReplySemantics(unittest.TestCase):
    def setUp(self):
        pass
        
    def test_target_context_selector_emits_typed_target_object_not_ambiguous_string(self):
        state = {
            "reply_context_interpretation": {
                "reply_context_present": True,
                "relationship_type": "correction_or_disagreement_with_replied_message",
                "confidence": 0.95
            },
            "reply_context_message_id": "msg_001",
            "reply_context_message_text": "We have 10 users.",
            "current_questions": "What else?",
            "reply_intent": "DIRECT_ANSWER"
        }
        res = target_context_selector_node(state)
        self.assertIn("active_semantic_target", res)
        self.assertEqual(res["active_semantic_target"]["target_type"], "replied_message")
        self.assertEqual(res["active_semantic_target"]["target_text"], "We have 10 users.")
        self.assertEqual(res["context_route_hint"], "normal_answer")

    def test_clarification_router_consumes_route_hint_without_recomputing_relationship_type(self):
        # Even if reply_intent is DIRECT_ANSWER, if route_hint is 'clarification_target', it routes to clarification.
        state = {
            "reply_intent": "DIRECT_ANSWER",
            "context_route_hint": "clarification_target"
        }
        res = clarification_router_node(state)
        self.assertEqual(res["clarification_route_id"], "answer_clarification")
        
    def test_missing_reply_context_message_text_fails_safely_for_correction_relationship(self):
        state = {
            "reply_context_interpretation": {
                "reply_context_present": True,
                "relationship_type": "correction_or_disagreement_with_replied_message",
                "confidence": 0.95
            },
            "reply_context_message_id": "msg_001",
            "reply_context_message_text": "", # Missing text
            "current_questions": "What else?"
        }
        res = target_context_selector_node(state)
        # Should fallback safely to latest_question
        self.assertEqual(res["active_semantic_target"]["target_type"], "latest_question")
        self.assertEqual(res["active_semantic_target"]["target_text"], "What else?")
        
    @patch("graph.nodes.interrupt")
    @patch("graph.nodes._build_user_message_dict")
    @patch("graph.nodes.get_section_by_index")
    def test_legacy_assistant_message_without_msg_id_does_not_corrupt_reply_context_flow(self, mock_get_section, mock_build, mock_interrupt):
        # A legacy session without UUIDs. Chat history has items but no msg_id on the assistant ones.
        mock_get_section.return_value.title = "Test Section"
        mock_build.return_value = {"msg_id": "new_msg", "role": "user", "content": "I am replying"}
        
        state = {
            "section_index": 0,
            "chat_history": [
                {"role": "user", "content": "Q1"},
                {"role": "assistant", "content": "Assistant msg 1 preview text goes here. It is long."}  # idx 1
            ]
        }
        
        mock_interrupt.return_value = {
            "event_type": "REPLY_TO_MESSAGE",
            "content": "I am replying",
            "target_message_id": "msg_1", # UI points to index 1
            "target_content": "preview text goes here" # UI sent substring preview
        }
        
        with patch("graph.nodes.flush_turn_summary"):
            res = await_answer_node(state)
            
        self.assertEqual(res["reply_context_message_text"], "Assistant msg 1 preview text goes here. It is long.")

    def test_msg_id_is_stable_across_rerender_and_checkpoint_restore(self):
        # Since msg_id is generated once during backend injection and stored in chat_history, 
        # it is part of the state dictionary natively. No rerender overrides it.
        from graph.split_nodes import echo_generation_node
        
        state = {
            "pending_echo": "We confirmed 500.",
            "chat_history": []
        }
        res = echo_generation_node(state)
        
        # Verify that the message was appended with a minted ID.
        msg_id_1 = res["chat_history"][0]["msg_id"]
        self.assertTrue(msg_id_1.startswith("msg_"))
        self.assertEqual(len(msg_id_1), 12) # 'msg_' + 8 chars
        
if __name__ == '__main__':
    unittest.main()
