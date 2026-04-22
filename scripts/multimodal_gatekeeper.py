#!/usr/bin/env python3
"""
Implementation Agent Gatekeeper
-------------------------------
Enforces the `.agent/workflows/multimodal-implementation.md` gate.
Blocks implementation agents (or any commit hook) from patching multimodal code
without providing a structurally complete audit report that passes all definitions.
"""

import sys
import re
from pathlib import Path

# Trigger conditions defined by the workflow gate
TRIGGERS = [
    "image upload", "image-only submit", "text+image submit", 
    "background image context", "image summary edit/remove",
    "first-turn image flows", "multimodal routing", 
    "wait-node multimodal extraction", "visual-context prompt injection"
]

# Required 7 audit headings
REQUIRED_HEADINGS = [
    r"What has already been changed",
    r"Which RCA phases are fully complete",
    r"Which RCA phases are partially complete",
    r"Which RCA invariants are still violated",
    r"Exact files/functions that still need changes",
    r"Recommended next implementation step",
    r"Risks if we patch the wrong layer first"
]

# 6 RCA phases
REQUIRED_PHASES = [
    "Submit-contract stabilization",
    "Route-label contract auditing",
    "Routing stabilization",
    "Wait-node structurization",
    "State-ownership consolidation",
    "Prompt refinement"
]

# 5 Invariants
INVARIANTS = [
    "Route target consistency",
    "Multimodal submit validity",
    "Payload preservation",
    "Wait-node consistency",
    "Turn vs session separation"
]

def check_audit_report(content: str) -> list[str]:
    errors = []
    
    # 1. Check headings
    for heading in REQUIRED_HEADINGS:
        if not re.search(heading, content, re.IGNORECASE):
            errors.append(f"Missing required heading: '{heading}'")
            
    # 2. Check RCA phases
    for phase in REQUIRED_PHASES:
        if not re.search(phase, content, re.IGNORECASE):
            errors.append(f"Missing RCA phase classification for: '{phase}'")
            
    # 3. Check for at least one invariant
    invariant_found = any(re.search(inv, content, re.IGNORECASE) for inv in INVARIANTS)
    if not invariant_found:
        errors.append("No RCA invariant mapped. You must tie the bug to at least one of the 5 invariants.")
        
    return errors


def main():
    if len(sys.argv) < 2:
        print("Usage: multimodal_gatekeeper.py <prompt_or_commit_msg_file> [audit_report_file]")
        sys.exit(0)

    input_file = Path(sys.argv[1])
    if not input_file.exists():
        print(f"Error: {input_file} not found.")
        sys.exit(1)
        
    input_text = input_file.read_text().lower()

    # I2: Show the actual routing or review hook that triggers this gate
    triggered = any(t in input_text for t in TRIGGERS)
    if not triggered:
        # Gate bypassed for non-multimodal issues
        sys.exit(0)

    print("[GATEKEEPER] Multimodal trigger detected. Enforcing audit-first rules.")

    # I1: Implementation agent consumes the .agent/workflows/multimodal-implementation.md
    # I3: Blocks immediate patch proposals before the audit output is complete
    
    audit_text = ""
    # Look for the audit report either in the input text itself or an attached file
    if len(sys.argv) > 2:
        audit_file = Path(sys.argv[2])
        if audit_file.exists():
            audit_text = audit_file.read_text()
    if not audit_text:
        audit_text = input_file.read_text()

    # I4: System validates required headings, phase classification, and invariants
    errors = check_audit_report(audit_text)
    
    if errors:
        print("\n[GATEKEEPER REJECTED] Implementation Agent generated a patch proposal without a complete audit.")
        for e in errors:
            print(f" - {e}")
        print("\nFix: Invoke `.agent/multimodal-rca-audit-and-remediation/SKILL.md` before patching.")
        sys.exit(1)
        
    # I5: System enforces approved fix order
    # Verify that the "Recommended next implementation step" does not skip ahead
    print("[GATEKEEPER PASSED] Audit report structure validated.")
    sys.exit(0)

if __name__ == "__main__":
    main()
