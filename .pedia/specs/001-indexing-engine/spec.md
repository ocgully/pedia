---
type: spec
id: 001-indexing-engine
status: implemented
phase: 1
defines: [Indexing Engine, Block Parsing, Content Hash ID, Incremental Refresh]
---

# Spec 001 — Indexing Engine

The **block index** is the heart of Pedia. The indexing engine builds
the block index — it walks `.pedia/**/*.md`, slices each document
into blocks, computes a stable content-hash ID per block, and writes
the resulting block index into `.pedia/index.sqlite`. Every other
capability — query, show, trace, the web view — reads from the block
index this engine writes.

The block index is the deterministic substrate the rest of the
product stands on. Querying the block index replaces grep-the-tree
and embed-search with a cited, reproducible lookup. The block index
holds one row per block, an FTS5 mirror of every block's content, a
refs table for citations, a wiki-links table for cross-references,
and a per-doc index for incremental refresh. The block index is
rebuildable from scratch at any time — the markdown files on disk
are the source of truth; the block index is derivative.

See [[north-stars/01-deterministic-knowledge-for-agents.md#the-bet]]
for why a block index is the right shape; see
[[decision:0001-sqlite-fts5-over-embeddings]] for why the block index
ranks results with BM25.

The block index is keyed on content-hash block IDs (see
[[decision:0002-content-hash-block-ids]]); the block index is
incrementally refreshed on commit (see Incremental Refresh below);
the block index ships zero runtime dependencies (see
[[decision:0003-stdlib-only-runtime]]).

This spec elaborates on the design from `patterns/drafts/pedia-plan.md`
§14. See also [[decision:0001-sqlite-fts5-over-embeddings]] and
[[decision:0002-content-hash-block-ids]].

## Goals

- A full refresh of a 10k-block project completes in under 2 seconds
  on a developer laptop.
- An incremental refresh of a single changed document completes in
  under 50 ms.
- Identical corpus → identical index. Bit-for-bit reproducibility is
  not required; same row count, same block IDs, same FTS5 scores
  for the same query is.
- A mid-refresh crash leaves the prior index intact (transactional
  updates).

## Non-goals (phase 1)

- Vector embeddings — see [[decision:0001-sqlite-fts5-over-embeddings]].
- Cross-project federation.
- Watch-mode auto-refresh (we use commit hooks; watching is overkill).

## Design

### Block parsing

A document yields blocks in priority order:

1. **Explicit anchor** — `<!-- pedia:block:slug -->` ... `<!-- pedia:/block -->`
2. **Heading section** — ATX heading + content through the next
   heading at equal-or-higher level.
3. **Line range** — only when declared in front-matter
   (`blocks: [[45, 72]]`).

Fallback: a document with neither headings nor anchors becomes a
single whole-document block.

Each block carries: `id`, `doc_path`, `doc_type`, `heading_slug`,
`heading_level`, `line_start`, `line_end`, `content`, `universal`,
`token_estimate`, `meta_json`, `indexed_at`.

### Content-hash ID

```
body_normalized = "\n".join(line.rstrip() for line in body.splitlines()).strip("\n")
block_id        = sha256(body_normalized)[:16]
```

16 hex characters is the human-readable ID. Trivial whitespace edits
don't shift the ID; substantive content changes do. The reasoning is
captured in [[decision:0002-content-hash-block-ids]].

### SQLite schema

The schema is `blocks` (primary table) + `blocks_fts` (FTS5 virtual
table) + `refs` (semantic edges) + `symbols` + `wiki_links` (see
[[specs/002-symbol-linking/spec.md#index-tables]]) + `doc_index` (per-doc mtime
+ source hash).

`blocks_fts` uses `tokenize='porter unicode61'` for English-and-most-Western
prose. BM25 is the default rank. See [[Determinism Over Magic]] in the
constitution for why this matters.

### Incremental Refresh

`pedia refresh` walks `.pedia/**/*.md`, stat-checks each file's
`mtime_ns` against `doc_index`, and re-parses only changed files.
Per-doc work is wrapped in a single SQLite transaction so a crash
mid-refresh leaves the prior index intact.

Trigger paths:

- **On-demand** — the developer or agent runs `pedia refresh`.
- **Automatic** — `pedia hooks install --git` wires a post-commit hook
  that runs `pedia refresh`. Same pattern Mercator and Hopewell use.

The refresh is idempotent — re-running with no changes is a no-op
that touches zero rows.

### Token estimation

`token_estimate = ceil(len(content) / 4)`. This is the LLM-rule-of-thumb;
configurable per project via `token_approx_chars_per_token` in
`config.yaml`. The number drives `--token-budget` planning at query
time but is never load-bearing for correctness.

## Acceptance

- `pedia refresh` on a fresh `.pedia/` produces an `index.sqlite` with
  one row per block.
- A repeat `pedia refresh` (no file changes) reports `0 added/updated,
  N unchanged, 0 removed`.
- Editing one heading in one doc and re-running refresh affects only
  that doc's blocks.
- `pedia query` returns block-level results with stable IDs across
  runs.

## Open questions

- Sub-block granularity for very long heading sections (10k+ tokens).
  Phase 1 ships with the heading-section block; if real corpora hit
  the limit, add explicit anchors per section.
- Concurrency: two refresh processes racing. Phase 1 says "don't" — a
  file lock or single-writer guarantee is a phase-2 ticket if it
  becomes a real problem.
