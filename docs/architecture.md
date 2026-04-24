# Pedia architecture (phase 1)

This document expands sections 14 and 15 of the master plan at
AgentFactory's `patterns/drafts/pedia-plan.md`. Read that first for
scope + design principles; this doc covers the implementation.

---

## 1. Storage layout

Every project gets a `.pedia/` directory at its root:

```
.pedia/
├── north-stars/            # type: north-star
├── vision/                 # type: vision
├── constitution/           # type: constitution
├── specs/<slug>/           # spec.md, plan.md, prd.md (type: spec|plan|prd)
├── prds/                   # standalone PRDs (type: prd)
├── technical-requirements/ # type: technical-requirement
├── decisions/              # ADRs (type: decision)
├── plans/                  # standalone plans
├── docs/                   # tutorials / how-to / reference / explanation
├── config.yaml             # project config
└── index.sqlite            # the derivative index (rebuildable)
```

`.pedia/` is `.claudeignore`d. Agents must NOT read the tree; they
MUST use the `pedia` CLI. The content is LLM-calibrated output, not
human-grep output.

## 2. Parsing (markdown -> blocks)

A block is an addressable region of a document with three possible
origins (in priority order when they overlap):

1. **Explicit anchor** - `<!-- pedia:block:slug -->` ... `<!-- pedia:/block -->`
2. **Heading section** - ATX heading + content through the next heading at equal-or-higher level
3. **Line-range** - only when declared in front-matter: `blocks: [[45, 72]]`

Fallback: a document with neither headings nor anchors becomes a single
whole-document block.

Each block carries:

- `id` - `sha256(normalize(body))[:16]` (16-char hex prefix)
- `doc_path` - relative to `.pedia/`
- `doc_type` - `spec | decision | north-star | constitution | prd | technical-requirement | plan | documentation | vision`
- `heading_slug`, `heading_level` - when applicable
- `line_start`, `line_end` - 1-indexed, in the original source
- `content` - the raw block body (markdown)
- `universal` - flagged via `universal_context: true` in front-matter
- `token_estimate` - `ceil(len(content) / 4)`
- `meta_json` - full front-matter + computed metadata

Normalization for the content hash:

```
body_normalized = "\n".join(line.rstrip() for line in body.splitlines()).strip("\n")
block_id         = sha256(body_normalized)[:16]
```

