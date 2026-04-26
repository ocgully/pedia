"""argparse dispatcher -- `pedia <cmd>`.

See README.md for the full CLI reference.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from pedia import __version__
from pedia import backfill as backfill_mod
from pedia import backfill_fs as backfill_fs_mod
from pedia import config as cfg
from pedia import doctypes
from pedia import hooks as hooks_mod
from pedia import index as idx
from pedia import query as qmod
from pedia import refresh as refresh_mod
from pedia import symbols as sym
from pedia import trace as trace_mod
from pedia.parser import parse_document, slugify


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _require_root(args) -> Path:
    r = cfg.find_pedia_root()
    if r is None:
        sys.stderr.write(
            "error: no .pedia/ directory found walking up from CWD. Run `pedia init` first.\n"
        )
        sys.exit(2)
    return r


def _format_opt(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--format", choices=["text", "json"], default="text")


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


CLAUDEIGNORE_BLOCK = """
# pedia:managed -- agents must query via the `pedia` CLI, never read .pedia/ directly
.pedia/
"""


NORTH_STAR_EXAMPLE = """---
type: north-star
universal_context: true
defines: [Agent-First Development]
---
# Agent-First Development

We are building tools so AI agents can operate over code + knowledge +
work deterministically, without vibes-based retrieval. Every session
should cost less context than the last to answer the same question.
"""


CONSTITUTION_EXAMPLE = """---
type: constitution
universal_context: true
defines: [Determinism Over Magic]
---
# Determinism Over Magic

Same inputs, same outputs, forever. Block IDs are content hashes.
Search is BM25 + structured filters. Embeddings are a future optional
layer, never the primary path. If an agent can't reproduce a result by
hand, it's a bug.
"""


SPEC_EXAMPLE = """---
type: spec
id: 001-example
version: 1.0
---
# Example Spec: Flow Network Routing

## Overview

The Flow Network is the executor topology that routes work between
nodes. This spec pins the routing contract for phase 1.

## Flow Network

A Flow Network is a directed acyclic graph of `Node` objects connected
by typed edges. See [[decision:0001-content-hash-block-ids]] for the
stable-id strategy. The network itself is pure data -- execution lives
in a separate `Executor`.

## Acceptance criteria

- Nodes resolve dependencies in topological order.
- Cycles are detected at insert time, not execute time.
- Every edge carries a kind: `cites | supersedes | amends | implements`.
"""


DECISION_EXAMPLE = """---
type: decision
id: 0001-content-hash-block-ids
---
# 0001 -- Content-hash block IDs

## Context

Pedia needs stable, addressable references to regions of markdown
documents. Path+line references break under trivial edits; heading
slugs collide; UUID assignment requires stateful authoring tooling.

## Decision

A block's ID is `sha256(normalize(body))[:16]`. Normalization: strip
trailing whitespace per line, join with `\\n`. The ID is deterministic,
diff-stable across trivial edits, and reproducible from the source
alone -- an agent can compute it without querying the index.

## Consequences

- Two distinct blocks with identical content collide to one ID. This
  is acceptable: the `refs` table still distinguishes them by `src_id`.
- Renaming a heading changes the ID (content changed). Clients that
  want heading-slug-stable references should cite `path#heading` wiki
  links (brackets omitted here to avoid resolution), which the resolver
  maps to whichever block currently carries that slug.
"""


CONFIG_EXAMPLE = """version: 1
token_approx_chars_per_token: 4
query:
  default_limit: 10
  default_token_budget: 2000
  universal_reserve: 500
# External-system deep-link templates (used by `pedia web`).
# The wiki advertises the URL; it does not fetch external content.
external_links:
  # TaskFlow. The legacy `hopewell` key is also honoured by the wiki
  # for backwards compat with existing pedia configs.
  taskflow:
    template: "http://localhost:8765/#/doc/{id}"
    link_when: "block front-matter has taskflow_id (or legacy hopewell_id) OR block cites [[tf:TF-NNNN]] or [[hw:HW-NNNN]]"
  hopewell:
    template: "http://localhost:8765/#/doc/{id}"
    link_when: "block front-matter has hopewell_id OR block cites [[hw:HW-NNNN]]"
  github_issues:
    template: "https://github.com/{repo}/issues/{id}"
  jira:
    template: "https://{instance}.atlassian.net/browse/{id}"
  github_code:
    template: "https://github.com/{repo}/blob/{sha}/{path}#L{line}"
