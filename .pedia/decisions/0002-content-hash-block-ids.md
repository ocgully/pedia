---
type: decision
id: 0002-content-hash-block-ids
status: accepted
date: 2026-04-24
defines: [Content Hash Block ID Decision]
---

# ADR 0002 — Content-hash block IDs

## Status

Accepted.

## Context

Every block in Pedia needs a stable identifier. The ID appears in
citations agents paste into outputs, in `pedia trace` graph walks,
and in `[[block:<id>]]` wiki-links. The identifier scheme shapes how
robust those references are over time.

Three plausible schemes:

1. **Monotonic counter** — `block:1`, `block:2`, ... assigned at
   first index time.
2. **UUID** — random per-block, persisted forever.
3. **Content hash** — `sha256(normalize(body))[:16]`.

## Decision

Use **content-hash IDs**: `sha256(normalize(body))[:16]`, where
`normalize` strips trailing whitespace per line and surrounding blank
lines. Take the first 16 hex characters as the block ID.

Schema and normalization lives in
[[specs/001-indexing-engine/spec.md#content-hash-id]].

## Rationale

Citation honesty. A content-hash ID *is* a fingerprint of the block's
bytes. If an agent cites `block:abc123` and someone edits the block,
the new content gets a new ID. The old citation no longer resolves —
which is the **correct** behavior. Drift is visible.

Statelessness. The indexer doesn't need to maintain "what counter
value did I assign last?" state. A fresh checkout, a fresh
`index.sqlite`, the same content, the same IDs.

Reproducibility. Two developers running `pedia refresh` on the same
commit get bit-for-bit identical IDs. The index is fully derivable
from the content.

## Alternatives considered

**Monotonic counter.** Rejected — assignment depends on indexer
order, which depends on filesystem traversal order, which is not
stable across OSes. Two clones of the same repo would get different
IDs. Citations between developers would diverge.

**UUID v4.** Rejected — same problem (non-deterministic), and adds
the operational burden of a "what UUIDs have I assigned?" registry
that needs to be checked into the repo. Defeats the point of an
index that rebuilds from content.

**SHA-256 full (64 hex)** vs truncated (16 hex). 16 hex = 64 bits =
~18 quintillion values. Birthday-bound collision probability for
1M blocks is < 1e-7. Worth the readability win.

**Hybrid (content-hash + heading-slug fallback).** Considered for
phase 2 if real-world ID churn becomes a problem. The current
position: edits should produce new IDs; for stability across edits,
use the other [[Wiki Link]] forms.

## Consequences

Positive:

- Same content → same ID → reproducible citations.
- Stateless indexer; the content *is* the source of truth.
- Visible drift — a citation that no longer resolves is a signal,
  not a silent bug.

Negative:

- IDs shift when content changes. Editing a block invalidates its
  block-ID citations.
- Mitigation: cite via `[[Term]]`, `[[type:slug]]`, or
  `[[path#heading]]` for cross-references that should survive edits.
  Reserve `[[block:<id>]]` for "I cite the exact bytes" cases (e.g.,
  Hopewell `spec-ref`s where drift detection is desirable).

The constitution captures this tradeoff in
[[Content-Hash Block ID]].

## Supersedes / superseded by

None.
