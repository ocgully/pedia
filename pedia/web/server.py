"""Stdlib http.server for `pedia web` (HW-0046).

Design notes (mirrors TaskFlow's web/server.py, minus SSE):

* Stdlib-only. `http.server` + `sqlite3` + `json` + `pathlib`. No
  FastAPI, no watchdog, no SSE. Pedia is commit-paced -- if the index
  changes you can just refresh the browser.
* Read-only. Every handler is GET; there are no POST/PUT/PATCH/DELETE
  routes. All mutations go through the `pedia` CLI.
* Thin adapter. Handlers call into `pedia.query`, `pedia.trace`,
  `pedia.index`, and `pedia.config` -- the same code the CLI uses.
  No alternate search path, no alternate storage.
* Deterministic graph layout is done on the client (elkjs); the server
  just emits `{nodes, edges}` with minimal shape info.

CLI entry: `pedia web --port 8766 [--open]` wires through
`pedia.cli.cmd_web` to `run(project_root, port, open_browser)`.
"""

from __future__ import annotations

import http.server
import json
import socketserver
import sqlite3
import sys
import threading
import time
import urllib.parse
import webbrowser
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pedia import __version__ as PEDIA_VERSION
from pedia import config as cfg
from pedia import index as idx
from pedia import query as qmod
from pedia import trace as trace_mod


STATIC_DIR = Path(__file__).parent / "static"


# ---------------------------------------------------------------------------
# TOC (top-down discovery entry point)
# ---------------------------------------------------------------------------


TYPE_ORDER = [
    "north-star",
    "vision",
    "constitution",
    "spec",
    "prd",
    "technical-requirement",
    "plan",
    "decision",
    "documentation",
]


