---
type: spec
id: 002-symbol-linking
status: implemented
phase: 1
defines: [Symbol Linking, Wiki Link, Auto-Link, Flow Network]
---

# Spec 002 — Symbol Linking

Pedia treats `[[wiki-style-links]]` as a first-class feature.
Documents cross-reference each other; the linker resolves those
references through the block index and stores them as edges that
`pedia trace` can walk.

This spec elaborates on `patterns/drafts/pedia-plan.md` §15. See also
[[specs/001-indexing-engine/spec.md#sqlite-schema]] for the index tables this
spec extends.

## Goals

- Four link forms with increasing specificity.
- `defines:` front-matter as the authoritative way to introduce a
  named term.
- `auto_link: true` as an opt-in convenience for prose-heavy docs.
- `pedia check` surfaces unresolved or ambiguous links as errors
  (not warnings — see [[Cite Everything]]).

## Link forms

Four forms, increasing specificity:

1. **`[[Term]]`** — looks up the canonical block whose front-matter
   declares `defines: [Term]`. Exactly one match → resolved; multiple
   matches → ambiguous (`pedia check` errors); zero matches →
   unresolved (`pedia check` errors).
2. **`[[type:slug]]`** — resolves against the typed path:
   `decision:0001-sqlite-fts5-over-embeddings` →
   `.pedia/decisions/0001-sqlite-fts5-over-embeddings.md`.
3. **`[[path#heading]]`** — relative to `.pedia/` root; anchors on the
   heading slug. Survives content edits as long as the heading text
   doesn't change.
4. **`[[block:<id>]]`** — direct block-ID reference. Strongest for
   "I want to cite the exact bytes I read"; weakest for "I want this
   to keep working after edits."

Optional display text: `[[Target|display]]`. The rendered text becomes
"display"; the link target is `Target`.

### Stability vs precision tradeoff

Block-IDs are stable as long as content is unchanged but shift on
edits. `[[path#heading]]` and `[[type:slug]]` survive content edits
but break on path/heading renames. Author guidance:

- Use `[[Term]]` for concepts that live in a constitution or
  glossary-style doc.
- Use `[[type:slug]]` for ADRs and specs (the slug is part of the
  filename and rarely changes).
- Use `[[path#heading]]` for fine-grained references inside a doc.
- Use `[[block:<id>]]` only when you genuinely need the exact bytes.

## `defines:` front-matter

A block introduces named terms by listing them in `defines:`:

```markdown
---
defines: [Flow Network, Executor, Route]
---
# Flow Network

An executor topology where nodes route work along directed edges...
```

The first block in document order that declares `defines: [X]` becomes
the canonical target for `[[X]]`. Later declarations of the same term
in any doc trigger a `pedia check` ambiguity error.

### Example: defining and citing "Flow Network"

This block defines the term **Flow Network**. Other blocks anywhere in
this corpus can cite it as `[[Flow Network]]` and the linker resolves
to here. The constitution's [[Determinism Over Magic]] block uses the
same mechanism — an agent reading "we choose BM25 over embeddings"
can click through to the definition.

## Auto-link

```markdown
---
auto_link: true
---
# Spec ...
```

When `auto_link: true`, the renderer scans prose for matches against
the symbol table and wraps them in `[[...]]` links at render/query
time. The markdown source stays unmodified — humans don't see the
brackets in their editor.

Auto-link skips:

- Code blocks (fenced and inline).
- Front-matter.
- Existing `[[...]]` links.
- Headings shorter than 3 characters.
- Inside `<!-- pedia:no-autolink -->` comments.

Auto-link is **opt-in per doc**, not a global default. In docs with
heavy code or technical jargon, false positives are likely; turn it
on only where prose dominates.

## Index tables

```sql
CREATE TABLE symbols (
  term          TEXT NOT NULL,
  canonical_id  TEXT NOT NULL,
  PRIMARY KEY (term, canonical_id),
  FOREIGN KEY (canonical_id) REFERENCES blocks(id) ON DELETE CASCADE
);

CREATE TABLE wiki_links (
  src_id  TEXT NOT NULL,
  dst_id  TEXT,                 -- NULL when unresolved
  raw     TEXT NOT NULL,        -- original [[raw text]]
  form    TEXT NOT NULL,        -- term | type-slug | path-heading | block-id
  PRIMARY KEY (src_id, raw),
  FOREIGN KEY (src_id) REFERENCES blocks(id) ON DELETE CASCADE
);

CREATE INDEX idx_wiki_dst ON wiki_links(dst_id);
```

`pedia trace --up <id>` joins on `wiki_links.dst_id`; `pedia trace
--down <id>` joins on `wiki_links.src_id`. The graph walk is depth-
bounded.

## Acceptance

- A doc declaring `defines: [Foo]` becomes the canonical target for
  `[[Foo]]` everywhere.
- A second doc declaring `defines: [Foo]` triggers a `pedia check`
  ambiguity error.
- `pedia check` exits non-zero when any unresolved or ambiguous link
  exists.
- `pedia trace` walks the graph using the `wiki_links` table.
