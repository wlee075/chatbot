---
description: Operational gate to enforce the multimodal RCA audit-first workflow before any patching occurs
---

# Multimodal Implementation Gate

**MUST READ BEFORE ANY MULTIMODAL IMPLEMENTATION WORK**

## 1. Purpose
This file is the mandatory first step and enforcement contract for the implementation agent whenever multimodal workflows are involved. It strictly prevents premature, local patching and ensures adherence to the approved architectural sequence.

## 2. Trigger Conditions (Explicit Matches)
This gate **MUST** fire if the request mentions any of the following exact phrases (or their conservative equivalents):
- "image upload"
- "image-only submit"
- "text+image submit"
- "background image context"
- "image summary edit/remove"
- "first-turn image flows"
- "multimodal routing"
- "wait-node multimodal extraction"
- "visual-context prompt injection"

*Note: Equivalent phrases must be interpreted conservatively so the gate does not over-fire on vague image mentions unrelated to multimodal workflow fixes.*

## 3. Exemptions
This gate **DOES NOT** trigger for unrelated text-only bugs. The agent MUST explicitly bypass this gate for text-only paths.

## 4. Mandatory Precondition: Audit First
Upon this gate firing, the implementation agent **MUST immediately invoke** the `.agent/multimodal-rca-audit-and-remediation/SKILL.md` skill.

## 5. Blocked Actions Before Audit
**NO** actions can proceed until the audit output is completed and accepted.
Blocked actions include:
- NO fix plan
- NO code patch proposal
- NO prompt tweak
- NO route-label tweak
- NO state cleanup recommendation

## 6. Required Audit Output
The audit output **MUST** contain at least these 7 headings:
1. What has already been changed
2. Which RCA phases are fully complete
3. Which RCA phases are partially complete
4. Which RCA invariants are still violated
5. Exact files/functions that still need changes
6. Recommended next implementation step
7. Risks if we patch the wrong layer first

## 7. Required RCA Phase Classification
The agent **MUST** classify all 6 RCA phases exactly as one of: *not started*, *partially complete*, *complete*, or *implemented incorrectly*.
The phases are:
1. Submit-contract stabilization
2. Route-label contract auditing
3. Routing stabilization
4. Wait-node structurization
5. State-ownership consolidation
6. Prompt refinement

## 8. Required Invariant Mapping
The agent **MUST** tie every major bug to at least one of the 5 invariants. Symptom-only reporting is forbidden.
The invariants are:
1. Route target consistency
2. Multimodal submit validity
3. Payload preservation
4. Wait-node consistency
5. Turn vs session separation

## 9. Gate Completion Criteria
The Audit Gate passes ONLY IF:
1. At least the 7 required audit headings are present.
2. All 6 RCA phases are classified.
3. The current bug is tied to at least one RCA invariant.
4. No code patch or fix plan is proposed before the audit output is complete.

## 10. Approved Fix Order
Once the gate passes, fixes **MUST** proceed numerically.
Implementation plans skipping ahead are **REJECTED** unless explicit, dependency-based justification is provided.
1. Submit-contract stabilization
2. Route-label contract auditing
3. Routing stabilization
4. Wait-node structurization
5. State-ownership consolidation
6. Prompt refinement

## 11. Phase Checkpoint Requirements
A phase is **NOT COMPLETE** until the checkpoint expressly reports all of the following:
- exact files touched
- tests added/updated
- one healthy execution trace
- the enforced RCA invariant

## 12. Rejection Conditions (Enumerated)
The implementer’s response is **REJECTED** if any of the following occur:
- Missing any required heading from the audit
- Missing classification for any RCA phase
- Missing an invariant mapping tied to the active bug
- Proposing code edits before the audit completes
- Proposing prompt changes before orchestration stabilization
- Recommending removal of legacy fields before migration is covered by tests
