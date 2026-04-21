import unittest
import os
os.environ["GOOGLE_API_KEY"] = "dummy"

from graph.state import PRDState
from graph.nodes import await_answer_node

class TestReplyContext(unittest.TestCase):
    def setUp(self):
        self.mock_chat_history = [
            {"msg_id": "msg_001", "role": "user", "content": "How many DAU do we expect?"},
            {"msg_id": "msg_002", "role": "assistant", "content": "We anticipate 50k DAU by August."},
        ]

        self.base_state = {
            "section_index": 0,
            "chat_history": self.mock_chat_history,
            # We mock the interrupt using unittest.mock, but wait, await_answer_node uses `interrupt()`.
            # Let's patch `interrupt` in graph.nodes!
        }

    @unittest.mock.patch("graph.nodes.interrupt")
    @unittest.mock.patch("graph.nodes._build_user_message_dict")
    @unittest.mock.patch("graph.nodes.get_section_by_index")
    def test_reply_context_uses_message_id_lookup_not_ui_target_content(self, mock_get_section, mock_build_user_msg, mock_interrupt):
        # Setup mock dependencies
        mock_get_section.return_value.title = "Test Section"
        mock_build_user_msg.return_value = {"msg_id": "new_msg_id", "content": "Here is my reply", "role": "user"}
        
        # Simulate UI sending an event with a completely bogus target_content
        mock_interrupt.return_value = {
            "event_type": "REPLY_TO_MESSAGE",
            "content": "Here is my reply",
            "target_message_id": "msg_002",
            "target_content": "BOGUS UI CONTENT" # This should be totally ignored
        }

        with unittest.mock.patch("graph.nodes.flush_turn_summary"):
            result = await_answer_node(self.base_state)
        
        # Verify the backend ignored 'BOGUS UI CONTENT' and pulled from chat_history
        self.assertEqual(result["reply_context_message_id"], "msg_002")
        self.assertEqual(result["reply_context_message_text"], "We anticipate 50k DAU by August.")

    @unittest.mock.patch("graph.nodes.interrupt")
    @unittest.mock.patch("graph.nodes._build_user_message_dict")
    @unittest.mock.patch("graph.nodes.get_section_by_index")
    def test_reply_context_ignores_large_rendered_html_blob_from_ui(self, mock_get_section, mock_build_user_msg, mock_interrupt):
        mock_get_section.return_value.title = "Test Section"
        mock_build_user_msg.return_value = {"msg_id": "new_msg_id", "content": "My text", "role": "user"}
        
        # Massive HTML blob
        huge_blob = "<span class='cite-chip'>We anticipate...</span>" * 100
        mock_interrupt.return_value = {
            "event_type": "REPLY_TO_MESSAGE",
            "content": "My text",
            "target_message_id": "msg_002",
            "target_content": huge_blob
        }

        with unittest.mock.patch("graph.nodes.flush_turn_summary"):
            result = await_answer_node(self.base_state)
            
        self.assertEqual(result["reply_context_message_text"], "We anticipate 50k DAU by August.")

    @unittest.mock.patch("graph.nodes.interrupt")
    @unittest.mock.patch("graph.nodes._build_user_message_dict")
    @unittest.mock.patch("graph.nodes.get_section_by_index")
    def test_reply_context_lookup_fails_safely_when_message_id_missing(self, mock_get_section, mock_build_user_msg, mock_interrupt):
        mock_get_section.return_value.title = "Test Section"
        mock_build_user_msg.return_value = {"msg_id": "new_msg", "content": "test", "role": "user"}
        
        # Supply an ID that DOES NOT EXIST in chat history
        mock_interrupt.return_value = {
            "event_type": "REPLY_TO_MESSAGE",
            "content": "test",
            "target_message_id": "msg_999",
            "target_content": "Some preview"
        }

        with unittest.mock.patch("graph.nodes.flush_turn_summary"):
            result = await_answer_node(self.base_state)
            
        # It must safely fail (empty string), preventing hallucinated state
        self.assertEqual(result["reply_context_message_id"], "")
        self.assertEqual(result["reply_context_message_text"], "")

    @unittest.mock.patch("graph.nodes.interrupt")
    @unittest.mock.patch("graph.nodes._build_user_message_dict")
    @unittest.mock.patch("graph.nodes.get_section_by_index")
    def test_reply_context_text_is_normalized_after_backend_lookup(self, mock_get_section, mock_build_user_msg, mock_interrupt):
        mock_get_section.return_value.title = "Test Section"
        mock_build_user_msg.return_value = {"msg_id": "new_msg", "content": "test", "role": "user"}
        
        # Test pulling a message that itself was plain text (like 'msg_001')
        mock_interrupt.return_value = {
            "event_type": "REPLY_TO_MESSAGE",
            "content": "test",
            "target_message_id": "msg_001",
            "target_content": ""
        }

        with unittest.mock.patch("graph.nodes.flush_turn_summary"):
            result = await_answer_node(self.base_state)
            
        self.assertEqual(result["reply_context_message_text"], "How many DAU do we expect?")

    @unittest.mock.patch("graph.nodes.interrupt")
    @unittest.mock.patch("graph.nodes._build_user_message_dict")
    @unittest.mock.patch("graph.nodes.get_section_by_index")
    def test_reply_submit_does_not_duplicate_full_message_block_in_ui(self, mock_get_section, mock_build_user_msg, mock_interrupt):
        # This asserts that given a massive payload, the state text is completely sanitized and isn't nested.
        mock_get_section.return_value.title = "Test Section"
        mock_build_user_msg.return_value = {"msg_id": "new_msg", "content": "test", "role": "user"}
        mock_interrupt.return_value = {
            "event_type": "REPLY_TO_MESSAGE",
            "content": "test",
            "target_message_id": "msg_001",
            "target_content": "<div class='huge-ui-block'>How many DAU do we expect?</div>" * 50
        }

        with unittest.mock.patch("graph.nodes.flush_turn_summary"):
            result = await_answer_node(self.base_state)
            
        # UI blob is dropped, state returns pure string without HTML duplication
        self.assertEqual(result["reply_context_message_text"], "How many DAU do we expect?")

if __name__ == '__main__':
    unittest.main()
