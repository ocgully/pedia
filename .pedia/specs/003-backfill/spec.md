---
type: spec
id: 003-backfill
status: implemented
phase: 1
defines: [Backfill, Filesystem Spider, Website Crawler, Backfill Source Hash]
---

# Spec 003 — Backfill

`pedia backfill` adopts an existing project. It walks the repo (and
optionally crawls a docs site), classifies each source to a Pedia doc
type, and writes the content under `.pedia/` with a stable
`backfill_source_hash` so re-runs are idempotent.

This spec elaborates on `patterns/drafts/pedia-plan.md` §14 and the
README's "Backfill — adopt an existing repo" section. See also
[[specs/001-indexing-engine/spec.md#design]] (the indexer reads what backfill
writes) and [[Integrate Don't Replace]] (backfill exists because we
adopt rather than replace).

## Goals

- A repo with `README.md` + `docs/architecture.md` + a `decisions/`
  directory becomes a working Pedia corpus in one command.
- Re-running backfill on an unchanged repo is a no-op (idempotent).
- Re-running after editing one source updates exactly that one
  doc's `.pedia/` mirror.
- The crawler respects `robots.txt`, depth limits, and same-origin
  bounds. No surprise external traffic.

## Non-goals

- Two-way sync. Once a source is backfilled, the `.pedia/` copy is
  the working copy. The original is no longer authoritative for
  Pedia's purposes.
- Format conversion beyond markdown. PDFs, docx, HTML get a stub
  with a link to the source; we don't try to extract text.
- Heuristic-driven doc-type guessing for inscrutable filenames. When
  in doubt, the file lands in `docs/imported/`.

## Filesystem spider — heuristics

The spider walks the project root with a fixed-priority classifier:

| Source pattern                                     | Doc type            | Pedia location                       |
|----------------------------------------------------|---------------------|--------------------------------------|
| `specs/NNN-slug/spec.md` (SpecKit)                 | `spec`              | `specs/NNN-slug/spec.md`             |
| `specs/NNN-slug/plan.md`                           | `plan`              | `specs/NNN-slug/plan.md`             |
| `docs/adr/*.md`, `docs/decisions/*.md`, ADR-shaped | `decision`          | `decisions/<slug>.md`                |
| `.specify/memory/constitution.md`, `*tenets*`, `*principles*` | `constitution` | `constitution/<slug>.md`     |
| `*north-star*`, `*charter*`, `*mission*`           | `north-star`        | `north-stars/<slug>.md`              |
| `*vision*`                                         | `vision`            | `vision/<slug>.md`                   |
| `*prd*`, `*product-requirements*`                  | `prd`               | `prds/<slug>.md`                     |
| `*technical-requirements*`, `*non-functional*`     | `technical-requirement` | `technical-requirements/<slug>.md` |
| `README.md`, `CHANGELOG.md`, `CONTRIBUTING.md`, `ARCHITECTURE.md` | `documentation` | `docs/imported/<slug>.md` |
| Anything else under `docs/` or top-level           | `documentation`     | `docs/imported/<path-context>.md`    |

The classifier is conservative — when a source matches multiple
patterns, the most specific (highest in the table) wins. When no
pattern matches, the file is skipped (with a `--report-only`
diagnostic) rather than misclassified.

## Website crawler

`pedia backfill --url <seed>` crawls a docs site:

- **Stdlib only**: `urllib` for HTTP, `html.parser` for HTML, no
  third-party scrapers (see [[Stdlib-Only Runtime]]).
- **`robots.txt`**: fetched once, parsed, obeyed for every URL.
- **BFS** from the seed with a configurable `--depth` (default 3).
- **Same-origin bound**: the crawler never follows off-domain links.
- **Content-type detection**: `text/html` → strip to markdown via a
  hand-rolled converter; `text/markdown` → pass through; everything
  else → skip (with a report entry).
- **Rate**: a polite default of 1 request per 250 ms; configurable.

URLs that fetch successfully become `documentation`-typed docs in
`.pedia/docs/imported/<host>/<path>.md`. The crawler is offline-by-
default — `--url` is the only opt-in (see [[Offline-First]]).

## Idempotency via `backfill_source_hash`

Every backfilled doc carries front-matter:

```yaml
---
type: documentation
backfill_source: docs/architecture.md
backfill_source_hash: 8927498adae0b052
---
```

`backfill_source_hash` is `sha256(source_bytes)[:16]`. On re-run, the
spider re-reads each source, recomputes the hash, and skips writes
when it matches. Edits to the source produce a new hash and a fresh
write of the `.pedia/` copy.

### What about edits to the `.pedia/` copy?

Once backfilled, the `.pedia/` copy is the canonical version. Edits
there are independent of the source. To re-sync from the source, the
operator deletes the `.pedia/` copy and re-runs `pedia backfill` —
explicit overwrite, never silent.

## CLI surface

```
pedia backfill                        # default: spider this repo
pedia backfill --source DIR           # spider a different root
pedia backfill --url URL              # add website crawl
pedia backfill --depth N              # crawl depth (default 3)
pedia backfill --dry-run              # preview the plan, write nothing
pedia backfill --report-only          # classify all sources without writing
```

## Acceptance

- Running `pedia init` on a repo with a README + `docs/` produces a
  populated `.pedia/` with no further commands.
- Re-running `pedia backfill` on an unchanged repo writes zero files.
- Editing the source README and re-running `pedia backfill` updates
  exactly that one doc.
- A `pedia backfill --url <seed>` run respects robots.txt, stays on
  the same origin, and exits cleanly when the seed returns 404.
