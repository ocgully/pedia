"""BM25 + structured-filter search, LLM-calibrated response shape.

Every query response carries three sections:
  * universal context (always prepended)
  * matches (ranked, token-budgeted)
  * see also (what the matches cite / are cited by)

Text mode is the default; `--format json` returns the same shape as a
structured object.
"""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from pedia import config as cfg
from pedia import index as idx


@dataclass
class QueryResult:
    query: str
    token_budget: int
    tokens_used: int
    universal: List[Dict[str, Any]] = field(default_factory=list)
    matches: List[Dict[str, Any]] = field(default_factory=list)
    see_also: List[Dict[str, Any]] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(
            {
                "query": self.query,
                "token_budget": self.token_budget,
                "tokens_used": self.tokens_used,
                "universal": self.universal,
                "matches": self.matches,
                "see_also": self.see_also,
            },
            indent=2,
            ensure_ascii=False,
        )


def _row_to_dict(row: sqlite3.Row, score: Optional[float] = None) -> Dict[str, Any]:
    meta: Dict[str, Any] = {}
    try:
        meta = json.loads(row["meta_json"]) if row["meta_json"] else {}
    except Exception:
        meta = {}
    heading_title = meta.get("heading_title") or row["heading_slug"] or ""
    return {
        "id": row["id"],
        "doc_path": row["doc_path"],
        "doc_type": row["doc_type"],
        "heading_slug": row["heading_slug"],
        "heading_title": heading_title,
        "line_start": row["line_start"],
        "line_end": row["line_end"],
        "universal": bool(row["universal"]),
        "token_estimate": row["token_estimate"],
        "score": score,
        "content": row["content"],
    }


def _fts_escape(term: str) -> str:
    # Keep spaces (AND semantics); quote tokens that contain non-word chars.
    parts: List[str] = []
    for raw in re.findall(r"[A-Za-z0-9_]+", term):
        if raw:
            parts.append(raw)
    if not parts:
        return '""'
    return " ".join(parts)


def _match_clause(search: str) -> str:
    return _fts_escape(search)


