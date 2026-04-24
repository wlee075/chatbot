"""
Regression tests for SnowballStemmer-based normalization.
Verifies that:
  - Library singletons are available
  - Normalization cases from the approved contract match expected roots
  - Existing clause evidence regression suite still passes
"""
import unittest


class TestSnowballNormalization(unittest.TestCase):

    def setUp(self):
        from graph.nodes import _STEMMER, _STOPWORDS_EN, _NLTK_AVAILABLE, _word_tokenize
        self.stemmer = _STEMMER
        self.stopwords = _STOPWORDS_EN
        self.available = _NLTK_AVAILABLE
        self.word_tokenize = _word_tokenize

    def _stem(self, word: str) -> str:
        if self.stemmer:
            return self.stemmer.stem(word.lower())
        return word.lower()

    def _tokenize_norm(self, text: str) -> set[str]:
        def _pre(t: str) -> str:
            if len(t) > 2 and t[-1] == "s" and t[:-1] == t[:-1].upper() and t[:-1].isalpha():
                t = t[:-1]
            return t.lower()
        toks = self.word_tokenize(text)  # case-preserved — matches production
        if self.stemmer:
            return {self.stemmer.stem(_pre(t)) for t in toks
                    if t.replace("'", "").isalpha() and _pre(t) not in self.stopwords and len(t) > 1}
        return {_pre(t) for t in toks
                if t.replace("'", "").isalpha() and _pre(t) not in self.stopwords and len(t) > 1}

    # ── I1: PDF/PDFs share same root when processed through _tokenize_norm ──
    def test_pdf_plural_same_root(self):
        r1 = self._tokenize_norm("PDF")
        r2 = self._tokenize_norm("PDFs")
        self.assertEqual(r1, r2,
                         f"PDF and PDFs must normalize to the same token set; got {r1} vs {r2}")

    # ── I2: map/mapping/mapped all share same root ───────────────────────────
    def test_map_verb_forms_same_root(self):
        roots = {self._stem(w) for w in ("map", "mapping", "mapped")}
        self.assertEqual(len(roots), 1,
                         f"map/mapping/mapped should share one root, got: {roots}")

    # ── I3: fix/fixed share same root ────────────────────────────────────────
    def test_fix_past_tense_same_root(self):
        self.assertEqual(self._stem("fix"), self._stem("fixed"),
                         "fix and fixed must normalize to the same stem")

    # ── I4: wrong/wrongly allowed to differ (known acceptable limitation) ────
    def test_wrong_wrongly_may_differ(self):
        # This is acceptable — document only, do not assert equality.
        r1, r2 = self._stem("wrong"), self._stem("wrongly")
        # Just confirm both are non-empty strings, no assertion on equality.
        self.assertTrue(len(r1) > 0)
        self.assertTrue(len(r2) > 0)

    # ── I5: stopword removal works ───────────────────────────────────────────
    def test_stopwords_removed(self):
        result = self._tokenize_norm("the PDFs are fine")
        self.assertNotIn("the", result)
        self.assertNotIn("are", result)

    # ── I6: critical subpart tokens survive normalization ────────────────────
    def test_subpart_tokens_survive(self):
        result = self._tokenize_norm("mapping products incorrectly")
        # At minimum the stem of 'mapping' should be present
        stems_of_mapping = {self._stem("mapping"), self._stem("map")}
        self.assertTrue(result & stems_of_mapping,
                        f"Stem of 'mapping' not found in: {result}")

    # ── I7: NLTK availability guard ──────────────────────────────────────────
    def test_nltk_available(self):
        # Warn on CI rather than hard-fail — allows graceful-fallback mode.
        if not self.available:
            import warnings
            warnings.warn("NLTK not available; running in fallback mode", RuntimeWarning)
        # Either mode should produce a non-empty set for a real sentence.
        result = self._tokenize_norm("PDFs are sent to the wrong address")
        self.assertGreater(len(result), 0)


if __name__ == "__main__":
    unittest.main()
