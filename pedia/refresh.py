"""Incremental reindex driver.

Walks `.pedia/**/*.md`, stat-checks mtime + content hash against the
`doc_index` table, and re-parses only docs that changed. Full refresh
(`--full`) drops everything and rebuilds.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sqlite3
from pathlib import Path
from typing import Iterable, List, Optional, Set, Tuple

from pedia import config as cfg
from pedia import index as idx
from pedia import symbols as sym
from pedia.parser import parse_document


# Map .pedia subdir -> default doc type
SUBDIR_TYPE = {
    "specs": "spec",
    "decisions": "decision",
    "north-stars": "north-star",
    "constitution": "constitution",
    "prds": "prd",
    "technical-requirements": "technical-requirement",
    "plans": "plan",
    "docs": "documentation",
    "vision": "vision",
}


def _file_hash(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _rel(root: Path, p: Path) -> str:
    return p.relative_to(cfg.pedia_dir(root)).as_posix()


def _doc_type_for(rel_path: str) -> str:
    head = rel_path.split("/", 1)[0]
    return SUBDIR_TYPE.get(head, "documentation")


def iter_markdown_files(root: Path) -> Iterable[Path]:
    base = cfg.pedia_dir(root)
    if not base.is_dir():
        return []
    out: List[Path] = []
    for p in base.rglob("*.md"):
        if p.is_file():
            out.append(p)
    return out


def refresh(
    root: Path,
    *,
    full: bool = False,
    docs_glob: Optional[str] = None,
    only_changed_in_session: bool = False,
) -> Tuple[int, int, int]:
    """Refresh the index. Returns (added_or_updated, unchanged, removed)."""
    conn = idx.connect(cfg.db_path(root))
    try:
        if full:
            idx.drop_all(conn)
        idx.init_schema(conn)
        project_cfg = cfg.load_project_config(root)
        chars_per_token = int(project_cfg.get("token_approx_chars_per_token", 4))

        pedia_base = cfg.pedia_dir(root)

        # Determine file set
        if only_changed_in_session:
            changed = _git_diff_pedia_paths(root)
            files = [pedia_base / c for c in changed if (pedia_base / c).is_file()]
        else:
            files = list(iter_markdown_files(root))
        if docs_glob:
            glob_matches = {p.resolve() for p in pedia_base.glob(docs_glob)}
            files = [f for f in files if f.resolve() in glob_matches]

        seen: Set[str] = set()
        added = 0
        unchanged = 0

        # Two-pass so `defines:` in any doc is registered before we
        # try to resolve `[[Term]]` wiki links.
        pending_docs: List[Tuple[str, Path, str]] = []
        for p in files:
            rel = _rel(root, p)
            seen.add(rel)
            mtime_ns = p.stat().st_mtime_ns
            chash = _file_hash(p)
            state = idx.get_doc_state(conn, rel)
            if (
                state is not None
                and state[0] == mtime_ns
                and state[1] == chash
                and not full
            ):
                unchanged += 1
                continue
            pending_docs.append((rel, p, chash))

        # Phase 1: parse + write blocks + definitions
        parsed: List[Tuple[str, Path, str, list]] = []
        for rel, p, chash in pending_docs:
            _, blocks = parse_document(
                p, rel, default_doc_type=_doc_type_for(rel),
                chars_per_token=chars_per_token,
            )
            idx.replace_document_blocks(conn, rel, blocks)
            sym.register_definitions(conn, blocks)
            idx.record_doc_state(conn, rel, p.stat().st_mtime_ns, chash)
            parsed.append((rel, p, chash, blocks))
            added += 1
        conn.commit()

        # Phase 2: re-scan wiki links for ALL blocks (full mode) or for
        # just the touched docs (incremental / session mode). Full mode
        # rescans every block so that unresolved `[[Term]]` references
        # in previously-indexed docs can now resolve to newly-added
        # definitions.
        if full:
            for row in idx.all_blocks(conn):
                sym.register_wiki_links(conn, [_RowBlock(row)])
        else:
            for rel, p, chash, blocks in parsed:
                sym.register_wiki_links(conn, blocks)

        # Removed docs: anything in doc_index not in `seen` (only when
        # we're doing a full walk; targeted/session runs don't prune).
        removed = 0
        if not only_changed_in_session and not docs_glob:
            for existing_path in idx.all_docs(conn):
                if existing_path not in seen:
                    idx.delete_document(conn, existing_path)
                    removed += 1

        conn.commit()
        return added, unchanged, removed
    finally:
        conn.close()


class _RowBlock:
    """Adapter that makes a sqlite3.Row quack like a parser.Block for the
    wiki-link registration path."""

    def __init__(self, row: sqlite3.Row):
        self.id = row["id"]
        self.content = row["content"] or ""
        import json as _json
        try:
            self.meta = _json.loads(row["meta_json"]) if row["meta_json"] else {}
        except Exception:
            self.meta = {}


def _blocks_from_parsed(parsed: list) -> list:
    out = []
    for _rel, _p, _h, blocks in parsed:
        for b in blocks:
            out.append(_BlockAdapter(b))
    return out


class _BlockAdapter:
    def __init__(self, b):
        self.id = b.id
        self.content = b.content
        self.meta = b.meta


def _git_diff_pedia_paths(root: Path) -> List[str]:
    """Return relative paths under .pedia/ that git considers dirty."""
    try:
        proc = subprocess.run(
            ["git", "diff", "--name-only", "HEAD", "--", ".pedia/"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception:
        return []
    if proc.returncode != 0:
        return []
    out: List[str] = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if line.startswith(".pedia/"):
            out.append(line[len(".pedia/"):])
    # Also include untracked files in .pedia/
    try:
        proc2 = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard", ".pedia/"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=10,
        )
        if proc2.returncode == 0:
            for line in proc2.stdout.splitlines():
                line = line.strip()
                if line.startswith(".pedia/"):
                    out.append(line[len(".pedia/"):])
    except Exception:
        pass
    return out
