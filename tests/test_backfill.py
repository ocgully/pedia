"""Backfill smoke tests.

Exercises:
  * filesystem classification on a synthetic project tree
  * idempotency (second run reports 0 added/updated)
  * dry-run does not write to disk
  * web crawler against a local http.server serving fixture HTML
"""
from __future__ import annotations

import http.server
import io
import socketserver
import subprocess
import sys
import threading
import urllib.request
from contextlib import closing
from pathlib import Path

import pytest

from pedia import backfill as backfill_mod
from pedia import backfill_fs as bfs
from pedia import backfill_web as bweb


# ---------------------------------------------------------------------------
# synthetic project tree
# ---------------------------------------------------------------------------


def _make_project(root: Path) -> None:
    (root / "README.md").write_text("# My Project\n\nIntro.\n", encoding="utf-8")
    (root / "CHANGELOG.md").write_text("# Changes\n\n- v0.1\n", encoding="utf-8")
    (root / "ARCHITECTURE.md").write_text("# Arch\n\nDiagram.\n", encoding="utf-8")

    (root / "docs").mkdir()
    (root / "docs" / "tutorial.md").write_text("# Tutorial\n\nSteps.\n", encoding="utf-8")
    (root / "docs" / "adr").mkdir()
    (root / "docs" / "adr" / "0001-use-sqlite.md").write_text(
        "# 0001 - Use SQLite\n\n## Status\nAccepted\n\n## Context\nWe need storage.\n\n"
        "## Decision\nUse SQLite.\n\n## Consequences\nSimple.\n",
        encoding="utf-8",
    )

    (root / "specs").mkdir()
    (root / "specs" / "042-canvas").mkdir()
    (root / "specs" / "042-canvas" / "spec.md").write_text(
        "# Canvas Spec\n\nSee [the ADR](../../docs/adr/0001-use-sqlite.md).\n",
        encoding="utf-8",
    )
    (root / "specs" / "042-canvas" / "plan.md").write_text("# Plan\n\nSteps.\n", encoding="utf-8")

    (root / ".specify" / "memory").mkdir(parents=True)
    (root / ".specify" / "memory" / "constitution.md").write_text(
        "# Constitution\n\nTenets live here.\n", encoding="utf-8"
    )


def test_filesystem_classification(tmp_path: Path):
    _make_project(tmp_path)
    plan = bfs.plan_filesystem(tmp_path)
    by_src = {it.source_rel.replace("\\", "/"): it for it in plan.items}

    assert "README.md" in by_src and by_src["README.md"].doc_type == "documentation"
    assert by_src["ARCHITECTURE.md"].doc_type == "documentation"
    assert by_src["CHANGELOG.md"].doc_type == "documentation"

    # SpecKit-shaped specs
    spec = by_src["specs/042-canvas/spec.md"]
    assert spec.doc_type == "spec"
    assert spec.dest_subpath == "specs/042-canvas/spec.md"
    plan_item = by_src["specs/042-canvas/plan.md"]
    assert plan_item.doc_type == "plan"
    assert plan_item.dest_subpath == "specs/042-canvas/plan.md"

    # ADR (path-aware dest so cross-tree ADRs don't collide)
    adr = by_src["docs/adr/0001-use-sqlite.md"]
    assert adr.doc_type == "decision"
    assert adr.dest_subpath.startswith("decisions/")
    assert "0001-use-sqlite" in adr.dest_subpath

    # Constitution
    cst = by_src[".specify/memory/constitution.md"]
    assert cst.doc_type == "constitution"
    assert cst.dest_subpath.startswith("constitution/")


def test_run_backfill_and_idempotency(tmp_path: Path):
    _make_project(tmp_path)

    r1 = backfill_mod.run_backfill(tmp_path)
    assert r1.added > 0
    assert r1.updated == 0
    # files actually landed
    assert (tmp_path / ".pedia" / "specs" / "042-canvas" / "spec.md").is_file()
    # ADR dest carries path context; just check something landed under decisions/
    decisions = list((tmp_path / ".pedia" / "decisions").glob("*.md"))
    assert decisions and any("0001-use-sqlite" in d.name for d in decisions)
    # Constitution file landed under constitution/ (path-aware name)
    constitution = list((tmp_path / ".pedia" / "constitution").glob("*.md"))
    assert constitution
    # .claudeignore written
    assert (tmp_path / ".claudeignore").is_file()

    # second run: no changes
    r2 = backfill_mod.run_backfill(tmp_path)
    assert r2.added == 0
    assert r2.updated == 0
    assert r2.unchanged > 0


def test_run_backfill_dry_run_writes_nothing(tmp_path: Path):
    _make_project(tmp_path)
    r = backfill_mod.run_backfill(tmp_path, dry_run=True)
    assert r.added + r.updated > 0
    # no actual files should have been written under specs/ etc.
    assert not (tmp_path / ".pedia" / "specs" / "042-canvas" / "spec.md").exists()


