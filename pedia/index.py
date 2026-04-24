"""SQLite schema + index CRUD.

Stdlib sqlite3 + FTS5. The database file lives at
`<root>/.pedia/index.sqlite`. Everything in here is deterministic and
rebuildable -- `pedia refresh --full` drops and recreates tables.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from pedia.parser import Block


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS blocks (
  id              TEXT PRIMARY KEY,
  doc_path        TEXT NOT NULL,
  doc_type        TEXT NOT NULL,
  heading_slug    TEXT,
  heading_level   INTEGER,
  line_start      INTEGER,
  line_end        INTEGER,
  content         TEXT,
  universal       INTEGER DEFAULT 0,
  token_estimate  INTEGER,
  meta_json       TEXT,
  indexed_at      TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS blocks_fts USING fts5(
  content,
  heading_slug,
  doc_type UNINDEXED,
  meta_json UNINDEXED,
  tokenize='porter unicode61'
);

CREATE TABLE IF NOT EXISTS refs (
  src_id TEXT NOT NULL,
  dst_id TEXT NOT NULL,
  kind   TEXT NOT NULL,
  PRIMARY KEY (src_id, dst_id, kind)
);

CREATE TABLE IF NOT EXISTS symbols (
  term          TEXT NOT NULL,
  canonical_id  TEXT NOT NULL,
  PRIMARY KEY (term, canonical_id)
);

CREATE TABLE IF NOT EXISTS wiki_links (
  src_id  TEXT NOT NULL,
  dst_id  TEXT,
  raw     TEXT NOT NULL,
  form    TEXT NOT NULL,
  PRIMARY KEY (src_id, raw)
);

CREATE TABLE IF NOT EXISTS doc_index (
  doc_path   TEXT PRIMARY KEY,
  mtime_ns   INTEGER,
  content_hash TEXT,
  indexed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_blocks_doc  ON blocks(doc_path);
CREATE INDEX IF NOT EXISTS idx_blocks_univ ON blocks(universal);
CREATE INDEX IF NOT EXISTS idx_refs_dst    ON refs(dst_id);
CREATE INDEX IF NOT EXISTS idx_wiki_dst    ON wiki_links(dst_id);
CREATE INDEX IF NOT EXISTS idx_wiki_src    ON wiki_links(src_id);
CREATE INDEX IF NOT EXISTS idx_symbols_term ON symbols(term);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    conn.commit()


def drop_all(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        DROP TABLE IF EXISTS blocks_fts;
        DROP TABLE IF EXISTS wiki_links;
        DROP TABLE IF EXISTS symbols;
        DROP TABLE IF EXISTS refs;
        DROP TABLE IF EXISTS blocks;
        DROP TABLE IF EXISTS doc_index;
        """
    )
    conn.commit()


