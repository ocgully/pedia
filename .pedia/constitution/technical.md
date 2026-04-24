---
type: constitution
chapter: technical
status: active
universal_context: true
defines: [Determinism Over Magic, Cite Everything, Universal Context, Stdlib-Only Runtime, Offline-First, Content-Hash Block ID]
---

# Pedia Constitution — Technical

These are the technical principles that govern how Pedia is built and
how it behaves. They apply to every release, every contributor, every
agent that authors content. Amendments require an ADR; see
[[decision:0001-sqlite-fts5-over-embeddings]] for the canonical
example of a constitutional decision.

This chapter is flagged `universal_context: true` — every `pedia
query` and `pedia show` prepends it automatically. Agents do not
forget these principles between sessions.

## Determinism Over Magic

Pedia returns the same answer for the same query against the same
corpus state. Always.

This rules out:

- Vector embeddings as the primary retrieval path. Embedding ranking
  drifts with model versions and floating-point variance. See
  [[decision:0001-sqlite-fts5-over-embeddings]].
- "Smart" query rewriting that reinterprets the user's terms.
- Any retrieval step that consults an LLM at query time.

It mandates:

- BM25 over an FTS5 index — deterministic, well-understood, documented
  ranking.
- Content-hash block IDs — same body produces the same ID.
- Structural filters (`--type`, `--scope`) as first-class — narrow
  before you rank.

Embeddings may someday ship as an *optional secondary index* for
specific corpora that need fuzzy semantic recall. They will never
replace BM25 as the default.

## Cite Everything

Every result Pedia returns is tagged with its block ID. Agents paste
citations into their outputs. Humans audit the trail.

The output contract:

```
[block:abc123] FROM specs/042-foo/spec.md @ "## Flow Network"
<body>
```

This is non-negotiable. A query response without citations is a bug,
not a feature request.

Citations enable:

- Drift detection — when a cited block's content changes, downstream
  consumers can be flagged.
- Trace graphs — `pedia trace --up/--down` walks citations.
- Reproducibility — re-running an agent's session reproduces the same
  cited evidence.

## Universal Context Advertises Itself

A small number of high-leverage blocks (this chapter is one) carry
`universal_context: true` in their front-matter. Every query and every
`pedia show` prepends them, regardless of what the user asked.

The discipline: **few in number, small in size, stable over time.**
Target is < 10 universal blocks per project; review on every refresh.
The cost is paid every query, so the budget is tight.

See [[decision:0004-universal-context-layer]] for the rationale and
the tradeoffs.

## Stdlib-Only Runtime

Pedia ships zero runtime dependencies. Python 3.10+ stdlib only:
`sqlite3`, `urllib`, `html.parser`, `argparse`, `hashlib`, `json`,
`http.server`. Everything else is bundled or hand-rolled.

This is a constraint, not an aesthetic. It buys:

- One-line install (`pip install pedia`) on any machine with Python.
- Offline operation — no PyPI hits at run time.
- Portability — the same binary works on a developer laptop, a CI
  worker, and an air-gapped server.
- Long-term stability — fewer versions to track in security advisories.

The cost: some features (YAML parsing, markdown rendering for the web
view via esm.sh) cost more engineering effort up front. We pay it
once. See [[decision:0003-stdlib-only-runtime]].

Optional extras are allowed for clearly-bounded paths — e.g., a future
embeddings add-on may depend on `numpy`. Extras are opt-in via
`pip install pedia[<extra>]` and never load on the default code path.

## Offline-First

Pedia operates fully without network access. The CLI, the index, and
the read-mostly web view all run from local files.

Network access is permitted only on explicit opt-in:

- `pedia backfill --url <seed>` crawls a docs site (respecting
  `robots.txt`, depth-bounded, same-origin BFS).
- The web view's external-link templates surface URLs but never fetch
  them.

Default = offline. The product must work on a plane.

## Content-Hash Block IDs Are the Identity

Every block's ID is `sha256(normalize(body))[:16]` — a 16-hex-character
prefix of a stable content hash. Same content, same ID, forever.

Consequences:

- IDs **shift when content changes**. A typo fix produces a new block
  ID. This is intentional — content drift produces ID drift, which is
  observable and citable.
- For *stable cross-references across edits*, use the other wiki-link
  forms ([[Term]], [[type:slug]], [[path#heading]]). Block-IDs are
  strongest for "I want to cite the exact bytes I read."
- Normalization (trim trailing whitespace, strip surrounding blank
  lines) absorbs trivial edits without ID churn.

See [[decision:0002-content-hash-block-ids]] for the alternatives we
rejected (monotonic counters, UUIDs) and why.