def test_link_rewriting(tmp_path: Path):
    _make_project(tmp_path)
    backfill_mod.run_backfill(tmp_path)
    ingested = (tmp_path / ".pedia" / "specs" / "042-canvas" / "spec.md").read_text(encoding="utf-8")
    # the relative markdown link into the adr should now point at the
    # rewritten destination path under decisions/ (either as a wiki-link
    # if it carried a #heading, or as a plain md link otherwise).
    assert "decisions/" in ingested and "0001-use-sqlite" in ingested
    # And it should NOT still be pointing at the old source path.
    assert "../../docs/adr/" not in ingested


def test_link_rewriting_with_heading_fragment(tmp_path: Path):
    _make_project(tmp_path)
    # overwrite spec to include a #heading link to the ADR
    (tmp_path / "specs" / "042-canvas" / "spec.md").write_text(
        "# Canvas Spec\n\nSee [status section](../../docs/adr/0001-use-sqlite.md#status).\n",
        encoding="utf-8",
    )
    backfill_mod.run_backfill(tmp_path)
    ingested = (tmp_path / ".pedia" / "specs" / "042-canvas" / "spec.md").read_text(encoding="utf-8")
    assert "[[decisions/" in ingested and "#status" in ingested


def test_init_autofires_backfill(tmp_path: Path, monkeypatch):
    _make_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    proc = subprocess.run(
        [sys.executable, "-m", "pedia", "init"],
        capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    # init should have run backfill automatically
    assert "pedia backfill" in proc.stdout.lower() or "backfill:" in proc.stdout.lower()
    assert (tmp_path / ".pedia" / "specs" / "042-canvas" / "spec.md").is_file()


def test_init_no_backfill_flag_suppresses(tmp_path: Path, monkeypatch):
    _make_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    proc = subprocess.run(
        [sys.executable, "-m", "pedia", "init", "--no-backfill"],
        capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    # spec/ADR should NOT be ingested
    assert not (tmp_path / ".pedia" / "specs" / "042-canvas" / "spec.md").exists()


# ---------------------------------------------------------------------------
# web crawler
# ---------------------------------------------------------------------------


_FIXTURE_PAGES = {
    "/": b"""<!doctype html><html><head><title>Docs Home</title></head>
<body>
<nav>ignored navigation</nav>
<main>
  <h1>Docs Home</h1>
  <p>Welcome. See <a href="/guide.html">the guide</a> or
  <a href="/api/intro.html">API intro</a>.</p>
  <ul><li>Point one</li><li>Point two</li></ul>
</main>
<footer>ignored footer</footer>
</body></html>""",
    "/guide.html": b"""<!doctype html><html><head><title>Guide</title></head>
<body><article><h1>Guide</h1><p>Guide body.</p>
<pre><code>example = 1</code></pre></article></body></html>""",
    "/api/intro.html": b"""<!doctype html><html><head><title>API Intro</title></head>
<body><main><h1>API Intro</h1><p>Use the things.</p></main></body></html>""",
    "/robots.txt": b"User-agent: *\nAllow: /\n",
}


class _FixtureHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = _FIXTURE_PAGES.get(self.path)
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        ctype = "text/plain" if self.path.endswith(".txt") else "text/html; charset=utf-8"
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass  # quiet


@pytest.fixture
def fixture_server():
    # bind to port 0 to avoid collisions
    srv = socketserver.TCPServer(("127.0.0.1", 0), _FixtureHandler)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        srv.shutdown()
        srv.server_close()


def test_web_crawler_bfs_same_origin(fixture_server: str):
    result = bweb.crawl(fixture_server + "/", max_depth=2, max_pages=10, timeout_s=5)
    urls = {p.url.rstrip("/") for p in result.pages}
    assert (fixture_server + "").rstrip("/") in urls or (fixture_server + "/").rstrip("/") in urls
    assert any(u.endswith("/guide.html") for u in urls)
    assert any(u.endswith("/api/intro.html") for u in urls)
    # markdown extraction swallowed nav/footer
    home = next(p for p in result.pages if p.url.rstrip("/") in (fixture_server.rstrip("/"), fixture_server + ""))
    assert "ignored navigation" not in home.markdown
    assert "ignored footer" not in home.markdown
    assert "Welcome" in home.markdown


def test_web_backfill_writes_docs(fixture_server: str, tmp_path: Path):
    r = backfill_mod.run_backfill(tmp_path, url=fixture_server + "/", depth=2, max_pages=10)
    assert r.web_pages >= 2
    imported = list((tmp_path / ".pedia" / "docs" / "imported").glob("*.md"))
    assert imported, "expected at least one imported doc"
    # idempotent
    r2 = backfill_mod.run_backfill(tmp_path, url=fixture_server + "/", depth=2, max_pages=10)
    assert r2.added == 0 and r2.updated == 0


def test_html_to_markdown_headings_and_links():
    md, title = bweb.html_to_markdown(
        "<html><head><title>T</title></head><body><main>"
        "<h2>Sec</h2><p>Hi <a href='/x'>link</a>.</p>"
        "</main></body></html>"
    )
    assert title == "T"
    assert "## Sec" in md
    assert "[link](/x)" in md
