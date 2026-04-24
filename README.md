# pedia

**A deterministic knowledge base for LLM agents — your agentic second brain.**

Pedia indexes any corpus of markdown (specs, notes, research, lore, journal entries, policies — anything) at the **block level** with stable content-hash IDs, wiki-style symbol linking, and BM25 search. Agents query it deterministically: same question + same corpus = same cited answer, every time.

## What it's for

Pedia is a general-purpose knowledge tool. Five illustrative use cases:

### 1. Software specifications + internal docs
Specs, PRDs, technical requirements, ADRs, constitution, north stars. Agents cite the **exact block** they worked from; spec drift detection fires when documents change under in-flight work; constitutional invariants auto-advertise via universal-context so agents don't forget them.
```
pedia show --for HW-0042   # spec sections this ticket consumes + cited decisions
pedia trace <block> --up   # what led to this being written?
```

### 2. Research notebook (academic or technical)
Paper summaries, experiment logs, hypotheses, literature reviews. Wiki-link `[[Hypothesis: fast-path saturates]]` across notes; the trace graph surfaces which experiments support vs contradict a hypothesis; your assistant always knows what question you're investigating.
```
pedia query "eventual consistency under partition" --type paper-note,experiment-log
pedia trace <hypothesis-block> --down   # everything that tested this
```

### 3. Business strategy + company wiki
OKRs, strategic bets, competitor analysis, market research, meeting decisions. ADRs are first-class for business choices, not just engineering. A new executive can `pedia show --universal-context` + `pedia query "pricing strategy"` and get the full decision chain in one session — why we bet on X, what signals justified it, who owned it.

### 4. Personal second brain (PKM)
Book notes, journal entries, learnings, quotes, ideas. Obsidian-style wiki-linking, but with **deterministic agent access** — your AI assistant queries via CLI instead of you curating prompts. Universal-context carries your personal north stars ("learning Rust", "writing a novel about X"); the assistant cites which of your notes it's drawing from.
```
pedia query "deliberate practice" --type book-note,journal
```

### 5. Fiction / game world-building
Characters, locations, lore, plot threads, timelines, magic-system rules. Wiki-linking is native — every character and place is a block; `[[Prince Elwin]]` resolves across every scene. Universal-context = world invariants ("magic works only at dawn"). `pedia check` finds continuity errors — unresolved references mean lore gaps.
```
pedia query "Prince Elwin" --type character
pedia trace <plot-thread> --down   # every scene downstream of this beat
```

## The agent-infrastructure trio

Pedia is one of three sibling tools:

| Product | Answers |
|---|---|
| [mercator](https://github.com/ocgully/mercator) | What is the shape of the code? Where does X live? What depends on Y? |
| [hopewell](https://github.com/ocgully/hopewell) | What work is happening? Who owns it? What's blocked? |
| **pedia** | What do we know? What have we decided? What are we trying to build, and why? |

Each is a CLI + storage convention. Each is deterministic (structured data, not "ask the LLM"). Each is agent-first. Software is the first-class use case; nothing about the engine is software-specific.

Phase-1 scope (this release) covers indexing, symbol linking, traceability, incremental refresh, and Claude Code / git hook integration.

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

## CLI reference

```
pedia init [--with-examples] [--backfill | --no-backfill]
pedia backfill [--source DIR] [--url URL] [--depth N] [--dry-run] [--report-only]
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

## Backfill — adopt an existing repo

`pedia init` auto-fires `pedia backfill` when the project already has
discoverable documentation (README, docs/, specs/, ADRs, a SpecKit
`.specify/memory/constitution.md`, etc.). The spider classifies each
source to a Pedia doc type and writes it under `.pedia/`:

| Source                                       | Pedia location                         |
|----------------------------------------------|----------------------------------------|
| `specs/NNN-slug/spec.md` (SpecKit)           | `specs/NNN-slug/spec.md`               |
| `specs/NNN-slug/plan.md`                     | `specs/NNN-slug/plan.md`               |
| `docs/adr/*.md`, `docs/decisions/*.md`, ADR-shaped | `decisions/<slug>.md`            |
| `.specify/memory/constitution.md`, `constitution/**/*.md`, `*tenets*`, `*principles*` | `constitution/<slug>.md` |
| `*north-star*`, `*charter*`, `*mission*`     | `north-stars/<slug>.md`                |
| `*vision*`                                   | `vision/<slug>.md`                     |
| `*prd*`, `*product-requirements*`            | `prds/<slug>.md`                       |
| `*technical-requirements*`, `*non-functional*` | `technical-requirements/<slug>.md`   |
| `README.md`, `CHANGELOG.md`, `CONTRIBUTING.md`, `ARCHITECTURE.md` | `docs/imported/<slug>.md` |
| everything else under `docs/` or top level   | `docs/imported/<path-context>.md`      |

Backfill is **idempotent** — it stamps a `backfill_source_hash` into
each ingested doc's front-matter and skips unchanged sources on re-run.

Run `pedia backfill --dry-run` to preview the plan, `--report-only` to
classify without writing, or `--url <seed>` to crawl an external docs
site (stdlib-only HTTP, same-origin BFS, depth-bounded, robots.txt
honored).

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
