"""
utils/llm_logger.py

Thin instrumentation wrapper around every LangChain LLM `.invoke()` call.

Usage
─────
    from utils.llm_logger import llm_invoke, flush_turn_summary

    response = llm_invoke(
        llm,
        messages,
        state=state,
        node_name="generate_questions_node",
        purpose="structured_question_generation",
        is_parallel=False,
    )

    # At the end of each user turn (called from await_answer_node or app.py):
    flush_turn_summary(state)

Design
──────
- One call to `llm_invoke` emits one JSONL line (event_type="llm_call").
- A per-thread accumulator collects all calls in a turn.
- `flush_turn_summary` emits a single aggregated "turn_llm_summary" line then
  clears the accumulator.
- Thread-safe for ThreadPoolExecutor fan-out (uses threading.Lock).
"""

from __future__ import annotations

import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from utils.logger import log_event

# ── Per-thread turn accumulator ───────────────────────────────────────────────
_lock = threading.Lock()
_accum: dict[str, list[dict]] = {}   # thread_id → list of call records


def _turn_key(state: dict) -> str:
    return state.get("thread_id", "unknown")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def llm_invoke(
    llm: Any,
    messages: list,
    *,
    state: dict,
    node_name: str,
    purpose: str,
    is_parallel: bool = False,
    attempt: int = 1,
) -> Any:
    """
    Invoke `llm` with `messages`, emitting a per-call log line.

    Parameters
    ----------
    llm         : A LangChain LLM (or `.with_structured_output(...)` variant).
    messages    : List of LangChain message objects.
    state       : Current PRDState dict (used for thread_id, run_id context).
    node_name   : Calling node identifier (used in log and metrics).
    purpose     : Short human-readable description of what this call does.
    is_parallel : True when called inside a ThreadPoolExecutor fan-out.
    attempt     : Retry attempt number (1 = first attempt).
    """
    thread_id = state.get("thread_id", "unknown")
    run_id = state.get("run_id", "")
    section = state.get("current_section", state.get("section_index", -1))
    iteration = state.get("iteration", -1)
    call_id = str(uuid.uuid4())
    model = getattr(llm, "model", None) or getattr(getattr(llm, "bound", None), "model", "unknown")
    prompt_chars = sum(len(m.content) if hasattr(m, "content") else len(str(m)) for m in messages)
    start_ts = _now_iso()
    t0 = time.monotonic()
    status = "success"
    response = None

    try:
        response = llm.invoke(messages)
    except Exception as exc:
        status = "error"
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        _emit_call_log(
            thread_id=thread_id, run_id=run_id, call_id=call_id,
            node_name=node_name, purpose=purpose, model=model,
            attempt=attempt, is_parallel=is_parallel,
            start_ts=start_ts, elapsed_ms=elapsed_ms,
            prompt_chars=prompt_chars, response_chars=0,
            status=status, section=section, iteration=iteration,
        )
        _accumulate(thread_id, node_name, elapsed_ms, status)
        raise

    elapsed_ms = int((time.monotonic() - t0) * 1000)
    response_chars = 0
    if hasattr(response, "content"):
        response_chars = len(response.content or "")
    elif isinstance(response, dict):
        response_chars = len(str(response))

    _emit_call_log(
        thread_id=thread_id, run_id=run_id, call_id=call_id,
        node_name=node_name, purpose=purpose, model=model,
        attempt=attempt, is_parallel=is_parallel,
        start_ts=start_ts, elapsed_ms=elapsed_ms,
        prompt_chars=prompt_chars, response_chars=response_chars,
        status=status, section=section, iteration=iteration,
    )
    _accumulate(thread_id, node_name, elapsed_ms, status)

    # Safety check: warn if attempt > 1 (indicates hidden retry)
    if attempt > 1:
        log_event(
            thread_id=thread_id, run_id=run_id,
            level="WARNING", event_type="llm_hidden_retry",
            message=f"LLM retry detected on {node_name}",
            node_name=node_name, call_id=call_id, attempt=attempt,
        )

    return response


def _emit_call_log(*, thread_id, run_id, call_id, node_name, purpose, model,
                   attempt, is_parallel, start_ts, elapsed_ms,
                   prompt_chars, response_chars, status, section, iteration):
    log_event(
        thread_id=thread_id, run_id=run_id,
        level="INFO", event_type="llm_call",
        message=f"{node_name} LLM call ({purpose}) in {elapsed_ms}ms [{status}]",
        node_name=node_name,
        section_index=section if isinstance(section, int) else -1,
        iteration=iteration,
        call_id=call_id,
        purpose=purpose,
        model=model,
        attempt=attempt,
        is_parallel=is_parallel,
        start_ts=start_ts,
        elapsed_ms=elapsed_ms,
        prompt_chars=prompt_chars,
        response_chars=response_chars,
        status=status,
    )


def _accumulate(thread_id: str, node_name: str, elapsed_ms: int, status: str) -> None:
    with _lock:
        if thread_id not in _accum:
            _accum[thread_id] = []
        _accum[thread_id].append({
            "node_name": node_name,
            "elapsed_ms": elapsed_ms,
            "status": status,
        })


def flush_turn_summary(state: dict, wall_clock_ms: int | None = None) -> None:
    """
    Emit the aggregated per-turn LLM summary and clear the accumulator.
    Call this at the end of each user-initiated turn (e.g. from await_answer_node).
    """
    thread_id = state.get("thread_id", "unknown")
    run_id = state.get("run_id", "")

    with _lock:
        records = _accum.pop(thread_id, [])

    if not records:
        return

    total = len(records)
    successful = sum(1 for r in records if r["status"] == "success")
    retried = sum(1 for r in records if r.get("attempt", 1) > 1)
    failed = total - successful
    summed_ms = sum(r["elapsed_ms"] for r in records)
    slowest_ms = max(r["elapsed_ms"] for r in records)

    per_node: dict[str, dict] = {}
    for r in records:
        nn = r["node_name"]
        if nn not in per_node:
            per_node[nn] = {"calls": 0, "total_ms": 0, "max_ms": 0}
        per_node[nn]["calls"] += 1
        per_node[nn]["total_ms"] += r["elapsed_ms"]
        per_node[nn]["max_ms"] = max(per_node[nn]["max_ms"], r["elapsed_ms"])

    # Alert thresholds
    level = "INFO"
    if total > 6:
        level = "WARNING"
    if total > 4 and level == "INFO":
        level = "INFO"  # log but don't alert

    log_event(
        thread_id=thread_id, run_id=run_id,
        level=level, event_type="turn_llm_summary",
        message=f"Turn used {total} LLM calls, {summed_ms}ms summed",
        node_name="__turn__",
        total_llm_calls=total,
        successful_calls=successful,
        retry_calls=retried,
        failed_calls=failed,
        summed_llm_ms=summed_ms,
        wall_clock_ms=wall_clock_ms or summed_ms,
        slowest_call_ms=slowest_ms,
        per_node=list(per_node.values()),
    )

    # G5: call budget alert
    if total > 6:
        log_event(
            thread_id=thread_id, run_id=run_id,
            level="WARNING", event_type="llm_call_budget_exceeded",
            message=f"Turn exceeded call budget: {total} LLM calls (threshold: 6)",
            node_name="__turn__", total_llm_calls=total,
        )
