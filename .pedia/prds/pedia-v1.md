---
type: prd
id: pedia-v1
status: shipped
release: v0.3.0
---

# PRD — Pedia v1 (phase-1 release)

What Pedia delivers to users and agents in its first release.

The product framing — for the design framing see
[[north-stars/01-deterministic-knowledge-for-agents.md]]; for the
governing principles see [[constitution/technical.md]] and
[[constitution/product.md]].

## Audience

- **Primary:** LLM agents executing tasks against a project corpus.
  They run `pedia query` and `pedia show` and paste citations into
  their outputs.
- **Secondary:** Humans authoring or reviewing the corpus. They edit
  markdown files; they browse via `pedia web`.

## The five use cases

The README's framing list, with each use case grounded in the spec
that delivers it.

### 1. Software specifications + internal docs

Specs, PRDs, technical requirements, ADRs, constitution, north stars.
Agents cite the exact block they worked from; spec drift detection
fires when documents change under in-flight work; constitutional
invariants auto-advertise via universal-context.

Delivered by: [[specs/001-indexing-engine/spec.md#design]] (the engine),
[[specs/002-symbol-linking/spec.md#index-tables]] (the cross-references),
[[decision:0004-universal-context-layer]] (the auto-prepend).

### 2. Research notebook

Paper summaries, experiment logs, hypotheses, lit reviews.
`[[Hypothesis: fast-path saturates]]` wiki-links hypotheses across
notes; the trace graph surfaces support vs contradiction.

Delivered by: [[specs/002-symbol-linking/spec.md#defines-front-matter]]
(named-term resolution) + the trace tables in
[[specs/001-indexing-engine/spec.md#sqlite-schema]].

### 3. Business strategy + company wiki

OKRs, strategic bets, ADRs as first-class for *business* choices.
`pedia show --universal-context` + `pedia query` walks the new
executive through the full decision chain in one session.

Delivered by: [[decision:0004-universal-context-layer]] (the auto-
prepend); the engine doesn't care that the doc-types are
"business-flavored" rather than "engineering-flavored."

### 4. Personal second brain (PKM)

Book notes, journal entries, learnings. Obsidian-style wiki-linking
with deterministic agent access.

Delivered by: [[specs/002-symbol-linking/spec.md#link-forms]] (all four
link forms work for PKM as well as code).

### 5. Fiction / game world-building

Characters, locations, lore, plot threads. `[[Prince Elwin]]`
resolves across every scene. `pedia check` finds continuity errors.

Delivered by: [[specs/002-symbol-linking/spec.md#defines-front-matter]] +
[[specs/001-indexing-engine/spec.md#design]] (`pedia check` exits non-zero on
unresolved links — treat them as continuity bugs).

## What ships in v1

- Block indexing, FTS5 + BM25 retrieval, content-hash IDs,
  incremental refresh ([[Spec 001 — Indexing Engine]]).
- Wiki-style symbol linking with four forms + `defines:` front-matter
  + opt-in auto-link ([[Spec 002 — Symbol Linking]]).
- Backfill spider (filesystem) + crawler (websites with
  `--url`) ([[Spec 003 — Backfill]]).
- Universal-context layer
  ([[decision:0004-universal-context-layer]]).
- `pedia check` schema + link validation.
- `pedia hooks install` for git + Claude Code.
- `pedia web` read-only wiki view.

## What's deferred

- Vector-embedding extras (phase 5; opt-in).
- Cross-project federation (phase 5).
- Authoring IDE integrations beyond what an editor's markdown
  features already provide.
- Live-collaboration / multi-user editing on the wiki view (the wiki
  is read-only by design — see [[Read Through CLI Write Through Markdown]]).

## Success metrics

- Agents pasting `[block:...]` citations in outputs is the dominant
  citation form (vs file:line).
- `pedia check` is in CI for every Pedia-using project; PRs that
  break links are caught before merge.
- New onboarding (human or agent) goes "read universal context, then
  query as needed" — no separate "read these 12 wiki pages" handout.
- The `.pedia/` directory is editable in any markdown editor; no
  team complains about lock-in.
