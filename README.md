# pedia

**Deterministic knowledge + specs + context for LLM agents.**

Pedia is the third leg of the agent-infrastructure tripod:

| Product | Answers |
|---|---|
| [mercator](https://github.com/ocgully/mercator) | What is the shape of the code? Where does X live? What depends on Y? |
| [hopewell](https://github.com/ocgully/hopewell) | What work is happening? Who owns it? What's blocked? |
| **pedia** | What do we know? What have we decided? What are we trying to build, and why? |

Each is a CLI + storage convention. Each is deterministic (structured data, not "ask the LLM"). Each is agent-first.

See the authoritative design document in AgentFactory at `patterns/drafts/pedia-plan.md` for the full picture. Phase-1 scope (this release) covers indexing, symbol linking, traceability, incremental refresh, and Claude Code / git hook integration. The human-facing web UI is explicitly phase-2 (HW-0046).

---

## Install

```bash
pip install pedia
```

Zero runtime dependencies. Python 3.10+.

## Quickstart

```bash
cd my-project
pedia init --with-examples        # scaffolds .pedia/ + appends .claudeignore
pedia refresh                     # builds .pedia/index.sqlite
pedia query "flow network"        # BM25 search, block-level output
pedia show --for HW-0042          # everything relevant to a work item
pedia trace <block-id> --up       # what led to this?
pedia trace <block-id> --down     # what cites this?
pedia check                       # validate schemas + wiki links
pedia hooks install --git --claude-code --scope project
```

## CLI reference (phase 1)

```
pedia init [--with-examples]
pedia add --type {spec|decision|north-star|constitution|prd|tr|plan|documentation} --path <file>
pedia query <search> [--type T] [--scope universal] [--token-budget N] [--exclude ID,ID] [--format text|json]
pedia get <block-id> [--format text|json]
pedia show --for <HW-id|path> [--format text|json]
pedia trace <block-id> --up|--down [--depth N] [--format text|json]
pedia refresh [--full] [--docs <glob>]
pedia check
pedia hooks install [--git] [--claude-code] [--settings-path PATH] [--scope user|project]
pedia hooks uninstall [--git] [--claude-code]
pedia block-id <path>[:heading]
```

## Response shape

Every query / show response has three sections, LLM-calibrated:

```
=== universal context ===
[block:u1] constitution/technical.md @ "Determinism Over Magic"
<body>

=== matches (3, budget used 1874/2000 tokens) ===
[block:m1] specs/042-canvas/spec.md @ "Flow Network" (score 0.87)
<body>

=== see also ===
[block:r1] (cited by m1) decisions/0003-node-shapes.md @ "Handle strategy"
```

Universal-context blocks (`universal_context: true` in front-matter) are always prepended, regardless of query. Token budgeting uses the `4 chars ~= 1 token` approximation -- document `token_approx_chars_per_token` in `.pedia/config.yaml` if your corpus skews otherwise.

`--format json` returns the same information as a structured object.

## Symbol linking

Wiki-style inline links resolve through the block index:

```markdown
The [[Flow Network]] is the executor topology -- see [[Executor]] and
[[decision:0001-content-hash-block-ids]]. For the routing section,
[[specs/042-canvas/spec.md#flow-network|the canvas spec]] is authoritative.
The [[block:abc123...]] formalizes this.
```

Four forms:

1. `[[Term]]` - declare via `defines: [Term, ...]` in a block's front-matter
2. `[[type:slug]]` - e.g. `decision:0001-...` -> `.pedia/decisions/0001-....md`
3. `[[path#heading]]` - relative to `.pedia/`, anchors on the slugified heading
4. `[[block:<id>]]` - direct content-hash lookup

`pedia check` surfaces unresolved and ambiguous links.

## Architecture

See [`docs/architecture.md`](docs/architecture.md). TL;DR:

- SQLite + FTS5 (stdlib `sqlite3`)
- Block IDs are SHA-256 content hashes (first 16 hex chars)
- BM25 ranking; structured filters are first-class (`--type`, `--scope`)
- Incremental refresh keyed on (mtime, content-hash)
- `.pedia/` directory is gitignored from agent read access via `.claudeignore`; agents MUST query via the CLI

## License

Apache-2.0. See [`LICENSE`](LICENSE).
