---
type: north-star
id: 01-deterministic-knowledge-for-agents
status: active
defines: [Deterministic Knowledge, Block Index, Agent-First Knowledge Base]
---

# Deterministic Knowledge for Agents

LLM agents need **deterministic, citable, structured access to project
knowledge**. Pedia exists so agents query a block index instead of
grepping a tree or guessing with embeddings.

## The problem

A working agent in a real project has to answer questions like:

- *What are we building, and why?*
- *Which decision governs the way I'm about to write this code?*
- *What does the constitution say about determinism?*
- *Which spec block does this ticket consume?*

Today the agent has three bad choices:

1. **Grep the tree.** Every session re-pays the cost of locating the
   right file. Non-deterministic — the matches depend on phrasing and
   tooling, not the corpus state.
2. **Load the whole file.** Burns context tokens on irrelevant prose.
   Encourages "summarize this" hallucination.
3. **Embed-search.** Opaque ranking. Same question can return
   different blocks across runs. Nothing to cite.

None of these are reproducible. None give the agent a stable way to
*cite* what it used. None advertise the cross-cutting principles the
agent must obey.

## The bet

Pedia replaces those three with **a block index** — every markdown
document in `.pedia/` is sliced into addressable, content-hashed
blocks; every block carries a stable ID; every query returns blocks
with citations attached. See [[specs/001-indexing-engine/spec.md#design]] for
the engine; see [[decision:0001-sqlite-fts5-over-embeddings]] for why
the ranking is BM25 rather than vector cosine.

When an agent asks Pedia a question, it gets:

1. **Universal context first** — the project's constitutional
   principles auto-prepend, regardless of query (see
   [[decision:0004-universal-context-layer]]).
2. **Block-level matches** — the specific section that answers the
   question, not the whole document.
3. **Citations** — every block carries `[block:id]` so the agent can
   paste the citation into its output.
4. **Trace links** — what cited this block, what this block cites,
   walkable in either direction.

Same question + same corpus = same cited answer, every time. That is
the definition of "deterministic" for this product.

## Why this matters more than it seems

Agent-pair workflows fail more often from forgotten constraints than
from missing knowledge. The constitution says "no runtime deps"; the
agent ships with a runtime dep because it never re-read the
constitution this session. Universal context fixes this *structurally*
— the agent can't avoid seeing the constraint.

Pedia is also the only place where decisions live as queryable, cited
artifacts rather than dusty ADR files. A new agent (or human) running
`pedia show --universal-context` and `pedia query "<topic>"` gets the
full why-trail in one session.

## Success looks like

- Agents cite block IDs in their outputs by default.
- `pedia check` shows zero unresolved wiki-links — the corpus is
  internally consistent.
- The constitution surfaces in every research step without being
  asked.
- Onboarding (human or agent) is "read the universal context, then
  query the rest" — not "read the README and hope."

The whole rest of Pedia — the indexing engine, the symbol linker, the
backfill spider, the CLI surface — is in service of this north star.
