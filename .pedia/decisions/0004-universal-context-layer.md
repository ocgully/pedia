---
type: decision
id: 0004-universal-context-layer
status: accepted
date: 2026-04-24
defines: [Universal Context Layer Decision]
---

# ADR 0004 — Universal-context layer

## Status

Accepted.

## Context

Agents forget. Even when an agent has read the constitution at the
start of a session, three task-switches and twenty tool calls later
the constraints can fall out of context. The result is shipped code
that violates principles the agent already knows.

The pattern is recognizable: a project that says "no runtime deps"
ships a runtime dep. A project that says "cite everything" ships an
agent output with no citations. A project that says "agent-first"
gets a feature designed around a human reviewer because nobody
re-asked the principle.

Pedia has a unique opportunity to fix this structurally. The agent
already calls `pedia query`. We can return universal context as part
of every response.

## Decision

Any block flagged `universal_context: true` in its front-matter is
**prepended to every `pedia query` and `pedia show` response**,
regardless of what the user asked.

The response shape is:

```
=== universal context ===
[block:u1] constitution/technical.md @ "Determinism Over Magic"
<body>
[block:u2] constitution/technical.md @ "Cite Everything"
<body>
=== matches ===
...
```

Discipline: **few in number, small in size, stable over time.** A
typical project has < 10 universal blocks. The `universal_reserve`
in `config.yaml` (default 500 tokens) caps the budget.

`pedia show --universal-context` returns just the universal layer —
useful for an agent to read at session start.

## Rationale

Structural reminders beat checklists. An agent that has to remember
to re-read the constitution sometimes won't. An agent that gets the
constitution as part of every query response always sees it.

Tiny cost, large benefit. 10 small blocks ≈ 500 tokens. On a typical
2,000-token query budget, universal context costs ~25% — a
worthwhile tax for never-forgotten principles.

Auditability. Every output an agent produces carries the universal
context citations. A reviewer can see "the agent had access to the
'no runtime deps' principle when it shipped this dep" and route the
defect appropriately.

## Alternatives considered

**Make the agent re-fetch the constitution itself.** Rejected — see
"agents forget" above. The whole point is to remove the discipline
burden.

**Inject universal context only at session start.** Rejected — long
agent sessions blow it out of context anyway. Per-query is the
right cadence.

**Auto-flag every constitution chapter as universal.** Rejected —
flagging is a deliberate authoring decision. A "design philosophy"
chapter probably is universal; a "deprecation policy" chapter
probably isn't. The `universal_context: true` tag is a contract,
not a default.

**Allow universal context to differ by query type.** Rejected for
phase 1 — adds complexity without clear demand. Revisit if real
projects accumulate enough universal blocks that they need scoping.

## Consequences

Positive:

- Agents do not forget constitutional principles within a session.
- Outputs carry universal-context citations as evidence the
  principles were available at decision time.
- Onboarding (`pedia show --universal-context`) is one command.

Negative:

- Per-query token tax (~500 tokens default). Mitigated by the
  small-number-small-size discipline.
- Authors have to choose what's universal carefully. Bad choices
  bloat every query.

## Performance budget

`pedia query` retrieves universal context with a `WHERE universal=1`
scan over the indexed `idx_blocks_univ` index — O(N_universal), with
N_universal expected to be < 10. The total query cost stays O(constant)
in the universal layer, O(log N) in the FTS5 match layer.

## Where this binds

[[Universal Context]] in [[constitution/technical.md]] makes the
principle constitutional. The discipline of "few, small, stable" is
enforced at review time, not at the engine level.

## Supersedes / superseded by

None.
