"""
Structured telemetry for conversational state monitoring.
Wraps utils.logger for Phase 1 P0 stabilization requirements.
"""

from __future__ import annotations
from utils.logger import log_event
import time

def log_canonical_write(
    *,
    thread_id: str,
    run_id: str,
    node_name: str,
    fact_id: str,
    concept_id: str,
    change_type: str,  # "CREATED" | "SUPERSEDED" | "RETRACTED"
    version: int,
    **extra,
) -> None:
    """Telemetry for any mutation of confirmed_qa_store."""
    log_event(
        thread_id=thread_id,
        run_id=run_id,
        level="INFO",
        event_type="canonical_write",
        message=f"Store mutation: {change_type} {concept_id}",
        node_name=node_name,
        fact_id=fact_id,
        concept_id=concept_id,
        change_type=change_type,
        version=version,
        **extra,
    )

def log_suppression_decision(
    *,
    thread_id: str,
    run_id: str,
    node_name: str,
    concept_id: str,
    decision: str,  # "ASKED" | "SUPPRESSED"
    reason: str,
) -> None:
    """Telemetry for repeats prevention logic."""
    log_event(
        thread_id=thread_id,
        run_id=run_id,
        level="INFO",
        event_type="suppression_decision",
        message=f"Suppression {decision}: {concept_id}",
        node_name=node_name,
        concept_id=concept_id,
        decision=decision,
        reason=reason,
    )

def log_parity_result(
    *,
    thread_id: str,
    run_id: str,
    node_name: str,
    is_dirty: bool,
    delta_count: int,
    parity_hash: str = "",
) -> None:
    """Telemetry for Store vs Mirror synchronization."""
    log_event(
        thread_id=thread_id,
        run_id=run_id,
        level="INFO" if not is_dirty else "WARNING",
        event_type="parity_check",
        message=f"Parity check: {'OK' if not is_dirty else 'DESYNCED'}",
        node_name=node_name,
        is_dirty=is_dirty,
        delta_count=delta_count,
        parity_hash=parity_hash,
    )

def log_integrity_failure(
    *,
    thread_id: str,
    run_id: str,
    node_name: str,
    failure_type: str,
    message: str,
    **extra,
) -> None:
    """Critical telemetry for P0 integrity violations."""
    log_event(
        thread_id=thread_id,
        run_id=run_id,
        level="ERROR",
        event_type="integrity_failure",
        message=f"CRITICAL: {failure_type} - {message}",
        node_name=node_name,
        failure_type=failure_type,
        **extra,
    )
