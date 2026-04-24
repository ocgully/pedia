---
type: plan
id: 001-indexing-engine-phase-1
spec: 001-indexing-engine
status: implemented
phase: 1
---

# Plan — Phase 1 implementation of the indexing engine

The phase-1 implementation plan that delivered
[[specs/001-indexing-engine/spec.md#design]]. Captured here as a record of
what shipped and the order it shipped in.

See [[Spec 001 — Indexing Engine]] for the requirement,
[[decision:0001-sqlite-fts5-over-embeddings]] for the engine choice,
[[decision:0002-content-hash-block-ids]] for the ID scheme.

## Tasks

1. **Repo scaffolding.** Single `pedia` package, `pyproject.toml`
   with no runtime deps (per [[Stdlib-Only Runtime]]), pytest harness.
2. **Front-matter parser.** Hand-rolled YAML subset (key/value, list,
   nested map). Tests against fixtures.
3. **Markdown block splitter.** Heading-section + explicit-anchor +
   line-range fallback. Tests for each path and each interaction.
4. **Content hash.** `sha256(normalize(body))[:16]`. Normalization
   strips trailing whitespace per line and surrounding blank lines.
5. **SQLite schema.** `blocks`, `blocks_fts`, `refs`, `symbols`,
   `wiki_links`, `doc_index`. Indexes on `(doc_path)`, `(universal)`,
   `(refs.dst_id)`, `(wiki_links.dst_id)`.
6. **`pedia init` command.** Creates `.pedia/` skeleton; appends to
   `.claudeignore`. Calls backfill if the repo has discoverable docs.
7. **`pedia refresh` command.** Walks `.pedia/**/*.md`, stat-checks
   `mtime_ns` against `doc_index`, re-indexes changed docs in a
   single transaction per doc.
8. **`pedia query` command.** FTS5 BM25 over `blocks_fts`. Universal-
   context prepend. Token-budget planner. Text + JSON output.
9. **`pedia get` command.** Block-ID lookup. Stable across runs.
10. **`pedia show --for` command.** Composed result for a Hopewell
    node ID or doc path; reuses query + trace internals.
11. **`pedia trace` command.** `--up` and `--down` walks of
    `wiki_links` + `refs`, depth-bounded.
12. **`pedia check` command.** Front-matter schema validation +
    unresolved/ambiguous wiki-link detection. Non-zero exit on
    errors.
13. **`pedia hooks install`.** Git post-commit + Claude Code
    settings integration. Post-commit runs `pedia refresh`.
14. **End-to-end smoke**: scaffold, refresh, query, show, trace,
    check on a fixture corpus. Round-trip to JSON for programmatic
    callers.

## Done when

- All 14 tasks shipped.
- `pedia refresh` on a 1k-block fixture completes in < 200 ms.
- `pedia check` exits 0 on the fixture, non-zero when a fixture is
  edited to introduce an unresolved link.
- README quickstart works from a clean repo with zero pip deps
  beyond `pedia` itself.

## Phase-2 carryovers

These were considered for phase 1 and deferred:

- Watch-mode (`pedia refresh --watch`). Commit-hook coverage is
  sufficient for now.
- Cross-project federation. No demand yet.
- Embedding-assisted retrieval extras. Phase 5 — see
  [[decision:0001-sqlite-fts5-over-embeddings]].