"""


def cmd_init(args) -> int:
    root = Path.cwd().resolve()
    base = cfg.pedia_dir(root)
    base.mkdir(parents=True, exist_ok=True)
    for sub in (
        "north-stars",
        "vision",
        "constitution",
        "specs",
        "prds",
        "technical-requirements",
        "decisions",
        "plans",
        "docs",
    ):
        (base / sub).mkdir(exist_ok=True)

    cfg_path = cfg.config_path(root)
    if not cfg_path.exists():
        cfg_path.write_text(CONFIG_EXAMPLE, encoding="utf-8")

    if args.with_examples:
        (base / "north-stars" / "01-agent-first.md").write_text(
            NORTH_STAR_EXAMPLE, encoding="utf-8"
        )
        (base / "constitution" / "technical.md").write_text(
            CONSTITUTION_EXAMPLE, encoding="utf-8"
        )
        (base / "specs" / "001-example").mkdir(exist_ok=True)
        (base / "specs" / "001-example" / "spec.md").write_text(
            SPEC_EXAMPLE, encoding="utf-8"
        )
        (base / "decisions" / "0001-content-hash-block-ids.md").write_text(
            DECISION_EXAMPLE, encoding="utf-8"
        )

    # .claudeignore append
    claudeignore = root / ".claudeignore"
    add_block = CLAUDEIGNORE_BLOCK.strip() + "\n"
    if claudeignore.exists():
        existing = claudeignore.read_text(encoding="utf-8", errors="replace")
        if "pedia:managed" not in existing:
            claudeignore.write_text(existing.rstrip() + "\n\n" + add_block, encoding="utf-8")
    else:
        claudeignore.write_text(add_block, encoding="utf-8")

    sys.stdout.write(f"Initialized Pedia at {base}\n")
    if args.with_examples:
        sys.stdout.write("Seeded example docs: north-star, constitution, spec, decision\n")

    # Auto-fire backfill when the project has discoverable sources.
    # `--no-backfill` suppresses this; `--backfill` forces it.
    want_backfill = getattr(args, "backfill", None)
    if want_backfill is None:
        # default: fire when sources are discoverable
        want_backfill = backfill_fs_mod.has_discoverable_sources(root)
    if want_backfill:
        sys.stdout.write("Running pedia backfill (auto-discovered sources)...\n")
        report = backfill_mod.run_backfill(root)
        sys.stdout.write(report.to_text())

    sys.stdout.write("Next: run `pedia refresh` to build the index.\n")
    return 0


# ---------------------------------------------------------------------------
# backfill
# ---------------------------------------------------------------------------


def cmd_backfill(args) -> int:
    # Find the project root: prefer an existing .pedia/, else walk up to a .git/
    root = cfg.find_pedia_root()
    if root is None:
        # fall back to nearest .git/
        cur = Path.cwd().resolve()
        while True:
            if (cur / ".git").exists():
                root = cur
                break
            if cur.parent == cur:
                root = Path.cwd().resolve()
                break
            cur = cur.parent
    source_dir = Path(args.source).resolve() if args.source else None
    report = backfill_mod.run_backfill(
        root,
        source_dir=source_dir,
        url=args.url,
        depth=args.depth,
        dry_run=bool(args.dry_run),
        report_only=bool(args.report_only),
    )
    # dry-run / report-only: enumerate items so humans see what would happen
    if args.dry_run or args.report_only:
        for status, dest, reason in report.items[:50]:
            sys.stdout.write(f"  [{status}] {dest}  -- {reason}\n")
        if len(report.items) > 50:
            sys.stdout.write(f"  ... {len(report.items) - 50} more\n")
    sys.stdout.write(report.to_text())
    return 0


# ---------------------------------------------------------------------------
# add
# ---------------------------------------------------------------------------


TYPE_SUBDIR = {
    "spec": "specs",
    "decision": "decisions",
    "north-star": "north-stars",
    "constitution": "constitution",
    "prd": "prds",
    "tr": "technical-requirements",
    "technical-requirement": "technical-requirements",
    "plan": "plans",
    "documentation": "docs",
}


def cmd_add(args) -> int:
    root = _require_root(args)
    src = Path(args.path).resolve()
    if not src.is_file():
        sys.stderr.write(f"error: source file not found: {src}\n")
        return 2
    subdir = TYPE_SUBDIR.get(args.type)
    if subdir is None:
        sys.stderr.write(f"error: unknown type '{args.type}'\n")
        return 2
    dst_dir = cfg.pedia_dir(root) / subdir
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / src.name
    shutil.copy2(src, dst)
    sys.stdout.write(f"Added {dst.relative_to(root)}\n")
    return 0


# ---------------------------------------------------------------------------
# refresh
# ---------------------------------------------------------------------------


def cmd_refresh(args) -> int:
    root = _require_root(args)
    added, unchanged, removed = refresh_mod.refresh(
        root,
        full=bool(args.full),
        docs_glob=args.docs,
        only_changed_in_session=bool(args.only_changed_in_session),
    )
    sys.stdout.write(
        f"refresh: {added} added/updated, {unchanged} unchanged, {removed} removed\n"
    )
    return 0


# ---------------------------------------------------------------------------
# query / get / show
# ---------------------------------------------------------------------------


def cmd_query(args) -> int:
    root = _require_root(args)
    exclude = [s.strip() for s in (args.exclude or "").split(",") if s.strip()]
    result = qmod.run_query(
        root,
        args.search,
        doc_type=args.type,
        scope=args.scope,
        token_budget=args.token_budget,
        exclude=exclude,
        limit=args.limit,
    )
    if args.format == "json":
        sys.stdout.write(result.to_json() + "\n")
    else:
        sys.stdout.write(qmod.format_text(result))
    return 0


def cmd_get(args) -> int:
    root = _require_root(args)
    d = qmod.get_single(root, args.block_id)
    if d is None:
        sys.stderr.write(f"error: no block with id prefix '{args.block_id}'\n")
        return 1
    if args.format == "json":
        sys.stdout.write(json.dumps(d, indent=2, ensure_ascii=False) + "\n")
    else:
        sys.stdout.write(
            f'[block:{d["id"]}] {d["doc_path"]} @ "{d["heading_title"] or d["heading_slug"] or "(body)"}"\n'
        )
        sys.stdout.write(d["content"] + "\n")
    return 0


def cmd_show(args) -> int:
    root = _require_root(args)
    result = qmod.show_for(root, args.for_target, token_budget=args.token_budget)
    if args.format == "json":
        sys.stdout.write(result.to_json() + "\n")
    else:
        sys.stdout.write(qmod.format_text(result))
    return 0


# ---------------------------------------------------------------------------
# trace
# ---------------------------------------------------------------------------


def cmd_trace(args) -> int:
    root = _require_root(args)
    direction = "up" if args.up else "down" if args.down else None
    if direction is None:
        sys.stderr.write("error: specify --up or --down\n")
        return 2
    results = trace_mod.walk(root, args.block_id, direction, depth=args.depth)
    if args.format == "json":
        sys.stdout.write(json.dumps(results, indent=2, ensure_ascii=False) + "\n")
    else:
        sys.stdout.write(trace_mod.format_trace(results, direction))
    return 0


# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------


def cmd_check(args) -> int:
    root = _require_root(args)
    conn = idx.connect(cfg.db_path(root))
    findings: List[tuple] = []
    try:
        # schema findings, doc-by-doc
        doc_paths = sorted({r["doc_path"] for r in conn.execute("SELECT doc_path FROM blocks")})
        for dp in doc_paths:
            full = cfg.pedia_dir(root) / dp
            if not full.is_file():
                continue
            fm, blocks = parse_document(full, dp)
            doc_type = str(fm.get("type") or _default_type_for(dp))
            validator = doctypes.validator_for(doc_type)
            findings.extend(validator(dp, fm, blocks))

        # unresolved / ambiguous wiki-links
        for r in sym.find_unresolved_links(conn):
            src_row = idx.get_block(conn, r["src_id"])
            src_path = src_row["doc_path"] if src_row else "(unknown)"
            findings.append(
                ("warning", f"{src_path}: unresolved wiki link [[{r['raw']}]] (form={r['form']})")
            )
        for term, ids in sym.find_ambiguous_terms(conn):
            findings.append(
                (
                    "error",
                    f"ambiguous term '{term}' defined in {len(ids)} blocks: {', '.join(ids)}",
                )
            )
    finally:
        conn.close()

    if not findings:
        sys.stdout.write("pedia check: OK -- no issues\n")
        return 0
    errs = sum(1 for sev, _ in findings if sev == "error")
    for sev, msg in findings:
        sys.stdout.write(f"[{sev}] {msg}\n")
    sys.stdout.write(f"\n{len(findings)} finding(s); {errs} error(s)\n")
    return 1 if errs else 0


def _default_type_for(rel_path: str) -> str:
    head = rel_path.split("/", 1)[0]
    return {
        "specs": "spec",
        "decisions": "decision",
        "north-stars": "north-star",
        "constitution": "constitution",
        "prds": "prd",
        "technical-requirements": "technical-requirement",
        "plans": "plan",
        "docs": "documentation",
        "vision": "vision",
    }.get(head, "documentation")


# ---------------------------------------------------------------------------
# hooks
# ---------------------------------------------------------------------------


def cmd_hooks_install(args) -> int:
    root = cfg.find_pedia_root() or Path.cwd().resolve()
    did_anything = False
    if args.git:
        if (root / ".git").exists():
            path = hooks_mod.install_git_hook(root)
            sys.stdout.write(f"Installed git post-commit hook -> {path}\n")
            did_anything = True
        else:
            sys.stdout.write(f"(skipping git hook: no .git dir at {root})\n")
    if args.claude_code:
        if args.settings_path:
            settings_path = Path(args.settings_path).resolve()
        else:
            settings_path = hooks_mod.default_settings_path(args.scope or "user").resolve()
        hooks_mod.install_claude_code(settings_path, dry_run=bool(args.dry_run))
        if args.dry_run:
            sys.stdout.write(f"(dry-run) would write {settings_path}\n")
        else:
            sys.stdout.write(f"Installed Claude Code hooks -> {settings_path}\n")
        did_anything = True
    if not did_anything:
        sys.stdout.write(
            "Nothing to do. Pass --git and/or --claude-code. See `pedia hooks install --help`.\n"
        )
        return 2
    return 0


def cmd_hooks_uninstall(args) -> int:
    root = cfg.find_pedia_root() or Path.cwd().resolve()
    any_removed = False
    if args.git:
        if hooks_mod.uninstall_git_hook(root):
            sys.stdout.write("Removed pedia git post-commit hook\n")
            any_removed = True
        else:
            sys.stdout.write("No pedia-managed git post-commit hook to remove\n")
    if args.claude_code:
        if args.settings_path:
            settings_path = Path(args.settings_path).resolve()
        else:
            settings_path = hooks_mod.default_settings_path(args.scope or "user").resolve()
        if hooks_mod.uninstall_claude_code(settings_path):
            sys.stdout.write(f"Removed Pedia hooks from {settings_path}\n")
            any_removed = True
        else:
            sys.stdout.write(f"No Pedia hooks found in {settings_path}\n")
    if not any_removed and not (args.git or args.claude_code):
        sys.stdout.write("Pass --git and/or --claude-code.\n")
        return 2
    return 0


# ---------------------------------------------------------------------------
# block-id resolver
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# web (read-only wiki view -- HW-0046)
# ---------------------------------------------------------------------------


def cmd_web(args) -> int:
    root = _require_root(args)
    # lazy import so `pedia --version` etc. don't pull http.server
    from pedia.web import server as web_server

    return web_server.run(
        root,
        port=int(args.port),
        open_browser=bool(args.open),
        host=args.host,
    )


def cmd_block_id(args) -> int:
    root = _require_root(args)
    spec = args.target
    path, _, heading = spec.partition(":")
    conn = idx.connect(cfg.db_path(root))
    try:
        if heading:
            r = conn.execute(
                "SELECT id FROM blocks WHERE doc_path = ? AND heading_slug = ?",
                (path, slugify(heading)),
            ).fetchone()
        else:
            r = conn.execute(
                "SELECT id FROM blocks WHERE doc_path = ? ORDER BY line_start LIMIT 1",
                (path,),
            ).fetchone()
        if r is None:
            sys.stderr.write(f"error: no block matches {spec}\n")
            return 1
        sys.stdout.write(r["id"] + "\n")
        return 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pedia",
        description="pedia -- deterministic knowledge + specs + context for LLM agents.",
    )
    p.add_argument("--version", action="version", version=f"pedia {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    # init
    sp = sub.add_parser("init", help="Initialize .pedia/ in the current directory")
    sp.add_argument("--with-examples", action="store_true")
    bf_group = sp.add_mutually_exclusive_group()
    bf_group.add_argument(
        "--backfill", dest="backfill", action="store_true",
        default=None,
        help="Force-run backfill after init (default: auto-run if sources discoverable)",
    )
    bf_group.add_argument(
        "--no-backfill", dest="backfill", action="store_false",
        help="Skip the auto-backfill step",
    )
    sp.set_defaults(func=cmd_init)

    # backfill
    sp = sub.add_parser(
        "backfill",
        help="Spider existing docs (filesystem and/or website) into .pedia/",
    )
    sp.add_argument("--source", default=None, help="Directory to spider (default: CWD / repo root)")
    sp.add_argument("--url", default=None, help="Website seed URL to crawl")
    sp.add_argument("--depth", type=int, default=3, help="Crawl depth (web mode, default 3)")
    sp.add_argument("--dry-run", action="store_true", help="Show what would be written; don't touch disk")
    sp.add_argument("--report-only", action="store_true", help="Classify + list, don't write anything")
    sp.set_defaults(func=cmd_backfill)

    # add
    sp = sub.add_parser("add", help="Import a markdown file as a given doc type")
    sp.add_argument(
        "--type",
        required=True,
        choices=list(TYPE_SUBDIR.keys()),
    )
    sp.add_argument("--path", required=True)
    sp.set_defaults(func=cmd_add)

    # query
    sp = sub.add_parser("query", help="Search the block index")
    sp.add_argument("search", help="Free-text search string")
    sp.add_argument("--type")
    sp.add_argument("--scope", choices=["universal"])
    sp.add_argument("--token-budget", type=int, default=2000)
    sp.add_argument("--exclude", default="")
    sp.add_argument("--limit", type=int, default=10)
    _format_opt(sp)
    sp.set_defaults(func=cmd_query)

    # get
    sp = sub.add_parser("get", help="Fetch one block by id (or id-prefix)")
    sp.add_argument("block_id")
    _format_opt(sp)
    sp.set_defaults(func=cmd_get)

    # show
    sp = sub.add_parser("show", help="Show context for a target (HW-id or doc path)")
    sp.add_argument("--for", dest="for_target", required=True)
    sp.add_argument("--token-budget", type=int, default=2000)
    _format_opt(sp)
    sp.set_defaults(func=cmd_show)

    # trace
    sp = sub.add_parser("trace", help="Walk provenance / dependency graph")
    sp.add_argument("block_id")
    g = sp.add_mutually_exclusive_group(required=True)
    g.add_argument("--up", action="store_true")
    g.add_argument("--down", action="store_true")
    sp.add_argument("--depth", type=int, default=5)
    _format_opt(sp)
    sp.set_defaults(func=cmd_trace)

    # refresh
    sp = sub.add_parser("refresh", help="Reindex (incremental by default)")
    sp.add_argument("--full", action="store_true")
    sp.add_argument("--docs", default=None, help="Optional glob (relative to .pedia/)")
    sp.add_argument(
        "--only-changed-in-session",
        action="store_true",
        help="Internal: scope to files git considers dirty under .pedia/",
    )
    sp.set_defaults(func=cmd_refresh)

    # check
    sp = sub.add_parser("check", help="Validate doc schemas + wiki-link references")
    sp.set_defaults(func=cmd_check)

    # hooks
    sp = sub.add_parser("hooks", help="Install / uninstall integration hooks")
    hsub = sp.add_subparsers(dest="hook_cmd", required=True)

    ih = hsub.add_parser("install")
    ih.add_argument("--git", action="store_true")
    ih.add_argument("--claude-code", action="store_true")
    ih.add_argument("--settings-path", default=None)
    ih.add_argument("--scope", choices=["user", "project"], default="user")
    ih.add_argument("--dry-run", action="store_true")
    ih.set_defaults(func=cmd_hooks_install)

    uh = hsub.add_parser("uninstall")
    uh.add_argument("--git", action="store_true")
    uh.add_argument("--claude-code", action="store_true")
    uh.add_argument("--settings-path", default=None)
    uh.add_argument("--scope", choices=["user", "project"], default="user")
    uh.set_defaults(func=cmd_hooks_uninstall)

    # block-id
    sp = sub.add_parser("block-id", help="Resolve a block id from `path` or `path:heading`")
    sp.add_argument("target")
    sp.set_defaults(func=cmd_block_id)

    # web -- read-only wiki view (HW-0046)
    sp = sub.add_parser("web", help="Start a local read-only web UI for humans")
    sp.add_argument("--port", type=int, default=8766)
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument(
        "--open", action="store_true", help="Open the UI in a browser once the server starts"
    )
    sp.set_defaults(func=cmd_web)

    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except SystemExit:
        raise
    except Exception as e:
        sys.stderr.write(f"pedia: error: {e}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
