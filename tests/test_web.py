"""Smoke tests for `pedia.web.server` handlers (HW-0046).

We exercise the module-level handler functions directly rather than
spinning up a socket — keeps the tests hermetic and fast on Windows
where sandboxed ports are flaky.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from pedia import refresh as refresh_mod
from pedia.cli import (
    CONFIG_EXAMPLE,
    DECISION_EXAMPLE,
    NORTH_STAR_EXAMPLE,
    CONSTITUTION_EXAMPLE,
    SPEC_EXAMPLE,
)
from pedia.web import server as web


@pytest.fixture
def seeded_root(tmp_path: Path) -> Path:
    root = tmp_path
    base = root / ".pedia"
    for sub in ("north-stars", "constitution", "specs/001-example", "decisions"):
        (base / sub).mkdir(parents=True, exist_ok=True)
    (base / "config.yaml").write_text(CONFIG_EXAMPLE, encoding="utf-8")
    (base / "north-stars" / "01-agent-first.md").write_text(
        NORTH_STAR_EXAMPLE, encoding="utf-8"
    )
    (base / "constitution" / "technical.md").write_text(
        CONSTITUTION_EXAMPLE, encoding="utf-8"
    )
    (base / "specs" / "001-example" / "spec.md").write_text(
        SPEC_EXAMPLE, encoding="utf-8"
    )
    (base / "decisions" / "0001-content-hash-block-ids.md").write_text(
        DECISION_EXAMPLE, encoding="utf-8"
    )
    refresh_mod.refresh(root, full=True)
    return root


def test_toc_groups_and_counts(seeded_root: Path) -> None:
    toc = web.handle_toc(seeded_root)
    # counts reflect seeded fixtures
    counts = toc["counts"]
    assert counts["north-star"] == 1
    assert counts["constitution"] == 1
    assert counts["spec"] == 1
    assert counts["decision"] == 1
    # grouped payload carries the path
    north = toc["groups"]["north-star"][0]
    assert north["path"] == "north-stars/01-agent-first.md"
    assert north["doc_type"] == "north-star"


def test_doc_endpoint_renders_markdown_and_refs(seeded_root: Path) -> None:
    payload = web.handle_doc(seeded_root, "specs/001-example/spec.md")
    assert payload is not None
    assert payload["doc_type"] == "spec"
    assert "Flow Network" in payload["markdown"]
    # The seeded spec cites decision 0001 — that should show up as refs_out
    assert any(
        r["doc_path"] == "decisions/0001-content-hash-block-ids.md"
        for r in payload["refs_out"]
    )
    # blocks carry heading titles (not just slugs)
    titles = {b["heading_title"] for b in payload["blocks"]}
    assert "Flow Network" in titles


def test_doc_endpoint_missing_returns_none(seeded_root: Path) -> None:
    assert web.handle_doc(seeded_root, "does/not/exist.md") is None


def test_block_and_graph_roundtrip(seeded_root: Path) -> None:
    # find a block id via the doc endpoint, then fetch it via handle_block
    doc = web.handle_doc(seeded_root, "specs/001-example/spec.md")
    assert doc is not None
    flow_block = next(b for b in doc["blocks"] if b["heading_slug"] == "flow-network")
    bid = flow_block["id"]

    block = web.handle_block(seeded_root, bid)
    assert block is not None
    assert block["heading_slug"] == "flow-network"
    # prefix lookup works too
    block_prefix = web.handle_block(seeded_root, bid[:8])
    assert block_prefix is not None and block_prefix["id"] == bid

    graph = web.handle_graph(seeded_root, bid, depth=2)
    assert graph is not None
    assert graph["anchor"] == bid
    # anchor flag is set on exactly one node
    anchors = [n for n in graph["nodes"] if n["data"]["is_anchor"]]
    assert len(anchors) == 1 and anchors[0]["id"] == bid
    # the cited decision block should be present
    doc_paths = {n["data"]["doc_path"] for n in graph["nodes"]}
    assert "decisions/0001-content-hash-block-ids.md" in doc_paths


def test_query_endpoint_returns_universal_and_matches(seeded_root: Path) -> None:
    res = web.handle_query(seeded_root, "flow network", limit=5)
    assert res["query"] == "flow network"
    # Universal context (north-star + constitution) is always prepended
    assert len(res["universal"]) >= 1
    # "Flow Network" heading is the top spec match
    assert any(m["heading_slug"] == "flow-network" for m in res["matches"])


def test_external_links_from_config(seeded_root: Path) -> None:
    payload = web.handle_external_links(seeded_root)
    templates = payload["templates"]
    # Parsed from the seeded config.yaml
    assert "hopewell" in templates
    assert templates["hopewell"]["template"].startswith("http://localhost:8765")
    assert "github_issues" in templates


def test_trace_endpoint_walks_both_directions(seeded_root: Path) -> None:
    doc = web.handle_doc(seeded_root, "specs/001-example/spec.md")
    assert doc is not None
    flow_block = next(b for b in doc["blocks"] if b["heading_slug"] == "flow-network")
    payload = web.handle_trace(seeded_root, flow_block["id"], depth=3)
    assert payload is not None
    # up should include the decision it cites
    up_ids = [r["doc_path"] for r in payload["up"]]
    assert "decisions/0001-content-hash-block-ids.md" in up_ids


def test_external_links_parser_handles_two_level_nesting() -> None:
    text = (
        "version: 1\n"
        "external_links:\n"
        "  hopewell:\n"
        '    template: "http://x/{id}"\n'
        '    link_when: "always"\n'
        "  jira:\n"
        '    template: "https://j/{id}"\n'
        "# trailing comment\n"
    )
    out = web._parse_external_links_section(text)
    assert out is not None
    assert out["hopewell"]["template"] == "http://x/{id}"
    assert out["hopewell"]["link_when"] == "always"
    assert out["jira"]["template"] == "https://j/{id}"