def run_query(
    root: Path,
    search: str,
    *,
    doc_type: Optional[str] = None,
    scope: Optional[str] = None,
    token_budget: int = 2000,
    exclude: Optional[Sequence[str]] = None,
    limit: int = 10,
) -> QueryResult:
    conn = idx.connect(cfg.db_path(root))
    try:
        exclude_set = set(exclude or [])
        result = QueryResult(query=search, token_budget=token_budget, tokens_used=0)

        # 1. universal context -- always prepended
        universal_reserve_tokens = min(token_budget // 2, 500)
        universal_rows = idx.get_universal_blocks(conn)
        used = 0
        for r in universal_rows:
            if r["id"] in exclude_set:
                continue
            est = int(r["token_estimate"] or 0)
            if used + est > universal_reserve_tokens and result.universal:
                break
            d = _row_to_dict(r)
            result.universal.append(d)
            used += est
        result.tokens_used += used

        # 2. matches (FTS5 + filters)
        if scope == "universal":
            # universal-only retrieval: we've already filled that section;
            # matches are the universal blocks too (for convenience).
            matches: List[Tuple[sqlite3.Row, float]] = [
                (r, 1.0) for r in universal_rows if r["id"] not in exclude_set
            ]
        else:
            matches = _fts_search(
                conn, search, doc_type=doc_type, exclude=exclude_set, limit=limit
            )

        remaining = max(0, token_budget - result.tokens_used)
        for row, score in matches:
            est = int(row["token_estimate"] or 0)
            if est > remaining and result.matches:
                continue  # skip too-big-to-fit items, keep going to fill
            d = _row_to_dict(row, score=score)
            result.matches.append(d)
            remaining -= est
            result.tokens_used += est
            if remaining <= 0:
                break

        # 3. see-also: follow refs (down and up) from the top matches
        seen: Set[str] = {d["id"] for d in result.matches} | exclude_set
        for match in result.matches[:5]:
            related = _related_rows(conn, match["id"])
            for r, reason in related:
                if r["id"] in seen:
                    continue
                d = _row_to_dict(r)
                d["relation"] = reason
                result.see_also.append(d)
                seen.add(r["id"])
                if len(result.see_also) >= 10:
                    break
            if len(result.see_also) >= 10:
                break

        return result
    finally:
        conn.close()


def _fts_search(
    conn: sqlite3.Connection,
    search: str,
    *,
    doc_type: Optional[str],
    exclude: Set[str],
    limit: int,
) -> List[Tuple[sqlite3.Row, float]]:
    match_expr = _match_clause(search)
    if match_expr == '""':
        return []
    sql = """
    SELECT b.*, bm25(blocks_fts) AS score
    FROM blocks_fts
    JOIN blocks b ON b.rowid = blocks_fts.rowid
    WHERE blocks_fts MATCH ?
    """
    args: List[Any] = [match_expr]
    if doc_type:
        sql += " AND b.doc_type = ?"
        args.append(doc_type)
    sql += " ORDER BY score ASC LIMIT ?"
    args.append(limit * 3)  # over-fetch; prune excluded
    out: List[Tuple[sqlite3.Row, float]] = []
    for row in conn.execute(sql, args):
        if row["id"] in exclude:
            continue
        # bm25 returns "smaller is better"; convert to a 0-1 display.
        score_raw = row["score"] or 0.0
        disp = 1.0 / (1.0 + abs(score_raw))
        out.append((row, round(disp, 4)))
        if len(out) >= limit:
            break
    return out


def _related_rows(
    conn: sqlite3.Connection, block_id: str
) -> List[Tuple[sqlite3.Row, str]]:
    out: List[Tuple[sqlite3.Row, str]] = []
    # refs where this block is the source (what THIS cites)
    for r in conn.execute(
        """SELECT b.*, 'cites' AS rel FROM refs
           JOIN blocks b ON b.id = refs.dst_id
           WHERE refs.src_id = ?""",
        (block_id,),
    ):
        out.append((r, f"{block_id} cites"))
    # refs where this block is the target (what cites THIS)
    for r in conn.execute(
        """SELECT b.*, 'cited_by' AS rel FROM refs
           JOIN blocks b ON b.id = refs.src_id
           WHERE refs.dst_id = ?""",
        (block_id,),
    ):
        out.append((r, f"cited by {block_id}"))
    return out


def format_text(result: QueryResult) -> str:
    out: List[str] = []
    out.append("=== universal context ===")
    if not result.universal:
        out.append("(no universal-context blocks)")
    for u in result.universal:
        out.append(
            f'[block:{u["id"]}] {u["doc_path"]} @ "{u["heading_title"] or u["heading_slug"] or "(body)"}"'
        )
        out.append(u["content"])
        out.append("")

    out.append(
        f"=== matches ({len(result.matches)}, budget used {result.tokens_used}/{result.token_budget} tokens) ==="
    )
    if not result.matches:
        out.append("(no matches)")
    for m in result.matches:
        score = m.get("score")
        score_str = f" (score {score})" if score is not None else ""
        out.append(
            f'[block:{m["id"]}] {m["doc_path"]} @ "{m["heading_title"] or m["heading_slug"] or "(body)"}"{score_str}'
        )
        out.append(m["content"])
        out.append("")

    out.append("=== see also ===")
    if not result.see_also:
        out.append("(none)")
    for s in result.see_also:
        out.append(
            f'[block:{s["id"]}] ({s.get("relation","related")}) {s["doc_path"]} @ "{s["heading_title"] or s["heading_slug"] or "(body)"}"'
        )
    return "\n".join(out) + "\n"


def show_for(
    root: Path,
    target: str,
    *,
    token_budget: int = 2000,
) -> QueryResult:
    """`pedia show --for <target>` -- returns universal context plus
    blocks directly relevant to `target` (an HW-NNNN id or a path under
    .pedia/)."""
    conn = idx.connect(cfg.db_path(root))
    try:
        result = QueryResult(query=f"show --for {target}", token_budget=token_budget, tokens_used=0)

        # universal
        used = 0
        for r in idx.get_universal_blocks(conn):
            d = _row_to_dict(r)
            result.universal.append(d)
            used += int(r["token_estimate"] or 0)
        result.tokens_used += used

        # Heuristic: HW-NNNN -> search for mentions; path -> blocks in that doc + linked.
        matches_rows: List[sqlite3.Row] = []
        if re.match(r"^HW-\d+$", target):
            for r in conn.execute(
                "SELECT * FROM blocks WHERE content LIKE ? ORDER BY doc_path",
                (f"%{target}%",),
            ):
                matches_rows.append(r)
        else:
            # treat as a path (strip `.pedia/` prefix if present)
            path = target
            if path.startswith(".pedia/"):
                path = path[len(".pedia/"):]
            matches_rows = list(
                conn.execute(
                    "SELECT * FROM blocks WHERE doc_path = ? ORDER BY line_start",
                    (path,),
                )
            )

        remaining = max(0, token_budget - result.tokens_used)
        for row in matches_rows:
            est = int(row["token_estimate"] or 0)
            d = _row_to_dict(row)
            result.matches.append(d)
            remaining -= est
            result.tokens_used += est
            if remaining <= 0:
                break

        # see-also via refs
        seen: Set[str] = {m["id"] for m in result.matches}
        for m in result.matches[:5]:
            for r, reason in _related_rows(conn, m["id"]):
                if r["id"] in seen:
                    continue
                d = _row_to_dict(r)
                d["relation"] = reason
                result.see_also.append(d)
                seen.add(r["id"])
                if len(result.see_also) >= 10:
                    break
            if len(result.see_also) >= 10:
                break

        return result
    finally:
        conn.close()


def get_single(root: Path, block_id: str) -> Optional[Dict[str, Any]]:
    conn = idx.connect(cfg.db_path(root))
    try:
        row = idx.get_block(conn, block_id)
        if row is None:
            # try prefix match
            rows = list(
                conn.execute(
                    "SELECT * FROM blocks WHERE id LIKE ? LIMIT 2",
                    (block_id + "%",),
                )
            )
            if len(rows) == 1:
                row = rows[0]
            else:
                return None
        return _row_to_dict(row)
    finally:
        conn.close()
