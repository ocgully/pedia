---
type: decision
id: 0001-sqlite-fts5-over-embeddings
status: accepted
date: 2026-04-24
defines: [SQLite FTS5 Decision]
---

# ADR 0001 — SQLite FTS5 over embeddings

## Status

Accepted.

## Context

Pedia's primary use case is agent retrieval: an LLM agent asks a
question, Pedia returns blocks. The retrieval mechanism shapes every
property of the product — determinism, cost, install footprint,
operational complexity.

The two natural candidates were:

1. **Vector embeddings + cosine similarity** (Chroma, FAISS, pgvector,
   etc.). The default in 2024-25 LLM tooling.
2. **SQLite FTS5 + BM25.** A 30-year-old information-retrieval algorithm
   on a stdlib database.

The constitution requires [[Determinism Over Magic]] and
[[Stdlib-Only Runtime]]. This decision picks the implementation that
honors those.

## Decision

Use **SQLite FTS5 with BM25** as the only retrieval engine in phase 1.

Schema lives in [[specs/001-indexing-engine/spec.md#sqlite-schema]]. The
virtual table uses `tokenize='porter unicode61'`. Ranking is the
default FTS5 BM25.

Embeddings remain on the roadmap as a **phase-5 optional secondary
index** — opt-in via `pip install pedia[embed]`, never the default
code path.

## Rationale

Determinism. Embedding ranking varies with:

- Model version (text-embedding-ada-002 vs -3-small vs -3-large).
- Floating-point variance across hardware.
- The choice of similarity metric.

BM25 is documented, deterministic, and reproducible. Same query on
the same corpus gives the same ranked results forever.

Cost. SQLite FTS5 ships with Python stdlib. No PyPI install, no
GPU, no API key, no embedding-model-host process. A `pedia query`
runs in milliseconds against a 100k-block corpus on a laptop.

Operational simplicity. The index is a single `index.sqlite` file.
It's rebuildable from scratch in seconds. It's transactional — a
crash mid-refresh leaves the prior index intact. There are no
"vector store snapshots" to manage.

Citation honesty. BM25 returns block-level results with a numeric
score the agent can paste into an output. Embedding ranking returns
"this is similar" with no auditable trail.

## Alternatives considered

**Vector embeddings as the default.** Rejected for the determinism
and cost reasons above. The "semantic recall" advantage mostly evaporates
when blocks are heading-scoped (a heading is itself a strong semantic
signal); when it doesn't, an opt-in extras package is the right
escape valve.

**Hybrid (BM25 + embeddings combined at rank time).** Rejected for
phase 1 — the complexity is real, and we have no evidence the
combined ranking beats BM25 alone on the workloads we care about.
Revisit if real corpora show systematic recall failures.

**Postgres + pgvector.** Rejected — adds a runtime dep and an
operational footprint that violates [[Stdlib-Only Runtime]].

## Consequences

Positive:

- Deterministic, reproducible queries by construction.
- Zero runtime deps for retrieval.
- Sub-millisecond latency over 100k blocks.
- Human-readable index file (open in any SQLite browser).

Negative:

- No "fuzzy semantic match" out of the box. A query for "auth flow"
  won't surface a block titled "permission gating" unless the words
  overlap.
- Multi-language corpora need careful tokenizer tuning (the default
  `unicode61` covers most cases but isn't optimal for CJK or
  agglutinative languages).

Mitigation: phase-5 optional embeddings extras. The default stays
deterministic; teams that need semantic recall opt into the cost.

## Supersedes / superseded by

None. This is the foundational retrieval decision.
