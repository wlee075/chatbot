"""tests/test_gateway_contract.py

Contract tests between utils/nemo_guardrails_gateway.GatewayResult
and graph/split_nodes.nemo_guardrails_gateway_node.

Addresses the runtime crash:
  split_nodes.py:118 read result.guardrail_confidence
  but GatewayResult defines the field as: confidence

These tests verify:
  1. GatewayResult has every field nemo_guardrails_gateway_node consumes
  2. The node runs end-to-end on a valid GatewayResult without AttributeError
  3. The node degrades safely when a malformed object reaches it
  4. The second-turn regression (first turn generates a question, second
     turn enters the gateway) no longer crashes
  5. Logging uses result.confidence (not result.guardrail_confidence)
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch
from dataclasses import fields

from utils.nemo_guardrails_gateway import GatewayResult, safe_fallback_result


# ── Minimal state helpers ─────────────────────────────────────────────────────

def _minimal_state(**overrides) -> dict:
    base = {
        "run_id": "test-run",
        "section_index": 2,
        "raw_answer_buffer": "the main pain point is approval latency",
        "uploaded_files": [],
        "current_questions": "What is the main operational pain today?",
        "confirmed_qa_store": {},
        "section_qa_pairs": [],
        "guardrail_reason": "",
        "chat_history": [],
    }
    base.update(overrides)
    return base


def _valid_gateway_result(**overrides) -> GatewayResult:
    """Build a fully-specified GatewayResult with overrides."""
    defaults = dict(
        message_class="valid_answer",
        confidence=0.88,
        allow_commit=True,
        allow_section_complete=True,
        allow_advance=True,
        route_to="numeric_validation",
        clarification_needed=False,
        corrected_prior_content=False,
        target_section_override=None,
        classifier_source="NEMO_FASTEMBED",
        guardrail_reason="NeMo NEMO_FASTEMBED classified as valid_answer",
        signals={"nemo_intent_raw": "valid answer", "section_id": "background"},
    )
    defaults.update(overrides)
    return GatewayResult(**defaults)


# ── T1: Schema contract ───────────────────────────────────────────────────────

def test_gateway_result_contract_matches_split_node_expectations():
    """T1: Every field nemo_guardrails_gateway_node reads must exist on GatewayResult.

    This is the canonical contract table.  If GatewayResult fields are renamed
    this test will fail before the app crashes.
    """
    # Fields consumed by nemo_guardrails_gateway_node (from split_nodes.py audit)
    FIELDS_CONSUMED_BY_NODE = {
        # defensive guard
        "message_class", "confidence", "allow_commit", "allow_section_complete",
        "allow_advance", "route_to", "corrected_prior_content", "target_section_override",
        "guardrail_reason", "classifier_source", "signals",
        # logging (blocked path)
        # "message_class", "classifier_source", "guardrail_reason", "route_to", "signals"
        # logging (rerouted path)
        # "target_section_override"
        # logging (passed path) — THIS was the crash site
        # "confidence"   ← line 118 previously read result.guardrail_confidence
        # state update dict
        # "allow_commit", "allow_section_complete", "allow_advance", "route_to",
        # "corrected_prior_content", "target_section_override", "guardrail_reason",
        # "confidence", "classifier_source"
        # noise path
        # (no extra fields)
    }

    dataclass_fields = {f.name for f in fields(GatewayResult)}
    missing = FIELDS_CONSUMED_BY_NODE - dataclass_fields

    assert not missing, (
        f"GatewayResult is missing fields that nemo_guardrails_gateway_node consumes: {missing}\n"
        f"Existing fields: {dataclass_fields}"
    )

    # Explicitly assert the canonical confidence field exists (crash regression)
    assert hasattr(GatewayResult, "__dataclass_fields__"), "GatewayResult must be a dataclass"
    assert "confidence" in dataclass_fields, (
        "GatewayResult.confidence is the canonical field — do NOT rename to guardrail_confidence"
    )
    assert "guardrail_confidence" not in dataclass_fields, (
        "guardrail_confidence must NOT exist on GatewayResult — it lives in PRDState (graph state), "
        "not on the return object. Confusing the two caused the crash on line 118."
    )


_GATEWAY_PATCH = "utils.nemo_guardrails_gateway.run_nemo_guardrails_gateway"
_GATEWAY_FALLBACK_PATCH = "utils.nemo_guardrails_gateway.safe_fallback_result"


# ── T2: Happy-path node execution ─────────────────────────────────────────────

def test_nemo_guardrails_gateway_node_does_not_crash_on_valid_gateway_result():
    """T2: A fully-populated GatewayResult must not raise AttributeError in the node."""
    from graph.split_nodes import nemo_guardrails_gateway_node

    state = _minimal_state()
    valid_result = _valid_gateway_result()

    with patch(_GATEWAY_PATCH, return_value=valid_result):
        updates = nemo_guardrails_gateway_node(state)

    assert isinstance(updates, dict), "Node must return a dict"
    assert "message_class" in updates, "Node output must include message_class"
    assert updates["message_class"] == "valid_answer"
    assert updates["guardrail_confidence"] == 0.88, (
        "State key 'guardrail_confidence' must receive GatewayResult.confidence value"
    )
    assert "guardrail_source" in updates


# ── T3: Malformed result degradation ─────────────────────────────────────────

def test_nemo_guardrails_gateway_node_safe_fallback_on_missing_field():
    """T3: A GatewayResult missing 'confidence' must trigger safe fallback, not AttributeError.

    This directly tests the defensive schema guard added to nemo_guardrails_gateway_node.
    """
    from graph.split_nodes import nemo_guardrails_gateway_node
    from types import SimpleNamespace

    # Simulate a malformed object that has all fields EXCEPT 'confidence'
    malformed = SimpleNamespace(
        message_class="valid_answer",
        allow_commit=True,
        allow_section_complete=True,
        allow_advance=True,
        route_to="numeric_validation",
        corrected_prior_content=False,
        target_section_override=None,
        guardrail_reason="test",
        classifier_source="NEMO_FASTEMBED",
        signals={},
        # deliberately omitting 'confidence'
    )

    state = _minimal_state()

    try:
        with patch(_GATEWAY_PATCH, return_value=malformed):
            updates = nemo_guardrails_gateway_node(state)
    except AttributeError as exc:
        pytest.fail(
            f"nemo_guardrails_gateway_node raised AttributeError instead of degrading safely: {exc}"
        )

    assert isinstance(updates, dict), "Node must return a dict even with malformed result"
    assert updates.get("guardrail_confidence") == 0.0, (
        "Safe fallback must produce confidence=0.0"
    )
    assert updates.get("gateway_allow_commit") is False, (
        "Safe fallback must not allow_commit"
    )


# ── T4: Second-turn regression ────────────────────────────────────────────────

def test_second_turn_after_question_generation_enters_gateway_successfully():
    """T4: Reproduce the exact regression.

    Simulates: turn-1 produces questions, turn-2 user answer enters the gateway.
    Before the fix: AttributeError at split_nodes.py:118 (result.guardrail_confidence).
    After the fix: gateway returns a valid dict update without any exception.
    """
    from graph.split_nodes import nemo_guardrails_gateway_node

    # State after turn-1 question generation (typical values)
    state = _minimal_state(
        raw_answer_buffer="the problem is that supervisors have no real-time visibility",
        section_index=2,
        chat_history=[
            {"role": "assistant", "content": "What is the main operational pain today?"}
        ],
    )

    expected_result = _valid_gateway_result(
        message_class="valid_answer",
        confidence=0.77,
        classifier_source="NEMO_FASTEMBED",
    )

    with patch(_GATEWAY_PATCH, return_value=expected_result):
        try:
            updates = nemo_guardrails_gateway_node(state)
        except AttributeError as exc:
            pytest.fail(
                f"Second-turn gateway crash regression: AttributeError raised — {exc}"
            )

    assert updates["message_class"] == "valid_answer"
    assert updates["gateway_allow_commit"] is True
    assert updates["guardrail_confidence"] == 0.77
    assert updates["guardrail_source"] == "NEMO_FASTEMBED"


# ── T5: Canonical confidence field in logging ─────────────────────────────────

def test_gateway_logging_uses_canonical_confidence_field():
    """T5: The log event in the 'passed' branch reads result.confidence, not result.guardrail_confidence.

    This is a direct regression test for the crash on line 118.
    """
    from graph.split_nodes import nemo_guardrails_gateway_node

    state = _minimal_state()
    result = _valid_gateway_result(message_class="valid_answer", confidence=0.91)

    log_calls = []

    def _capture_log(*args, **kwargs):
        log_calls.append(kwargs)

    with patch(_GATEWAY_PATCH, return_value=result), \
         patch("graph.split_nodes.log_event", side_effect=_capture_log):
        nemo_guardrails_gateway_node(state)

    passed_log = next(
        (c for c in log_calls if c.get("event_type") == "nemo_guardrails_classified"), None
    )
    assert passed_log is not None, "Expected a 'nemo_guardrails_classified' log event"
    assert "confidence" in passed_log, (
        "Log event must include 'confidence' key (read from result.confidence)"
    )
    assert passed_log["confidence"] == 0.91, (
        f"Log confidence must match result.confidence (0.91), got {passed_log.get('confidence')}"
    )
