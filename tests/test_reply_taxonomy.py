import unittest
from graph.nodes import _classify_intent_rule, interpret_and_echo_node

class TestReplyTaxonomy(unittest.TestCase):
    def test_direct_answer(self):
        q = "How many users?"
        a = "Targeting 50k DAU by August."
        intent, _ = _classify_intent_rule(q, a)
        self.assertEqual(intent, "DIRECT_ANSWER")

    def test_blended_option_based(self):
        q = "Is this feature targeted at external B2B clients or internal ops?"
        a = "Both applies."
        intent, extracted = _classify_intent_rule(q, a)
        self.assertEqual(intent, "BLENDED")

    def test_blended_open_ended(self):
        q = "Describe your architecture."
        a = "Both."
        intent, _ = _classify_intent_rule(q, a)
        # Should be AMBIGUOUS because 'both' doesn't make sense here
        self.assertEqual(intent, "AMBIGUOUS")

    def test_frustration_repaired(self):
        q = "Are you building X or Y?"
        a = "Why are you asking again when I already answered this."
        intent, _ = _classify_intent_rule(q, a)
        self.assertEqual(intent, "COMPLAINT_OR_META")

    def test_frustration_with_blend(self):
        q = "Is this feature targeted at external B2B clients or internal ops?"
        a = "why are you asking again when I said both applies"
        intent, _ = _classify_intent_rule(q, a)
        # Treat as BLENDED if it contains 'both' and question has options
        self.assertEqual(intent, "BLENDED")

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
        res = interpret_and_echo_node(state)
        
        # 1. Complaint text never enters confirmed_qa_store
        self.assertNotIn("confirmed_qa_store", res)
        self.assertIn("chat_history", res)
        # Verify repair copy does not re-ask blindly
        self.assertTrue(any("You're right" in msg["content"] or "rephrase" in msg["content"] or "clarify" in msg["content"] for msg in res["chat_history"]))

        # 2. Blended answer advances flow when grounded by prior options
        state_blended = {
            "section_index": 0,
            "raw_answer_buffer": "Both applies.",
            "current_questions": "Is this targeted at external B2B clients or internal ops?",
            "iteration": 1,
            "section_qa_pairs": []
        }
        res_blended = interpret_and_echo_node(state_blended)
        self.assertIn("confirmed_qa_store", res_blended)
        self.assertEqual(res_blended["answer_confirmation_status"], "CONFIRMED")
        self.assertIn("Both options matter", list(res_blended["confirmed_qa_store"].values())[0]["answer"])

        # 3. Open-ended 'both' does not hallucinate missing options
        state_open_both = {
            "section_index": 0,
            "raw_answer_buffer": "both",
            "current_questions": "Describe your architecture.",
            "iteration": 1,
            "section_qa_pairs": []
        }
        res_open_both = interpret_and_echo_node(state_open_both)
        # Should be treated as AMBIGUOUS and trigger repair instead of storing fact
        self.assertNotIn("confirmed_qa_store", res_open_both)

        graph.nodes.get_section_by_index = original_get_section

if __name__ == '__main__':
    unittest.main()
