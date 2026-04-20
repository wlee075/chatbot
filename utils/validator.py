"""
Integrity validation suite for PRD state monitoring.
Handles semantic corruption detection, duplicate checks, and transition validation.
"""

from __future__ import annotations
import time
import json
import logging
import re
from typing import Any, Dict, Tuple, Optional
from utils.telemetry import log_integrity_failure
from config.sections import PRD_SECTIONS

def _check_numeric_plausibility(answer: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Deterministically checks for impossible numeric bounds in the user's answer.
    Returns (validation_flag, validation_reason) if found, else (None, None).
    """
    answer_lower = answer.lower()
    
    # Catch "N hours per/a day" where N > 24
    match = re.search(r'(\d+(?:\.\d+)?)\s*(?:hour|hr)s?\s*(?:per|a|\/)\s*day', answer_lower)
    if match:
        hours = float(match.group(1))
        if hours > 24:
            return "INVALID_VALUE", "hours_per_day_exceeds_24"
    return None, None

class IntegrityValidator:
    @staticmethod
    def validate_mutation(
        *,
        thread_id: str,
        run_id: str,
        node_name: str,
        store: dict[str, Any],
        update: dict[str, Any],
        section_id: str,
    ) -> None:
        """Post-write semantic validation of a canonical store mutation."""
        
        # 1. Structural Validation (P0)
        valid_section_ids = {s.id for s in PRD_SECTIONS}
        if section_id not in valid_section_ids:
             log_integrity_failure(
                thread_id=thread_id, run_id=run_id, node_name=node_name,
                failure_type="SEMANTIC_CORRUPTION",
                message=f"Invalid section_id: {section_id}",
                section_id=section_id
            )

        # 2. Duplicate ACTIVE Fact Detection (P0)
        # Check if the new update overlaps with existing facts in the same section
        for new_key, new_val in update.items():
            new_subparts = set(new_val.get("resolved_subparts", []))
            if not new_subparts:
                continue
                
            for old_key, old_val in store.items():
                if old_val.get("section_id") == section_id and old_key != new_key:
                    # If same subparts but different concept key and not a correction linkage
                    old_subparts = set(old_val.get("resolved_subparts", []))
                    if new_subparts.intersection(old_subparts):
                         # If it's not explicitly a correction (SUPERSEDED)
                         if new_val.get("event_type") != "CORRECT_MESSAGE":
                            log_integrity_failure(
                                thread_id=thread_id, run_id=run_id, node_name=node_name,
                                failure_type="DUPLICATE_ACTIVE_FACT",
                                message=f"Duplicate subparts detected in section {section_id}",
                                concept_id=new_key, overlapping_with=old_key
                            )

        # 3. Orphan Supersession Verification (P1)
        for _, val in update.items():
            corrects_key = val.get("corrects_key")
            if corrects_key and corrects_key not in store:
                log_integrity_failure(
                    thread_id=thread_id, run_id=run_id, node_name=node_name,
                    failure_type="ORPHAN_SUPERSESSION",
                    message=f"Correction references non-existent key: {corrects_key}",
                    concept_id=corrects_key
                )

class LatencyMonitor:
    """Lightweight turn-scoped latency tracker."""
    def __init__(self):
        self.timings = {}

    def start(self, node_name: str):
        self.timings[node_name] = time.monotonic()

    def end(self, node_name: str) -> int:
        if node_name in self.timings:
            duration = int((time.monotonic() - self.timings[node_name]) * 1000)
            return duration
        return 0
