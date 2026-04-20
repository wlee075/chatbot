---
name: conversation-performance
description: Optimizes chatbot response speed without reducing context precision or recall. Use when replies are slow, investigating >3 second responses, reducing unnecessary LLM calls, tuning orchestration latency, or benchmarking conversation throughput.
---

# Conversation Performance Skill

Use this skill whenever chatbot replies feel slow or system throughput needs improvement.

## Core responsibility

This skill owns latency, throughput, and efficiency.

It helps answer:

- Why are replies slow?
- Which node is the bottleneck?
- Are LLM calls excessive?
- Is context too large?
- Can deterministic logic replace model calls?
- Are caches working?
- How do we reduce latency without hurting quality?

This skill does **not** own question logic, answer correctness, or product truth-state decisions.

## Primary performance standard

For each normal user reply:

- **Ideal:** ≤ 3 seconds end-to-end
- **Acceptable baseline:** 2–5 seconds
- **Warning:** > 5 seconds
- **Critical:** > 8 seconds

End-to-end includes:

- state loading
- routing
- validation
- question generation
- LLM inference
- rendering prep

Exclude only:

- user network lag
- platform outages
- browser rendering issues outside backend control

## Non-negotiable constraint

Performance improvements must **not** reduce:

### Context Precision

Do not use:

- wrong branch state
- stale facts
- unrelated memory
- incorrect prior answers

### Context Recall

Do not forget:

- answered questions
- chosen branches
- stored facts
- active workflow state

Fast but wrong = failure.  
Fast but forgetful = failure.  
Fast and accurate = target.

## When to use this skill

Use this skill when:

- Responses exceed 3 seconds regularly
- P95 latency is drifting upward
- Users perceive lag
- LLM costs are high
- Orchestration has too many steps
- Context windows are bloated
- Scaling traffic requires efficiency gains

## Measurement first rule

If metrics do not exist, establish baseline before optimizing.

Measure:

- P50 latency
- P95 latency
- P99 latency
- total turn latency
- per-node latency
- LLM latency
- non-LLM orchestration latency
- token counts
- cache hit rate
- retries/timeouts

Do not optimize blindly.

## Performance workflow

### 1. Break down latency by stage

Separate:

- input handling
- state read/write
- routing logic
- deterministic validators
- LLM calls
- rendering

### 2. Identify biggest bottleneck first

Do not micro-optimize 20ms helpers when one LLM call costs 3500ms.

### 3. Improve highest ROI component

Prioritize the slowest meaningful stage.

## Optimization hierarchy

### Tier 1: Remove unnecessary LLM calls

Replace with deterministic logic when possible:

- regex/rules
- branch routing
- duplicate checks
- numeric sanity checks
- exact option mapping

### Tier 2: Reduce number of LLM calls

Examples:

- combine two small prompts into one
- skip reflective pass when unnecessary
- short-circuit obvious cases

### Tier 3: Shrink prompt size

Reduce:

- irrelevant chat history
- stale context
- repeated instructions
- oversized examples

### Tier 4: Parallelize safe tasks

Run independent tasks together:

- retrieval + validation
- scoring + logging
- multiple deterministic checks

Only parallelize when outputs do not depend on each other.

### Tier 5: Cache intelligently

Cache:

- repeated retrievals
- deterministic transforms
- embeddings
- reusable summaries

Never serve stale workflow state.

## Common slow patterns

### Too many model calls

Bad:

- classify intent
- then classify again
- then ask LLM for same routing

### Bloated context

Bad:

- sending full chat history every turn
- repeated policy text every prompt

### Serial steps that could be parallel

Bad:

- wait for retrieval, then wait for validation, then wait for scoring

### Hidden retries

Bad:

- silent parser retries
- repeated structured-output attempts

## Practical targets

## Latency targets

- P50 < 2500 ms
- P95 < 5000 ms
- P99 < 8000 ms

## Hot path deterministic logic

- < 50 ms preferred
- < 200 ms max for normal utilities

## LLM usage targets

- 1 meaningful call per normal turn preferred
- 2 calls acceptable if justified
- 3+ calls requires review

## Good examples

### Example 1: Replace LLM with rules

Before:

Use LLM to detect “30 hours/day” typo.

After:

Use deterministic validator.

Savings: seconds.

### Example 2: Trim context

Before:

Send 30 prior turns.

After:

Send only active question + relevant facts.

Savings: tokens + latency.

### Example 3: Skip duplicate reflection

Before:

Draft → reflect every turn.

After:

Reflect only when material state changed.

Savings: one LLM pass.

## Benchmark method

Always benchmark with:

- sample size stated
- hardware/runtime context
- included vs excluded timings
- warm vs cold cache
- median and tail latency

Example:

- 1000 runs
- P50 = 1.9s
- P95 = 4.2s
- includes LLM call
- excludes browser rendering

## Guardrails

- Never trade correctness for raw speed blindly
- Never cache mutable truth-state without version checks
- Never hide timeouts as success
- Never optimize without before/after measurements
- Do not increase complexity for tiny gains

## Escalation rules

### Immediate action

- sustained P95 > 8s
- timeout spikes
- traffic collapse under load

### Investigation required

- P50 worsens > 20%
- token growth trend
- cache miss surge

## Success criteria

This skill is working well when:

- normal turns feel fast
- latency is stable at tail percentiles
- fewer unnecessary LLM calls occur
- token costs fall
- context accuracy remains intact
- scaling load does not degrade UX