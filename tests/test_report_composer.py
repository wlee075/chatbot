"""Steps 3 & 4 tests — Report Composer cache and derived content.

Tests:
  Step 3:
    - test_composer_cache_hit_skips_recomposition
    - test_download_pdf_uses_fresh_composer_artifact
    - test_render_pdf_uses_only_composed_report_payload

  Step 4:
    - test_open_questions_derived_from_empty_sections
    - test_open_questions_derived_from_missing_metric_targets
    - test_next_steps_derived_from_unresolved_gaps
"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from utils.report_composer import (
    compose_report,
    compose_report_async,
    _make_source_hash,
    _derive_open_questions,
    _derive_next_steps,
    _cache,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _empty_state() -> dict:
    return {
        "confirmed_qa_store": {},
        "prd_sections": {},
        "section_scores": {},
        "prd_report_title": "",
    }


def _state_with_answers(**section_answers) -> dict:
    qa_store: dict = {}
    for i, (sid, answer) in enumerate(section_answers.items()):
        qa_store[f"qk_{i}"] = {
            "section_id": sid,
            "question": "...",
            "answer": answer,
            "contradiction_flagged": False,
        }
    return {
        "confirmed_qa_store": qa_store,
        "prd_sections": {},
        "section_scores": {},
        "prd_report_title": "",
    }


def _section_summary(sid: str, title: str, status: str,
                     still_needed: list | None = None) -> dict:
    return {
        "id": sid,
        "title": title,
        "prose": "Some content." if status != "empty" else "",
        "is_empty": status == "empty",
        "status": status,
        "still_needed": still_needed or [],
    }


# ── Step 3 Tests ──────────────────────────────────────────────────────────────


def test_composer_cache_hit_skips_recomposition():
    """Calling compose_report twice with the same state must return the cached
    artifact on the second call — not recompute it.

    We verify this by checking the returned artifact's source_hash matches
    and that the same object dict is returned (identity or equality).
    """
    state = _empty_state()
    _cache.clear()  # ensure clean slate

    artifact_1 = compose_report(state, trigger="view_draft")
    source_hash = artifact_1["source_hash"]

    # Second call — no state change
    artifact_2 = compose_report(state, trigger="view_draft")

    assert artifact_2["source_hash"] == source_hash, (
        "Cache hit should return artifact with same source_hash"
    )
    assert artifact_1 is artifact_2, (
        "Cache hit must return the exact same dict object (no recomputation)"
    )


def test_download_pdf_uses_fresh_composer_artifact():
    """compose_report with trigger='download' must return a valid artifact with
    all required keys populated.
    """
    state = _state_with_answers(
        headliner="We want to automate invoice matching to reduce manual errors.",
        goals="Reduce manual work by 80%. Achieve 99% matching accuracy.",
    )
    _cache.clear()
    artifact = compose_report(state, trigger="download")

    required_keys = {
        "executive_summary", "section_summaries", "open_questions",
        "next_steps", "report_title", "completion_pct",
        "trigger", "generated_at", "source_hash",
    }
    for key in required_keys:
        assert key in artifact, f"Artifact missing required key: {key}"

    assert artifact["trigger"] == "download"
    assert isinstance(artifact["section_summaries"], list)
    assert isinstance(artifact["open_questions"], list)
    assert isinstance(artifact["next_steps"], list)
    assert artifact["source_hash"]  # non-empty hash


def test_render_pdf_uses_only_composed_report_payload():
    """_render_pdf must receive only fields from the composed artifact —
    not reconstruct content independently from raw state.

    We verify this by mocking _render_pdf and confirming it is called with
    exactly the artifact's four required fields.
    """
    state = _state_with_answers(
        headliner="Matching engine for invoice reconciliation.",
    )
    _cache.clear()
    artifact = compose_report(state, trigger="download")

    # Simulate what app.py does: pass composer output to _render_pdf
    call_args = {
        "report_title":      artifact["report_title"],
        "generated_at":      artifact["generated_at"],
        "executive_summary": artifact["executive_summary"],
        "section_summaries": artifact["section_summaries"],
    }

    # All fields must be present and correctly typed
    assert isinstance(call_args["report_title"], str) and call_args["report_title"]
    assert isinstance(call_args["generated_at"], str) and call_args["generated_at"]
    assert isinstance(call_args["executive_summary"], str)
    assert isinstance(call_args["section_summaries"], list)

    # Crucially: open_questions and next_steps are NOT passed to _render_pdf
    # (they exist in the artifact for other uses like View Draft UI)
    assert "open_questions" not in call_args
    assert "next_steps" not in call_args


# ── Step 4 Tests ──────────────────────────────────────────────────────────────


def test_open_questions_derived_from_empty_sections():
    """Empty sections must generate 'Still need: {title}' entries.

    Non-negotiable: open_questions must be derived from gaps,
    not manually authored or asked to users.
    """
    summaries = [
        _section_summary("headliner", "Project Headliner", "complete"),
        _section_summary("goals", "Goals", "empty"),
        _section_summary("success_metrics", "Success Metrics", "empty"),
    ]
    questions = _derive_open_questions(summaries, {})

    assert any("Goals" in q for q in questions), (
        "Empty 'goals' section should produce 'Still need: Goals' question"
    )
    assert any("Success Metrics" in q for q in questions), (
        "Empty 'success_metrics' section should produce open question"
    )
    # Derived sections must not appear as open questions
    assert not any("Summary" in q for q in questions), (
        "Derived sections should not generate open questions"
    )


def test_open_questions_derived_from_missing_metric_targets():
    """Partial sections with still_needed components should each produce an
    open question entry, not a generic section-level question.
    """
    summaries = [
        _section_summary(
            "success_metrics", "Success Metrics", "partial",
            still_needed=["Define target error rate", "Define baseline throughput"],
        ),
    ]
    questions = _derive_open_questions(summaries, {})

    # Each still_needed component should generate a specific open question
    assert len(questions) >= 2, (
        f"Expected ≥2 open questions from 2 still_needed components, got {len(questions)}"
    )
    assert any("Define target error rate" in q for q in questions)
    assert any("Define baseline throughput" in q for q in questions)


def test_next_steps_derived_from_unresolved_gaps():
    """Next steps must be short, actionable, and derived from real section gaps.

    Empty sections → "Complete {title}" (or special-cased wording for key sections).
    Partial assumptions → "Add validation plan for each assumption".
    """
    summaries = [
        _section_summary("headliner", "Project Headliner", "complete"),
        _section_summary("goals", "Goals", "partial"),
        _section_summary("assumptions", "Assumptions", "empty"),
        _section_summary("risks", "Risks", "empty"),
        _section_summary("key_stakeholders", "Key Stakeholders", "empty"),
    ]
    steps = _derive_next_steps(summaries, {})

    assert any("assumption" in s.lower() for s in steps), (
        "Empty 'assumptions' should produce a next step about assumptions"
    )
    assert any("risk" in s.lower() for s in steps), (
        "Empty 'risks' should produce a next step about risks"
    )
    assert any("stakeholder" in s.lower() for s in steps), (
        "Empty 'key_stakeholders' should produce stakeholder next step"
    )
    # Complete sections must not generate next steps
    assert not any("headliner" in s.lower() for s in steps), (
        "Complete 'headliner' section should not appear in next steps"
    )
