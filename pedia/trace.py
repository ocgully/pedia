"""Provenance / dependency graph walks.

`pedia trace <block-id> --up`    -> blocks this block cites (transitively)
`pedia trace <block-id> --down`  -> blocks that cite this block (transitively)

The graph combines `refs` (explicit reference edges -- cites / supersedes
/ amends / implements) with `wiki_links` where `dst_id IS NOT NULL`.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from pedia import config as cfg
from pedia import index as idx


def _neighbors_up(conn: sqlite3.Connection, block_id: str) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    for r in conn.execute(
        "SELECT dst_id, kind FROM refs WHERE src_id = ?",
        (block_id,),
    ):
        if r["dst_id"]:
            out.append((r["dst_id"], r["kind"]))
    return out


def _neighbors_down(conn: sqlite3.Connection, block_id: str) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    for r in conn.execute(
        "SELECT src_id, kind FROM refs WHERE dst_id = ?",
        (block_id,),
    ):
        out.append((r["src_id"], r["kind"]))
    return out


def walk(
    root: Path,
    block_id: str,
    direction: str,
    *,
    depth: int = 5,
) -> List[Dict[str, Any]]:
    """Breadth-first walk. Returns a list of dicts:
        { id, depth, via, doc_path, heading_title }
    """
    conn = idx.connect(cfg.db_path(root))
    try:
        if direction not in ("up", "down"):
            raise ValueError("direction must be 'up' or 'down'")
        visited: Set[str] = {block_id}
        frontier: List[Tuple[str, int, str]] = [(block_id, 0, "self")]
        results: List[Dict[str, Any]] = []
        while frontier:
            cur, d, via = frontier.pop(0)
            row = idx.get_block(conn, cur)
            if row is None:
                continue
            results.append(
                {
                    "id": cur,
                    "depth": d,
                    "via": via,
                    "doc_path": row["doc_path"],
                    "heading_slug": row["heading_slug"],
                }
            )
            if d >= depth:
                continue
            neighbors = (
                _neighbors_up(conn, cur) if direction == "up" else _neighbors_down(conn, cur)
            )
            for nid, kind in neighbors:
                if nid in visited:
                    continue
                visited.add(nid)
                frontier.append((nid, d + 1, kind))
        return results
    finally:
        conn.close()


def format_trace(results: List[Dict[str, Any]], direction: str) -> str:
    lines: List[str] = [f"=== trace --{direction} ==="]
    for r in results:
        indent = "  " * r["depth"]
        head = r.get("heading_slug") or "(body)"
        via = r.get("via")
        via_s = "" if via == "self" else f" [via {via}]"
        lines.append(f'{indent}[block:{r["id"]}] {r["doc_path"]} @ "{head}"{via_s}')
    return "\n".join(lines) + "\n"
