import unittest
import string
from unittest.mock import patch

from graph.nodes import _log_keyword_extraction_observability, _global_logged_messages

class TestKeywordExtractionObservability(unittest.TestCase):
    def setUp(self):
        # Clear the deduplication set before each test
        _global_logged_messages.clear()

    @patch('graph.nodes.log_event')
    def test_keyword_extraction_logs_structured_candidate_objects(self, mock_log_event):
        """Candidates must have consistent fields, not loose strings."""
        msg_id = "msg_test_1"
        _log_keyword_extraction_observability("We need a manual process for product mapping.", msg_id)
        
        self.assertTrue(mock_log_event.called)
        kwargs = mock_log_event.call_args.kwargs
        self.assertEqual(kwargs['event_type'], 'keyword_extraction_observability')
        
        # Check all_candidates_raw schema
        raw_candidates = kwargs['all_candidates_raw']
        self.assertTrue(len(raw_candidates) > 0)
        for cand in raw_candidates:
            self.assertIn("surface_text", cand)
            self.assertIn("normalized", cand)
            self.assertIn("type", cand)
            self.assertIn("start", cand)
            self.assertIn("end", cand)

        # Check deduped_candidates schema
        deduped = kwargs['deduped_candidates']
        for cand in deduped:
            self.assertIn("surface_text", cand)
            self.assertIn("normalized", cand)

        self.assertIn("candidate_pool_for_downstream", kwargs)

    @patch('graph.nodes.log_event')
    def test_empty_input_vs_low_information_input_differentiated(self, mock_log_event):
        """'' and 'hi' produce distinct observability meaning."""
        # 1. Empty string case
        _log_keyword_extraction_observability("", "msg_empty")
        empty_kwargs = mock_log_event.call_args.kwargs
        self.assertEqual(empty_kwargs['char_count'], 0)
        self.assertEqual(empty_kwargs['filtered_out'], [])
        self.assertEqual(empty_kwargs['all_candidates_raw'], [])
        
        # 2. Low-info case ('hi')
        _log_keyword_extraction_observability("hi", "msg_low_info")
        low_info_kwargs = mock_log_event.call_args.kwargs
        self.assertEqual(low_info_kwargs['char_count'], 2)
        # Assuming 'hi' is too short or low value, we should see it in filtered
        
        # They should behave differently!
        self.assertNotEqual(empty_kwargs, low_info_kwargs)

    @patch('graph.nodes.log_event')
    def test_no_double_logging_on_rebuild_or_retry(self, mock_log_event):
        """Same msg_id does not emit duplicate observability events."""
        msg_id = "msg_double"
        _log_keyword_extraction_observability("Initial ingestion", msg_id)
        self.assertEqual(mock_log_event.call_count, 1)
        
        # Call it again with same msg_id
        _log_keyword_extraction_observability("Initial ingestion", msg_id)
        self.assertEqual(mock_log_event.call_count, 1)  # Stays 1!

    @patch('graph.nodes._get_nlp')
    @patch('graph.nodes.log_event')
    def test_spacy_failure_degrades_safely(self, mock_log_event, mock_get_nlp):
        """Ingestion continues and log records extractor failure cleanly."""
        mock_get_nlp.return_value = None  # Simulate spacy failure
        msg_id = "msg_fail"
        
        _log_keyword_extraction_observability("This should not crash.", msg_id)
        
        self.assertTrue(mock_log_event.called)
        kwargs = mock_log_event.call_args.kwargs
        self.assertEqual(kwargs['extractor_name'], 'unavailable')
        self.assertEqual(kwargs['all_candidates_raw'], [])

    @patch('graph.nodes.log_event')
    def test_filtered_reason_present(self, mock_log_event):
        """Removed candidates always have explicit reason."""
        msg_id = "msg_reasons"
        # "a" is a stopword / too_short, "." is punctuation
        _log_keyword_extraction_observability("a.", msg_id)
        
        kwargs = mock_log_event.call_args.kwargs
        filtered = kwargs.get('filtered_out', [])
        
        # All filtered items must have a valid reason from the Enum strings
        valid_reasons = {"stopword", "too_short", "punctuation", "subsumed", "duplicate", "low_value", "truncated"}
        for f in filtered:
            self.assertIn("reason", f)
            self.assertIn(f["reason"], valid_reasons)

    @patch('graph.nodes.log_event')
    def test_domain_allowlist_preserves_short_terms(self, mock_log_event):
        msg_id = "msg_pdf"
        _log_keyword_extraction_observability("Upload the PDF.", msg_id)
        kwargs = mock_log_event.call_args.kwargs
        pool = kwargs.get('candidate_pool_for_downstream', [])
        self.assertIn("pdf", pool)

    @patch('graph.nodes.log_event')
    def test_low_value_chunks_hidden_from_compact_view(self, mock_log_event):
        msg_id = "msg_lowval"
        _log_keyword_extraction_observability("that its me the end goal", msg_id)
        kwargs = mock_log_event.call_args.kwargs
        filtered = kwargs.get('filtered_out', [])
        reasons = [c.get("reason") for c in filtered if c.get("normalized") in ["that", "its", "me"]]
        self.assertTrue(all(r in ["stopword", "low_value"] for r in reasons))

    @patch('graph.nodes.log_event')
    def test_keyword_audit_flags_truncation(self, mock_log_event):
        msg_id = "msg_trunc"
        _log_keyword_extraction_observability("check the mailbo", msg_id)
        kwargs = mock_log_event.call_args.kwargs
        filtered = kwargs.get('filtered_out', [])
        self.assertTrue(any(c.get("reason") == "truncated" and c.get("surface_text") == "mailbo" for c in filtered))

    @patch('graph.nodes.log_event')
    def test_domain_entity_ruler_detects_pdf_mailbox_prd(self, mock_log_event):
        msg_id = "msg_entities"
        _log_keyword_extraction_observability("We need a PDF and PRD from the group mailbox.", msg_id)
        kwargs = mock_log_event.call_args.kwargs
        entities = kwargs.get('entities_detected', [])
        entity_labels = {e["normalized"]: e["pos"] for e in entities}
        self.assertEqual(entity_labels.get("pdf"), "FILE_TYPE")
        self.assertEqual(entity_labels.get("prd"), "FILE_TYPE")
        self.assertEqual(entity_labels.get("group mailbox"), "EMAIL_GROUP")

    @patch('graph.nodes.log_event')
    def test_entity_keyword_merge_no_duplicates(self, mock_log_event):
        msg_id = "msg_nodup"
        _log_keyword_extraction_observability("PDF file", msg_id)
        kwargs = mock_log_event.call_args.kwargs
        pool = kwargs.get('candidate_pool_for_downstream', [])
        self.assertEqual(pool.count("pdf"), 1)

    @patch('graph.nodes.log_event')
    def test_entity_detected_outranks_generic_keyword(self, mock_log_event):
        msg_id = "msg_outrank"
        _log_keyword_extraction_observability("SAP system pipeline", msg_id)
        kwargs = mock_log_event.call_args.kwargs
        deduped = kwargs.get('deduped_candidates', [])
        # Entity (SAP) should appear first in deduped due to tuple sorting logic
        types = [c["type"] for c in deduped]
        self.assertTrue(len(types) > 0 and types[0] == "entity")

if __name__ == '__main__':
    unittest.main()