def replace_document_blocks(
    conn: sqlite3.Connection,
    doc_path: str,
    blocks: Sequence[Block],
) -> None:
    now = _now_iso()
    existing_rows = list(
        conn.execute("SELECT rowid, id FROM blocks WHERE doc_path = ?", (doc_path,))
    )
    existing_ids = [r["id"] for r in existing_rows]
    existing_rowids = [r["rowid"] for r in existing_rows]
    if existing_ids:
        # Clear FTS entries for each of this doc's existing blocks, then
        # drop the blocks themselves + their refs/links/symbols rows.
        for rid in existing_rowids:
            conn.execute("DELETE FROM blocks_fts WHERE rowid = ?", (rid,))
        qmarks = ",".join("?" for _ in existing_ids)
        conn.execute(f"DELETE FROM refs WHERE src_id IN ({qmarks})", existing_ids)
        conn.execute(f"DELETE FROM wiki_links WHERE src_id IN ({qmarks})", existing_ids)
        conn.execute(f"DELETE FROM symbols WHERE canonical_id IN ({qmarks})", existing_ids)
        conn.execute(f"DELETE FROM blocks WHERE id IN ({qmarks})", existing_ids)

    for b in blocks:
        conn.execute(
            """INSERT OR REPLACE INTO blocks
               (id, doc_path, doc_type, heading_slug, heading_level,
                line_start, line_end, content, universal, token_estimate,
                meta_json, indexed_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                b.id, b.doc_path, b.doc_type, b.heading_slug,
                b.heading_level, b.line_start, b.line_end, b.content,
                1 if b.universal else 0, b.token_estimate,
                json.dumps(b.meta, ensure_ascii=False),
                now,
            ),
        )
        # Mirror into FTS5 keyed by blocks.rowid so we can de-dupe on
        # updates without needing a trigger.
        row = conn.execute("SELECT rowid FROM blocks WHERE id = ?", (b.id,)).fetchone()
        if row is not None:
            conn.execute("DELETE FROM blocks_fts WHERE rowid = ?", (row["rowid"],))
            conn.execute(
                "INSERT INTO blocks_fts(rowid, content, heading_slug, doc_type, meta_json) VALUES (?,?,?,?,?)",
                (row["rowid"], b.content, b.heading_slug or "", b.doc_type, json.dumps(b.meta)),
            )


def record_doc_state(
    conn: sqlite3.Connection,
    doc_path: str,
    mtime_ns: int,
    content_hash: str,
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO doc_index(doc_path, mtime_ns, content_hash, indexed_at) VALUES (?,?,?,?)",
        (doc_path, mtime_ns, content_hash, _now_iso()),
    )


def get_doc_state(
    conn: sqlite3.Connection, doc_path: str
) -> Optional[Tuple[int, str]]:
    r = conn.execute(
        "SELECT mtime_ns, content_hash FROM doc_index WHERE doc_path = ?", (doc_path,)
    ).fetchone()
    if r is None:
        return None
    return int(r["mtime_ns"] or 0), str(r["content_hash"] or "")


def delete_document(conn: sqlite3.Connection, doc_path: str) -> None:
    rows = list(conn.execute("SELECT id FROM blocks WHERE doc_path = ?", (doc_path,)))
    ids = [r["id"] for r in rows]
    if ids:
        qmarks = ",".join("?" for _ in ids)
        # FTS5 cleanup
        for bid in ids:
            r = conn.execute("SELECT rowid FROM blocks WHERE id = ?", (bid,)).fetchone()
            if r is not None:
                conn.execute("DELETE FROM blocks_fts WHERE rowid = ?", (r["rowid"],))
        conn.execute(f"DELETE FROM refs WHERE src_id IN ({qmarks})", ids)
        conn.execute(f"DELETE FROM wiki_links WHERE src_id IN ({qmarks})", ids)
        conn.execute(f"DELETE FROM symbols WHERE canonical_id IN ({qmarks})", ids)
        conn.execute(f"DELETE FROM blocks WHERE id IN ({qmarks})", ids)
    conn.execute("DELETE FROM doc_index WHERE doc_path = ?", (doc_path,))


def all_docs(conn: sqlite3.Connection) -> List[str]:
    return [r["doc_path"] for r in conn.execute("SELECT doc_path FROM doc_index")]


def all_blocks(conn: sqlite3.Connection) -> List[sqlite3.Row]:
    return list(conn.execute("SELECT * FROM blocks"))


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def get_block(conn: sqlite3.Connection, block_id: str) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM blocks WHERE id = ?", (block_id,)).fetchone()


def get_blocks_for_doc(conn: sqlite3.Connection, doc_path: str) -> List[sqlite3.Row]:
    return list(
        conn.execute(
            "SELECT * FROM blocks WHERE doc_path = ? ORDER BY line_start ASC",
            (doc_path,),
        )
    )


def get_universal_blocks(conn: sqlite3.Connection) -> List[sqlite3.Row]:
    return list(conn.execute("SELECT * FROM blocks WHERE universal = 1 ORDER BY doc_path, line_start"))
