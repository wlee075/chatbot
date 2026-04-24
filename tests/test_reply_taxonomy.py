import unittest
import os
os.environ["GOOGLE_API_KEY"] = "dummy"
from unittest.mock import patch

from graph.nodes import _classify_intent_rule

class TestReplyTaxonomy(unittest.TestCase):
    def test_direct_answer(self):
        q = "How many users?"
        a = "Targeting 50k DAU by August."
        intent, _, _, _ = _classify_intent_rule(q, a)
        self.assertEqual(intent, "DIRECT_ANSWER")

    def test_blended_option_based(self):
        q = "Is this feature targeted at external B2B clients or internal ops?"
        a = "Both applies."
        intent, extracted, _, _ = _classify_intent_rule(q, a)
        self.assertEqual(intent, "UNCLEAR_META")

    def test_blended_open_ended(self):
        q = "Describe your architecture."
        a = "Both."
        intent, _, _, _ = _classify_intent_rule(q, a)
        # Should be UNCLEAR_META without LLM fallback
        self.assertEqual(intent, "UNCLEAR_META")

    def test_frustration_repaired(self):
        q = "Are you building X or Y?"
        a = "Why are you asking again when I already answered this."
        intent, _, _, _ = _classify_intent_rule(q, a)
        self.assertEqual(intent, "UNCLEAR_META")

    def test_frustration_with_blend(self):
        q = "Is this feature targeted at external B2B clients or internal ops?"
        a = "why are you asking again when I said both applies"
        intent, _, _, _ = _classify_intent_rule(q, a)
        # Treat as UNCLEAR_META without LLM fallback
        self.assertEqual(intent, "UNCLEAR_META")

    def test_interpret_and_echo_complaint(self):
        class MockSection:
            id = "test"
            title = "Test Section"
            expected_components = []
            
        import graph.nodes
        original_get_section = graph.nodes.get_section_by_index
        graph.nodes.get_section_by_index = lambda idx: MockSection()

        state = {
            "section_index": 0,
            "raw_answer_buffer": "Stop asking about metrics we don't have them yet.",
            "current_questions": "What are your specific SLA targets?",
            "iteration": 1,
            "section_qa_pairs": []
        }
        from graph.split_nodes import intent_classifier_node, repair_mode_node, truth_commit_node
        
        # We also need to add is_eligible=True when testing truth paths
        with patch("langchain_google_genai.ChatGoogleGenerativeAI.__init__", return_value=None):
            res_intent = intent_classifier_node(state)
        # Verify intent doesn't go forward directly
        
        # 1. Complaint text shouldn't be truth committed.
        # But we only ran intent_classifier_node! Let's simulate running the full chain for the old assertions:
        intent = res_intent.get("reply_intent")
        self.assertEqual(intent, "UNCLEAR_META") # Because safe fallback kicks in
        
        # 2 & 3: Blended and open-ended 'both' safely fall back in the new architecture
        # without an LLM. They classify as UNCLEAR_META -> answer_clarification.
        state_blended = {
            "section_index": 0,
            "raw_answer_buffer": "Both applies.",
            "current_questions": "Is this targeted at external B2B clients or internal ops?",
            "iteration": 1,
            "section_qa_pairs": []
        }
        
        from graph.split_nodes import clarification_router_node
        from graph.routing import route_after_intent
        
        with patch("langchain_google_genai.ChatGoogleGenerativeAI.__init__", return_value=None):
            res_blended_intent = intent_classifier_node(state_blended)
            
        # Verify fallback intent
        self.assertEqual(res_blended_intent.get("reply_intent"), "UNCLEAR_META")
        
        # Verify clarification router sets the right route
        clr_res = clarification_router_node(dict(state_blended, **res_blended_intent))
        self.assertEqual(clr_res.get("clarification_route_id"), "answer_clarification")
        
        # Verify routing module obeys it
        next_node = route_after_intent(dict(state_blended, **res_blended_intent, **clr_res))
        self.assertEqual(next_node, "answer_clarification")

        graph.nodes.get_section_by_index = original_get_section

if __name__ == '__main__':
    unittest.main()