def _doc_index(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    """One row per (doc_path, doc_type) with a heuristic display title.

    The first heading block for a doc_path (lowest line_start) supplies
    the title; if no headings exist, the last path segment is used.
    """
    rows = list(
        conn.execute(
            """
            SELECT doc_path, doc_type, heading_slug, meta_json, line_start
              FROM blocks
            ORDER BY doc_path, line_start
            """
        )
    )
    docs: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        dp = r["doc_path"]
        if dp in docs:
            continue
        meta: Dict[str, Any] = {}
        try:
            meta = json.loads(r["meta_json"]) if r["meta_json"] else {}
        except Exception:
            meta = {}
        title = meta.get("heading_title") or r["heading_slug"] or dp.split("/")[-1]
        docs[dp] = {
            "path": dp,
            "doc_type": r["doc_type"],
            "title": title,
        }
    return list(docs.values())


def handle_toc(root: Path) -> Dict[str, Any]:
    conn = idx.connect(cfg.db_path(root))
    try:
        docs = _doc_index(conn)
        grouped: Dict[str, List[Dict[str, Any]]] = {t: [] for t in TYPE_ORDER}
        for d in docs:
            t = d["doc_type"] if d["doc_type"] in grouped else "documentation"
            grouped.setdefault(t, []).append(d)
        # sort each group by path
        for t in grouped:
            grouped[t].sort(key=lambda x: x["path"])
        return {
            "project_root": str(root),
            "pedia_version": PEDIA_VERSION,
            "counts": {t: len(v) for t, v in grouped.items()},
            "groups": grouped,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Doc view (markdown + blocks + in/out refs)
# ---------------------------------------------------------------------------


def _refs_out(conn: sqlite3.Connection, block_id: str) -> List[Dict[str, Any]]:
    rows = list(
        conn.execute(
            """
            SELECT b.id, b.doc_path, b.heading_slug, b.meta_json, r.kind
              FROM refs r
              JOIN blocks b ON b.id = r.dst_id
             WHERE r.src_id = ?
            """,
            (block_id,),
        )
    )
    return [_ref_row(r) for r in rows]


def _refs_in(conn: sqlite3.Connection, block_id: str) -> List[Dict[str, Any]]:
    rows = list(
        conn.execute(
            """
            SELECT b.id, b.doc_path, b.heading_slug, b.meta_json, r.kind
              FROM refs r
              JOIN blocks b ON b.id = r.src_id
             WHERE r.dst_id = ?
            """,
            (block_id,),
        )
    )
    return [_ref_row(r) for r in rows]


def _ref_row(r: sqlite3.Row) -> Dict[str, Any]:
    meta: Dict[str, Any] = {}
    try:
        meta = json.loads(r["meta_json"]) if r["meta_json"] else {}
    except Exception:
        pass
    return {
        "id": r["id"],
        "doc_path": r["doc_path"],
        "heading_slug": r["heading_slug"],
        "heading_title": meta.get("heading_title") or r["heading_slug"] or "",
        "kind": r["kind"],
    }


def _unresolved_wiki_links(conn: sqlite3.Connection, block_id: str) -> List[Dict[str, Any]]:
    rows = list(
        conn.execute(
            "SELECT raw, form FROM wiki_links WHERE src_id = ? AND dst_id IS NULL",
            (block_id,),
        )
    )
    return [{"raw": r["raw"], "form": r["form"]} for r in rows]


def handle_doc(root: Path, path: str) -> Optional[Dict[str, Any]]:
    """Return the markdown body + block metadata for a .pedia-relative path."""
    if path.startswith(".pedia/"):
        path = path[len(".pedia/") :]
    full = cfg.pedia_dir(root) / path
    if not full.is_file():
        return None
    try:
        markdown = full.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    conn = idx.connect(cfg.db_path(root))
    try:
        block_rows = idx.get_blocks_for_doc(conn, path)
        blocks: List[Dict[str, Any]] = []
        refs_out_all: Dict[str, Dict[str, Any]] = {}
        refs_in_all: Dict[str, Dict[str, Any]] = {}
        for r in block_rows:
            meta: Dict[str, Any] = {}
            try:
                meta = json.loads(r["meta_json"]) if r["meta_json"] else {}
            except Exception:
                pass
            blocks.append(
                {
                    "id": r["id"],
                    "heading_slug": r["heading_slug"],
                    "heading_title": meta.get("heading_title") or r["heading_slug"] or "",
                    "heading_level": r["heading_level"],
                    "line_start": r["line_start"],
                    "line_end": r["line_end"],
                    "universal": bool(r["universal"]),
                    "kind": meta.get("kind") or "heading",
                }
            )
            for ref in _refs_out(conn, r["id"]):
                refs_out_all.setdefault(ref["id"], ref)
            for ref in _refs_in(conn, r["id"]):
                refs_in_all.setdefault(ref["id"], ref)
        # doc-level summary: filter out refs internal to this doc for clarity
        self_ids = {b["id"] for b in blocks}
        refs_out_list = [
            r for rid, r in refs_out_all.items() if rid not in self_ids
        ]
        refs_in_list = [
            r for rid, r in refs_in_all.items() if rid not in self_ids
        ]
        # doc meta: pull from first block's front-matter
        doc_meta: Dict[str, Any] = {}
        if block_rows:
            try:
                m0 = json.loads(block_rows[0]["meta_json"]) if block_rows[0]["meta_json"] else {}
                doc_meta = m0.get("front_matter") or {}
            except Exception:
                doc_meta = {}
        return {
            "path": path,
            "doc_type": block_rows[0]["doc_type"] if block_rows else "documentation",
            "doc_meta": doc_meta,
            "markdown": markdown,
            "blocks": blocks,
            "refs_out": refs_out_list,
            "refs_in": refs_in_list,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Block view (deep link target)
# ---------------------------------------------------------------------------


def handle_block(root: Path, block_id: str) -> Optional[Dict[str, Any]]:
    conn = idx.connect(cfg.db_path(root))
    try:
        row = idx.get_block(conn, block_id)
        if row is None:
            # prefix match (CLI convention)
            rows = list(
                conn.execute(
                    "SELECT * FROM blocks WHERE id LIKE ? LIMIT 2",
                    (block_id + "%",),
                )
            )
            if len(rows) != 1:
                return None
            row = rows[0]
        meta: Dict[str, Any] = {}
        try:
            meta = json.loads(row["meta_json"]) if row["meta_json"] else {}
        except Exception:
            pass
        return {
            "id": row["id"],
            "doc_path": row["doc_path"],
            "doc_type": row["doc_type"],
            "heading_slug": row["heading_slug"],
            "heading_title": meta.get("heading_title") or row["heading_slug"] or "",
            "heading_level": row["heading_level"],
            "line_start": row["line_start"],
            "line_end": row["line_end"],
            "universal": bool(row["universal"]),
            "content": row["content"],
            "front_matter": meta.get("front_matter") or {},
            "refs_out": _refs_out(conn, row["id"]),
            "refs_in": _refs_in(conn, row["id"]),
            "unresolved_wiki_links": _unresolved_wiki_links(conn, row["id"]),
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Query (same path as CLI)
# ---------------------------------------------------------------------------


def handle_query(
    root: Path,
    q: str,
    *,
    doc_type: Optional[str] = None,
    token_budget: int = 2000,
    limit: int = 10,
) -> Dict[str, Any]:
    result = qmod.run_query(
        root,
        q,
        doc_type=doc_type,
        token_budget=token_budget,
        limit=limit,
    )
    # QueryResult.to_json is a string; re-parse so we can embed it cleanly
    return json.loads(result.to_json())


# ---------------------------------------------------------------------------
# Graph (React Flow-shaped payload for the graph view)
# ---------------------------------------------------------------------------


DOC_TYPE_RANK = {
    "north-star": 0,
    "vision": 1,
    "constitution": 1,
    "prd": 2,
    "spec": 3,
    "technical-requirement": 3,
    "plan": 4,
    "decision": 5,
    "documentation": 6,
}


def handle_graph(root: Path, block_id: str, depth: int = 2) -> Optional[Dict[str, Any]]:
    """BFS a neighborhood around `block_id` (both directions) up to `depth`
    and return React-Flow-shaped {nodes, edges}. Client lays out with
    elkjs using its layered algorithm (same as the TaskFlow canvas).
    """
    conn = idx.connect(cfg.db_path(root))
    try:
        # confirm block exists (accept prefix)
        anchor_row = idx.get_block(conn, block_id)
        if anchor_row is None:
            rows = list(
                conn.execute(
                    "SELECT * FROM blocks WHERE id LIKE ? LIMIT 2",
                    (block_id + "%",),
                )
            )
            if len(rows) != 1:
                return None
            anchor_row = rows[0]
        anchor_id = anchor_row["id"]

        # BFS both up (what this cites) and down (what cites this)
        visited: Dict[str, int] = {anchor_id: 0}
        edges: List[Tuple[str, str, str]] = []  # (src, dst, kind)
        frontier: List[Tuple[str, int]] = [(anchor_id, 0)]
        while frontier:
            cur, d = frontier.pop(0)
            if d >= depth:
                continue
            # outgoing (cites)
            for r in conn.execute(
                "SELECT dst_id, kind FROM refs WHERE src_id = ?", (cur,)
            ):
                dst = r["dst_id"]
                if not dst:
                    continue
                edges.append((cur, dst, r["kind"]))
                if dst not in visited:
                    visited[dst] = d + 1
                    frontier.append((dst, d + 1))
            # incoming (cited-by)
            for r in conn.execute(
                "SELECT src_id, kind FROM refs WHERE dst_id = ?", (cur,)
            ):
                src = r["src_id"]
                if not src:
                    continue
                edges.append((src, cur, r["kind"]))
                if src not in visited:
                    visited[src] = d + 1
                    frontier.append((src, d + 1))

        # materialize nodes
        nodes: List[Dict[str, Any]] = []
        if visited:
            qmarks = ",".join("?" for _ in visited)
            rows = list(
                conn.execute(
                    f"SELECT * FROM blocks WHERE id IN ({qmarks})",
                    list(visited.keys()),
                )
            )
            for r in rows:
                meta: Dict[str, Any] = {}
                try:
                    meta = json.loads(r["meta_json"]) if r["meta_json"] else {}
                except Exception:
                    pass
                rank = DOC_TYPE_RANK.get(r["doc_type"], 6)
                nodes.append(
                    {
                        "id": r["id"],
                        "type": "pediaBlock",
                        "data": {
                            "label": meta.get("heading_title")
                            or r["heading_slug"]
                            or r["doc_path"],
                            "doc_path": r["doc_path"],
                            "doc_type": r["doc_type"],
                            "universal": bool(r["universal"]),
                            "is_anchor": r["id"] == anchor_id,
                            "rank": rank,
                        },
                        # elkjs will overwrite on the client.
                        "position": {"x": rank * 220, "y": 0},
                    }
                )
        # de-dupe edges
        seen = set()
        edge_list: List[Dict[str, Any]] = []
        for src, dst, kind in edges:
            key = (src, dst, kind)
            if key in seen:
                continue
            seen.add(key)
            edge_list.append(
                {
                    "id": f"{src}->{dst}:{kind}",
                    "source": src,
                    "target": dst,
                    "data": {"kind": kind},
                    "label": kind if kind != "cites" else "",
                }
            )

        return {
            "anchor": anchor_id,
            "depth": depth,
            "nodes": nodes,
            "edges": edge_list,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Trace (full thread of impact -- collapsible tree upstream + downstream)
# ---------------------------------------------------------------------------


def handle_trace(root: Path, block_id: str, depth: int = 5) -> Optional[Dict[str, Any]]:
    conn = idx.connect(cfg.db_path(root))
    try:
        row = idx.get_block(conn, block_id)
        if row is None:
            rows = list(
                conn.execute(
                    "SELECT * FROM blocks WHERE id LIKE ? LIMIT 2",
                    (block_id + "%",),
                )
            )
            if len(rows) != 1:
                return None
            row = rows[0]
            block_id = row["id"]
    finally:
        conn.close()
    up = trace_mod.walk(root, block_id, "up", depth=depth)
    down = trace_mod.walk(root, block_id, "down", depth=depth)
    return {"anchor": block_id, "up": up, "down": down}


# ---------------------------------------------------------------------------
# External-link templates
# ---------------------------------------------------------------------------


DEFAULT_EXTERNAL_LINKS = {
    # TaskFlow. Both keys are present so existing pedia configs with
    # `hopewell` continue to work as a backwards-compat alias.
    "taskflow": {
        "template": "http://localhost:8765/#/doc/{id}",
        "link_when": "block front-matter has `taskflow_id` (or legacy `hopewell_id`) OR block cites [[tf:TF-NNNN]] or [[hw:HW-NNNN]]",
    },
    "hopewell": {
        "template": "http://localhost:8765/#/doc/{id}",
        "link_when": "block front-matter has `hopewell_id` OR block cites [[hw:HW-NNNN]]",
    },
    "github_issues": {
        "template": "https://github.com/{repo}/issues/{id}",
    },
    "jira": {
        "template": "https://{instance}.atlassian.net/browse/{id}",
    },
    "github_code": {
        "template": "https://github.com/{repo}/blob/{sha}/{path}#L{line}",
    },
}


def _parse_external_links_section(config_text: str) -> Optional[Dict[str, Any]]:
    """Hand-parse the `external_links:` 2-level-nested map from config.yaml.

    `pedia.config.load_yaml_lite` deliberately only supports one level of
    nesting; `external_links.<system>.template` is two levels. Rather
    than complicate the generic loader, we do a focused extraction here
    because this is the only config section that needs the deeper shape.
    """
    lines = config_text.splitlines()
    # find the section header
    start = -1
    for i, raw in enumerate(lines):
        if raw.startswith("external_links:") or raw.rstrip() == "external_links:":
            start = i
            break
    if start < 0:
        return None
    # collect indented block after the header
    block_lines: List[str] = []
    for raw in lines[start + 1:]:
        if not raw.strip():
            block_lines.append(raw)
            continue
        if raw.lstrip() != raw and raw.lstrip().startswith("#"):
            continue
        if raw.lstrip() == raw:
            # un-indented line means the section ended
            break
        block_lines.append(raw)
    # parse: top-level keys at indent==2; values at indent==4 under them
    result: Dict[str, Dict[str, Any]] = {}
    cur_key: Optional[str] = None
    for raw in block_lines:
        stripped = raw.rstrip()
        if not stripped.strip() or stripped.strip().startswith("#"):
            continue
        # count leading spaces
        n = len(raw) - len(raw.lstrip(" "))
        body = raw.strip()
        if n <= 2 and body.endswith(":"):
            cur_key = body[:-1].strip()
            result[cur_key] = {}
            continue
        if cur_key is not None and n >= 4 and ":" in body:
            k, _, v = body.partition(":")
            k = k.strip()
            v = v.strip()
            # strip surrounding quotes
            if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
                v = v[1:-1]
            result[cur_key][k] = v
    return result or None


def handle_external_links(root: Path) -> Dict[str, Any]:
    # Prefer a focused hand-parse of the config file so nested templates
    # round-trip correctly (the generic yaml-lite loader flattens them).
    templates: Optional[Dict[str, Any]] = None
    try:
        p = cfg.config_path(root)
        if p.is_file():
            text = p.read_text(encoding="utf-8")
            templates = _parse_external_links_section(text)
    except Exception:
        templates = None
    if not templates:
        templates = DEFAULT_EXTERNAL_LINKS
    return {"templates": templates}


# ---------------------------------------------------------------------------
# HTTP server
# ---------------------------------------------------------------------------


class PediaWebHandler(http.server.BaseHTTPRequestHandler):
    # `root` is attached at class-creation time in `run(...)`.
    server_version = f"pedia-web/{PEDIA_VERSION}"

    # Keep the default stderr logger quiet — still emit access lines
    # because this is a local dev tool and they're useful.
    def log_message(self, fmt: str, *args: Any) -> None:  # type: ignore[override]
        sys.stderr.write(
            "[pedia-web] %s - %s\n"
            % (self.address_string(), fmt % args)
        )

    # -- helpers -----------------------------------------------------------

    def _send_json(self, code: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error_json(self, code: int, message: str) -> None:
        self._send_json(code, {"error": message})

    def _send_static(self, rel: str) -> None:
        # static files live under pedia/web/static/
        rel = rel.lstrip("/").replace("..", "")
        target = STATIC_DIR / rel
        if not target.is_file():
            self._send_error_json(404, f"not found: {rel}")
            return
        ctype = _guess_mime(target.name)
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # -- routing -----------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 (http.server API)
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)

        root: Path = self.server.project_root  # type: ignore[attr-defined]

        try:
            if path == "/" or path == "/index.html":
                return self._send_static("index.html")

            if path.startswith("/static/"):
                return self._send_static(path[len("/static/") :])

            if path == "/api/toc":
                return self._send_json(200, handle_toc(root))

            if path == "/api/doc":
                dp = (qs.get("path") or [""])[0]
                if not dp:
                    return self._send_error_json(400, "missing ?path=")
                payload = handle_doc(root, dp)
                if payload is None:
                    return self._send_error_json(404, f"no doc at {dp}")
                return self._send_json(200, payload)

            if path.startswith("/api/block/"):
                bid = path[len("/api/block/") :]
                payload = handle_block(root, bid)
                if payload is None:
                    return self._send_error_json(404, f"no block: {bid}")
                return self._send_json(200, payload)

            if path == "/api/query":
                q = (qs.get("q") or [""])[0]
                if not q:
                    return self._send_error_json(400, "missing ?q=")
                doc_type = (qs.get("type") or [None])[0]
                try:
                    budget = int((qs.get("budget") or ["2000"])[0])
                except ValueError:
                    budget = 2000
                try:
                    limit = int((qs.get("limit") or ["10"])[0])
                except ValueError:
                    limit = 10
                return self._send_json(
                    200,
                    handle_query(
                        root, q,
                        doc_type=doc_type,
                        token_budget=budget,
                        limit=limit,
                    ),
                )

            if path == "/api/graph":
                bid = (qs.get("block") or [""])[0]
                if not bid:
                    return self._send_error_json(400, "missing ?block=")
                try:
                    depth = int((qs.get("depth") or ["2"])[0])
                except ValueError:
                    depth = 2
                payload = handle_graph(root, bid, depth=depth)
                if payload is None:
                    return self._send_error_json(404, f"no block: {bid}")
                return self._send_json(200, payload)

            if path == "/api/trace":
                bid = (qs.get("block") or [""])[0]
                if not bid:
                    return self._send_error_json(400, "missing ?block=")
                try:
                    depth = int((qs.get("depth") or ["5"])[0])
                except ValueError:
                    depth = 5
                payload = handle_trace(root, bid, depth=depth)
                if payload is None:
                    return self._send_error_json(404, f"no block: {bid}")
                return self._send_json(200, payload)

            if path == "/api/external-links":
                return self._send_json(200, handle_external_links(root))

            if path == "/api/meta":
                return self._send_json(
                    200,
                    {
                        "project_root": str(root),
                        "pedia_version": PEDIA_VERSION,
                    },
                )

            return self._send_error_json(404, f"no route: {path}")
        except BrokenPipeError:
            # client closed the connection; nothing to do
            return
        except Exception as e:  # pragma: no cover -- best-effort error render
            try:
                self._send_error_json(500, f"{type(e).__name__}: {e}")
            except Exception:
                pass


def _guess_mime(name: str) -> str:
    name = name.lower()
    if name.endswith(".html"):
        return "text/html; charset=utf-8"
    if name.endswith(".js") or name.endswith(".mjs"):
        return "application/javascript; charset=utf-8"
    if name.endswith(".css"):
        return "text/css; charset=utf-8"
    if name.endswith(".json"):
        return "application/json; charset=utf-8"
    if name.endswith(".svg"):
        return "image/svg+xml"
    return "application/octet-stream"


class _ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    """One thread per request. Queries are cheap, SQLite handles concurrent
    reads fine, and this keeps slow clients from blocking the main loop."""

    daemon_threads = True
    allow_reuse_address = True


def run(
    project_root: Path,
    *,
    port: int = 8766,
    open_browser: bool = False,
    host: str = "127.0.0.1",
) -> int:
    """Start the read-only wiki server. Blocks until Ctrl+C."""
    if not cfg.pedia_dir(project_root).is_dir():
        sys.stderr.write(
            f"error: no .pedia/ directory at {project_root}. Run `pedia init` first.\n"
        )
        return 2
    if not cfg.db_path(project_root).is_file():
        sys.stderr.write(
            "warning: .pedia/index.sqlite not found. "
            "Run `pedia refresh` first for full content; launching anyway.\n"
        )

    server = _ThreadingHTTPServer((host, port), PediaWebHandler)
    server.project_root = project_root  # type: ignore[attr-defined]
    url = f"http://{host}:{port}/"
    sys.stdout.write(
        f"pedia web {PEDIA_VERSION} -- serving {project_root} at {url}\n"
        "(read-only; Ctrl+C to stop)\n"
    )
    if open_browser:
        # Open after a short delay so the server is accepting.
        def _open() -> None:
            time.sleep(0.4)
            try:
                webbrowser.open(url)
            except Exception:
                pass

        threading.Thread(target=_open, daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        sys.stdout.write("\npedia web: stopping\n")
    finally:
        server.server_close()
    return 0
