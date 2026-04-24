"""Wiki-link + symbol resolution.

Four forms (plan  15.1):
  [[Term]]           -> symbols table (term -> canonical_id)
  [[type:slug]]      -> .pedia/<type-plural>/<slug>.md
  [[path#heading]]   -> resolve `path` (relative to .pedia/) + heading slug
  [[block:<id>]]     -> direct content-hash lookup

`defines:` in a doc's front-matter declares canonical targets. The first
block in a doc that declares `defines: [X]` is the canonical owner of
`[[X]]`. `pedia check` flags later re-declarations as ambiguous.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from pedia.parser import (
    Block,
    WIKI_LINK_RE,
    detect_wiki_link_form,
    extract_wiki_links,
    slugify,
)


# doc_type -> .pedia subdirectory convention
TYPE_DIR = {
    "spec": "specs",
    "decision": "decisions",
    "north-star": "north-stars",
    "constitution": "constitution",
    "prd": "prds",
    "technical-requirement": "technical-requirements",
    "tr": "technical-requirements",
    "plan": "plans",
    "documentation": "docs",
    "vision": "vision",
}


def register_definitions(conn: sqlite3.Connection, blocks: List[Block]) -> None:
    """Populate the `symbols` table from `defines:` front-matter."""
    for b in blocks:
        defines = b.meta.get("defines") if b.meta else None
        if not defines:
            continue
        if isinstance(defines, str):
            defines = [defines]
        for term in defines:
            t = str(term).strip()
            if not t:
                continue
            conn.execute(
                "INSERT OR IGNORE INTO symbols(term, canonical_id) VALUES (?, ?)",
                (t, b.id),
            )


def register_wiki_links(conn: sqlite3.Connection, blocks: List[Block]) -> None:
    """Extract + resolve [[...]] links for every block."""
    for b in blocks:
        links = extract_wiki_links(b.content)
        for raw, target in links:
            form = detect_wiki_link_form(target)
            dst = resolve_wiki_link(conn, target, form)
            conn.execute(
                "INSERT OR REPLACE INTO wiki_links(src_id, dst_id, raw, form) VALUES (?,?,?,?)",
                (b.id, dst, raw, form),
            )
            if dst is not None:
                # Also mirror into refs as a 'cites' edge (soft-linked).
                conn.execute(
                    "INSERT OR IGNORE INTO refs(src_id, dst_id, kind) VALUES (?,?,?)",
                    (b.id, dst, "cites"),
                )


def resolve_wiki_link(
    conn: sqlite3.Connection, target: str, form: str
) -> Optional[str]:
    t = target.strip()
    if form == "block-id":
        bid = t.split(":", 1)[1].strip()
        r = conn.execute("SELECT id FROM blocks WHERE id = ?", (bid,)).fetchone()
        return r["id"] if r else None
    if form == "term":
        rows = list(
            conn.execute(
                "SELECT canonical_id FROM symbols WHERE term = ? COLLATE NOCASE",
                (t,),
            )
        )
        if len(rows) == 1:
            return rows[0]["canonical_id"]
        return None  # 0 -> unresolved; >1 -> ambiguous (pedia check surfaces)
    if form == "type-slug":
        t_part, _, slug = t.partition(":")
        t_part = t_part.strip().lower()
        slug = slug.strip()
        subdir = TYPE_DIR.get(t_part)
        if subdir is None:
            return None
        # Spec convention: .pedia/specs/<slug>/spec.md
        # Others: .pedia/<subdir>/<slug>.md
        candidate_paths: List[str] = []
        if t_part == "spec":
            candidate_paths.append(f"specs/{slug}/spec.md")
        else:
            candidate_paths.append(f"{subdir}/{slug}.md")
        for cp in candidate_paths:
            row = conn.execute(
                "SELECT id FROM blocks WHERE doc_path = ? ORDER BY line_start LIMIT 1",
                (cp,),
            ).fetchone()
            if row:
                return row["id"]
        return None
    if form == "path-heading":
        path_part, _, heading = t.partition("#")
        path_part = path_part.strip()
        heading = heading.strip()
        heading_slug = slugify(heading)
        row = conn.execute(
            "SELECT id FROM blocks WHERE doc_path = ? AND heading_slug = ?",
            (path_part, heading_slug),
        ).fetchone()
        if row:
            return row["id"]
        return None
    return None


def find_unresolved_links(conn: sqlite3.Connection) -> List[sqlite3.Row]:
    return list(
        conn.execute(
            "SELECT src_id, raw, form FROM wiki_links WHERE dst_id IS NULL"
        )
    )


def find_ambiguous_terms(conn: sqlite3.Connection) -> List[Tuple[str, List[str]]]:
    rows = list(
        conn.execute(
            "SELECT term, canonical_id FROM symbols ORDER BY term"
        )
    )
    grouped: Dict[str, List[str]] = {}
    for r in rows:
        grouped.setdefault(r["term"], []).append(r["canonical_id"])
    return [(t, ids) for t, ids in grouped.items() if len(ids) > 1]