This gives trivial-edit stability (whitespace changes don't shift IDs)
without needing stateful authoring tooling.

## 3. SQLite schema

```sql
CREATE TABLE blocks (
  id              TEXT PRIMARY KEY,
  doc_path        TEXT NOT NULL,
  doc_type        TEXT NOT NULL,
  heading_slug    TEXT,
  heading_level   INTEGER,
  line_start      INTEGER,
  line_end        INTEGER,
  content         TEXT,
  universal       INTEGER DEFAULT 0,
  token_estimate  INTEGER,
  meta_json       TEXT,
  indexed_at      TEXT NOT NULL
);

CREATE VIRTUAL TABLE blocks_fts USING fts5(
  content,
  heading_slug,
  doc_type UNINDEXED,
  meta_json UNINDEXED,
  tokenize='porter unicode61'
);

CREATE TABLE refs (
  src_id TEXT NOT NULL,
  dst_id TEXT NOT NULL,
  kind   TEXT NOT NULL,   -- cites | supersedes | amends | implements
  PRIMARY KEY (src_id, dst_id, kind)
);

CREATE TABLE symbols (
  term          TEXT NOT NULL,
  canonical_id  TEXT NOT NULL,
  PRIMARY KEY (term, canonical_id)
);

CREATE TABLE wiki_links (
  src_id  TEXT NOT NULL,
  dst_id  TEXT,           -- NULL when unresolved
  raw     TEXT NOT NULL,
  form    TEXT NOT NULL,  -- term | type-slug | path-heading | block-id
  PRIMARY KEY (src_id, raw)
);

CREATE TABLE doc_index (
  doc_path     TEXT PRIMARY KEY,
  mtime_ns     INTEGER,
  content_hash TEXT,
  indexed_at   TEXT
);

CREATE INDEX idx_blocks_doc  ON blocks(doc_path);
CREATE INDEX idx_blocks_univ ON blocks(universal);
CREATE INDEX idx_refs_dst    ON refs(dst_id);
CREATE INDEX idx_wiki_dst    ON wiki_links(dst_id);
CREATE INDEX idx_wiki_src    ON wiki_links(src_id);
CREATE INDEX idx_symbols_term ON symbols(term);
```

Note that `blocks_fts` is a contentless FTS5 virtual table keyed by
`rowid` to `blocks.rowid`. We synchronize it explicitly on inserts
rather than using triggers -- keeps the data flow obvious.

## 4. Incremental refresh

Two triggers:

1. **On-demand**: `pedia refresh` walks `.pedia/**/*.md`, stat-checks
   mtime vs `doc_index.mtime_ns`, and re-parses only docs whose
   mtime changed AND whose content-hash changed.
2. **Session-scoped**: `pedia refresh --only-changed-in-session` runs
   `git diff --name-only HEAD -- .pedia/` + `git ls-files --others
   --exclude-standard -- .pedia/` to find docs touched in the current
   session, and re-parses only those.

Full refresh (`--full`) drops all tables and rebuilds from scratch.

Wiki-link registration runs in two passes so that `[[Term]]` references
in doc A can resolve to a `defines:` in doc B regardless of indexing
order:

- Pass 1: parse every dirty doc, write blocks + `symbols` rows
- Pass 2: scan blocks for `[[...]]` patterns, write `wiki_links` rows,
  mirror resolved links into `refs` as `cites` edges

## 5. Query (BM25 + filters + response shape)

`pedia query <search>` does:

1. Fetch every `universal = 1` block, token-budget up to `universal_reserve`, emit as `universal`
2. Run FTS5 `MATCH` against `blocks_fts`, order by `bm25()` ascending (lower is better), convert to a displayable `1 / (1 + |bm25|)` score
3. Apply `--type`, `--exclude`, `--limit` filters
4. Token-budget match results; skip blocks whose `token_estimate` would overflow (keep trying smaller ones)
5. For the top-5 matches, follow `refs` in both directions, dedupe against matches + exclude, emit up to 10 as `see_also`

`--format text` renders the three sections for direct piping into agent
prompts. `--format json` returns the same structure as JSON.

## 6. Symbol resolution

`pedia check` validates wiki links and flags:

- **Unresolved**: `wiki_links.dst_id IS NULL`
- **Ambiguous**: a term in `symbols` with multiple `canonical_id`s

`auto_link: true` in a doc's front-matter is honored at render time
only -- the markdown source is never modified on disk. Phase 1's CLI
surfaces `auto_link` as metadata; the phase-2 web UI will consume it.

## 7. Hook integration

`pedia hooks install --claude-code [--scope user|project] [--settings-path PATH]`
writes Stop + SubagentStop hooks into Claude Code's `settings.json`,
marked with the `# pedia:managed` sentinel for round-trip
uninstall. Each hook runs:

```
pedia refresh --only-changed-in-session 2>/dev/null
  || python -m pedia refresh --only-changed-in-session 2>/dev/null
  || true
```

Exit status is always 0 -- hooks never block tool use. Outside a pedia
project (no `.pedia/` walking up from CWD) the command is a silent
no-op.

`pedia hooks install --git` writes `.git/hooks/post-commit` with the
same sentinel, running `pedia refresh` (full, not session-scoped, so
commits from outside a Claude Code session also update the index).
Both triggers are idempotent -- both just call `pedia refresh` with
mtime + content-hash short-circuiting.

## 8. Token approximation

Pedia does not depend on an LLM tokenizer. The approximation
`4 chars ~= 1 token` is applied uniformly (`token_approx_chars_per_token`
in `.pedia/config.yaml` is the knob). Agents that care about exact
token counts re-estimate in their own process; Pedia's budget is a
coarse-but-deterministic ceiling.

## 9. Non-goals in phase 1

- Web UI (phase 2, tracked as HW-0046)
- Embedding search (phase 5 optional add-on)
- Cross-project federation (phase 5)
- SpecKit importer (phase 3)
- Fuzzy / semantic query rewriting
- Live collaboration / write UI
