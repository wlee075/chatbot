"""
Session-scoped JSONL event logger for the PRD chatbot graph.

Every log line is a self-contained JSON object — one per line.
Each line includes standard context fields (thread_id, run_id, node_name,
section_name, section_index, iteration) plus event-specific extras.

Files
─────
  logs/session_<thread_id[:8]>.log   — INFO + WARNING (always written)
  logs/session_<thread_id[:8]>.debug — DEBUG only (created when LOG_LEVEL=DEBUG)

Console output mirrors the .log file at the configured level (default: INFO).

Usage
─────
  from utils.logger import log_event

  log_event(
      thread_id="abc123", run_id="run456",
      level="INFO", event_type="node_start",
      message="reflect_node started",
      node_name="reflect_node",
      section_name="Goals", section_index=2, iteration=1,
      draft_len=450,
  )

Gate all DEBUG calls behind LOG_LEVEL=DEBUG in .env to avoid log spam.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_LOGS_DIR = _PROJECT_ROOT / "logs"

# Cache open file handles per thread_id to avoid re-opening on every call.
_HANDLES: dict[str, dict[str, object]] = {}


def _env_level() -> int:
    """Read LOG_LEVEL from environment; default INFO."""
    return getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)


def _file_handles(thread_id: str) -> dict[str, object]:
    """Return (and cache) open file handles for this session thread_id."""
    if thread_id in _HANDLES:
        return _HANDLES[thread_id]

    _LOGS_DIR.mkdir(parents=True, exist_ok=True)
    short = thread_id[:8]

    h: dict[str, object] = {
        "info": open(_LOGS_DIR / f"session_{short}.log", "a", encoding="utf-8"),
        "debug": None,
    }
    if _env_level() <= logging.DEBUG:
        h["debug"] = open(
            _LOGS_DIR / f"session_{short}.debug", "a", encoding="utf-8"
        )

    _HANDLES[thread_id] = h
    return h


def log_event(
    *,
    thread_id: str,
    run_id: str,
    level: str,          # "DEBUG" | "INFO" | "WARNING"
    event_type: str,     # e.g. "node_start", "reflect_parsed", "routing_decision"
    message: str,        # short human-readable summary
    node_name: str = "",
    section_name: str = "",
    section_index: int = -1,
    iteration: int = -1,
    **extra,             # flat event-specific fields
) -> None:
    """
    Emit one JSONL line to the session log file.

    Standard fields are always present. Any additional kwargs are appended
    as flat fields in the same JSON object.
    """
    lvl = getattr(logging, level.upper(), logging.INFO)

    record = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "level": level.upper(),
        "event_type": event_type,
        "message": message,
        "thread_id": thread_id,
        "run_id": run_id,
        "node_name": node_name,
        "section_name": section_name,
        "section_index": section_index,
        "iteration": iteration,
        **extra,
    }

    line = json.dumps(record, ensure_ascii=False)
    h = _file_handles(thread_id)

    # INFO + WARNING always go to the .log file
    if lvl >= logging.INFO:
        h["info"].write(line + "\n")
        h["info"].flush()

    # DEBUG goes to the .debug file (only exists when LOG_LEVEL=DEBUG)
    if lvl == logging.DEBUG and h["debug"] is not None:
        h["debug"].write(line + "\n")
        h["debug"].flush()

    # Console output at configured level
    if lvl >= _env_level():
        ts = record["timestamp"]
        print(f"{ts}  {level.upper():<7}  [{event_type}] {message}")
