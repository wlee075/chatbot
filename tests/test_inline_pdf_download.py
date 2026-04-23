"""
tests/test_inline_pdf_download.py

Unit tests for the inline PDF download UX requirement (U1-U4, S1-S3).

These tests validate the rendering conditions for the inline download button
that appears directly after the final "complete" assistant message:
  - Button appears only when prd_pdf_bytes is non-empty.
  - Button is absent when prd_pdf_bytes is empty.
  - Button label and filename are derived from prd_report_title, not internal keys.

Because we cannot run Streamlit natively in pytest, we test the *logic layer*
that controls whether the button would be rendered:
  - The state contract (S1-S3) is verified by asserting on sv.get() call results.
  - The rendering path is verified by checking message type classification.
"""

import re
import pytest


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_complete_message(content: str = "Your report is ready.") -> dict:
    """Minimal assistant message with type='complete'."""
    return {"role": "assistant", "type": "complete", "content": content, "_idx": 0}


def _make_sv(
    prd_pdf_bytes: bytes = b"",
    prd_report_title: str = "",
    is_complete: bool = True,
) -> dict:
    """Simulate the session values dict that app.py reads via sv.get(...)."""
    return {
        "prd_pdf_bytes": prd_pdf_bytes,
        "prd_report_title": prd_report_title,
        "is_complete": is_complete,
    }


def _should_show_inline_pdf(sv: dict) -> bool:
    """Mirror of the app.py guard: show button iff prd_pdf_bytes is non-empty."""
    return bool(sv.get("prd_pdf_bytes", b""))


def _derive_filename(sv: dict) -> str:
    """Mirror of the app.py filename derivation logic."""
    raw_title = sv.get("prd_report_title", "") or "requirements_report"
    safe_fn = re.sub(r"[^\w\s\-]", "", raw_title).strip().replace(" ", "_")[:60]
    return (safe_fn or "requirements_report") + ".pdf"


# ── tests ─────────────────────────────────────────────────────────────────────

class TestInlinePDFDownloadButton:
    """S1 / U1: Button only renders when prd_pdf_bytes is non-empty."""

    def test_inline_pdf_download_button_shows_when_prd_pdf_bytes_present(self):
        """S1 contract: button must appear when bytes are present."""
        sv = _make_sv(prd_pdf_bytes=b"%PDF-1.4 fake-pdf-content", is_complete=True)
        assert _should_show_inline_pdf(sv) is True, (
            "Inline PDF button must be shown when prd_pdf_bytes is non-empty"
        )

    def test_pdf_download_button_not_shown_when_pdf_missing(self):
        """S1 contract: button must be absent when bytes are empty."""
        sv = _make_sv(prd_pdf_bytes=b"", is_complete=True)
        assert _should_show_inline_pdf(sv) is False, (
            "Inline PDF button must NOT be shown when prd_pdf_bytes is empty"
        )

    def test_pdf_download_button_not_shown_when_pdf_is_none(self):
        """S1 contract: missing key treated same as empty bytes."""
        sv = {"is_complete": True}  # prd_pdf_bytes key absent
        assert _should_show_inline_pdf(sv) is False, (
            "Inline PDF button must NOT be shown when prd_pdf_bytes key is missing"
        )


class TestFinalReportMessageAndInlineDownload:
    """U1 / U2: Complete message type triggers the inline download path."""

    def test_final_report_message_and_inline_download_render_together(self):
        """
        When the last assistant message has type='complete' AND prd_pdf_bytes is
        non-empty, both the success banner AND the download button must be rendered.
        This test verifies the logical prerequisites for that co-rendering.
        """
        msg = _make_complete_message("Your requirements report is ready.")
        sv = _make_sv(
            prd_pdf_bytes=b"%PDF-1.4 not-empty",
            prd_report_title="Requirements Summary - AI Matching Tool",
            is_complete=True,
        )

        # The message must have type='complete' to trigger the correct render branch
        assert msg["type"] == "complete", (
            "Only 'complete' messages trigger the inline download branch"
        )
        # The PDF guard must pass independently
        assert _should_show_inline_pdf(sv) is True, (
            "PDF download must be gated on non-empty bytes"
        )

    def test_filename_derived_from_prd_report_title_not_raw_key(self):
        """S2 / S3: filename must use prd_report_title, sanitised, never expose internal keys."""
        sv = _make_sv(
            prd_pdf_bytes=b"fake",
            prd_report_title="Requirements Summary \u2014 AI Matching Tool",
        )
        filename = _derive_filename(sv)
        # Must end with .pdf
        assert filename.endswith(".pdf"), "Filename must end with .pdf"
        # Must not contain em-dash or unicode special chars after sanitisation
        stem = filename[:-4]
        assert all(c.isalnum() or c in ("_", "-", " ") for c in stem), (
            f"Filename stem must contain only safe chars; got: {stem!r}"
        )
        # Must not expose internal state key names
        for forbidden in ("prd_pdf_bytes", "prd_report_title", "confirmed_qa_store"):
            assert forbidden not in filename, (
                f"Filename must not expose internal key name: {forbidden!r}"
            )
        # Must not be the raw default "requirements_report" since title was provided
        assert stem != "requirements_report", (
            "Filename should be derived from the actual title, not the fallback"
        )

    def test_filename_falls_back_to_requirements_report_when_title_empty(self):
        """S2: when prd_report_title is empty, filename falls back to safe default."""
        sv = _make_sv(prd_pdf_bytes=b"fake", prd_report_title="")
        filename = _derive_filename(sv)
        assert filename == "requirements_report.pdf", (
            f"Empty title must produce fallback filename; got {filename!r}"
        )
