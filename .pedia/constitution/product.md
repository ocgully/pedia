---
type: constitution
chapter: product
status: active
universal_context: true
defines: [Agent-First Human-Approachable, Block-Level Atomic Unit, Read Through CLI Write Through Markdown, Integrate Don't Replace]
---

# Pedia Constitution — Product

These are the product principles that govern what Pedia is *for* and
how it relates to the rest of an agent-equipped project. They sit
alongside [[constitution/technical.md]] and apply to every feature
decision.

This chapter is flagged `universal_context: true`. Agents see it on
every query.

## Agent-First, Human-Approachable

The first-class consumer is an LLM agent running `pedia query`. The
output format, the response shape, and the universal-context layer
all exist to keep an agent's context window cheap and its citations
honest.

But the storage is plain markdown on disk. A human can edit a spec in
their text editor, commit it, and watch `pedia refresh` re-index the
change. The web view (`pedia web`) renders the same content for
humans — read-only by design, no separate authoring path, no special
storage format.

The order matters: **agent-first, then human-approachable.** When
those tensions, the agent path wins. Example: response structure
("universal context, then matches, then see-also") is optimized for
piping into an LLM prompt; humans see the same shape and adapt.

## Block-Level Is the Atomic Unit

Pedia indexes **blocks**, not documents. A block is a heading-section,
an explicit anchor region, or a line-range. Every block has its own
content hash, its own row in the index, its own URL in the web view.

This is because:

- Agent context windows are finite. Returning a whole 5,000-line spec
  to answer a 50-token question is malpractice.
- Citations must be precise. "I cited spec 042" is too coarse; "I
  cited block:abc123 at spec 042 / Flow Network" is auditable.
- Trace graphs only work at block level. Document-level provenance
  collapses into "everything cites everything."

Documents exist as containers — they group blocks, share front-matter,
hold ordering. The unit of meaning is the block.

## Read Through CLI + Wiki View, Write Through CLI + Direct Markdown

There are exactly two read paths and two write paths.

**Reads:**

- `pedia query`, `pedia show`, `pedia get`, `pedia trace` — the agent
  path. Cited, structured, token-budgeted.
- `pedia web` — the human path. Same FTS5 backend, rendered in a
  browser. Read-only.

**Writes:**

- `pedia <create-cmd>` — for content that benefits from validation
  (e.g., `pedia decision record` enforces ADR shape).
- Direct markdown edits — open the file, type, save, commit. `pedia
  refresh` indexes the change automatically via the post-commit hook.

There is **no** separate authoring database, no rich-text editor, no
WYSIWYG, no live-collaboration backend. The source of truth is the
markdown file. The index is derivative — rebuildable from scratch
at any time.

This means version control is the version control. `git log` on a
spec file is the spec history. There is no parallel system to keep in
sync.

## Integrate, Don't Replace

Pedia is one of three sibling tools: **mercator** (code structure),
**hopewell** (work ledger), **pedia** (knowledge).

The discipline is to integrate cleanly with the others — and with
SpecKit, ADR repos, and existing markdown trees — rather than absorb
them.

Practical consequences:

- `pedia speckit import` reads SpecKit-format trees as-is. No
  conversion required.
- `pedia backfill` adopts existing READMEs, `docs/`, `decisions/`,
  ADRs from any of the common conventions.
- Hopewell's work nodes can cite Pedia blocks (`hopewell spec-ref add
  <node> --pedia <block-id>`); Pedia never replaces Hopewell's ticket
  storage.
- Mercator's contracts and symbols can be ingested as Pedia blocks of
  type `contract`/`symbol` — code is documentation.

When a new tool appears in the ecosystem, the question is "what does
Pedia link to?" not "should we absorb this into Pedia?"

Pedia is glue plus index, not a platform.
