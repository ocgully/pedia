"""Markdown document -> blocks.

A block is an addressable region of a document. Three ways to make one:

  1. Heading-section     -- an ATX heading and its content through the
                            next heading at equal-or-higher level.
  2. Explicit anchor     -- `<!-- pedia:block:slug -->` ...
                            `<!-- pedia:/block -->` overrides heading
                            segmentation inside that region.
  3. Line-range (opt-in) -- front-matter `blocks: [[45, 72]]` yields
                            additional blocks covering the named lines.

The block ID is a 16-hex-char prefix of SHA-256 over the normalized
body (trailing whitespace stripped per line, joined with '\\n').
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pedia.config import split_front_matter


HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
ANCHOR_OPEN_RE = re.compile(r"^\s*<!--\s*pedia:block:([A-Za-z0-9_\-]+)\s*-->\s*$")
ANCHOR_CLOSE_RE = re.compile(r"^\s*<!--\s*pedia:/block\s*-->\s*$")
WIKI_LINK_RE = re.compile(r"\[\[([^\[\]\|]+?)(?:\|([^\[\]]+?))?\]\]")


@dataclass
class Block:
    id: str
    doc_path: str
    doc_type: str
    heading_slug: Optional[str]
    heading_level: Optional[int]
    line_start: int
    line_end: int
    content: str
    universal: bool
    token_estimate: int
    meta: Dict[str, Any]
    kind: str  # heading | anchor | line-range | whole-document

    def as_row(self) -> Tuple[Any, ...]:
        import json
        return (
            self.id,
            self.doc_path,
            self.doc_type,
            self.heading_slug,
            self.heading_level,
            self.line_start,
            self.line_end,
            self.content,
            1 if self.universal else 0,
            self.token_estimate,
            json.dumps(self.meta, ensure_ascii=False),
        )


def slugify(text: str) -> str:
    norm = unicodedata.normalize("NFKD", text)
    norm = "".join(c for c in norm if not unicodedata.combining(c))
    norm = norm.lower()
    norm = re.sub(r"[^a-z0-9]+", "-", norm).strip("-")
    return norm or "section"


def estimate_tokens(text: str, chars_per_token: int = 4) -> int:
    if not text:
        return 0
    return max(1, (len(text) + chars_per_token - 1) // chars_per_token)


def normalize_for_hash(body: str) -> str:
    lines = [ln.rstrip() for ln in body.splitlines()]
    return "\n".join(lines).strip("\n")


def block_id_for(body: str) -> str:
    h = hashlib.sha256(normalize_for_hash(body).encode("utf-8")).hexdigest()
    return h[:16]


def extract_wiki_links(text: str) -> List[Tuple[str, str]]:
    """Return a list of (raw, target) tuples. `raw` is the exact bracketed
    text (without the outer brackets); `target` strips any `|display`."""
    out: List[Tuple[str, str]] = []
    for m in WIKI_LINK_RE.finditer(text):
        target = m.group(1).strip()
        raw = target + ("|" + m.group(2).strip() if m.group(2) else "")
        out.append((raw, target))
    return out


def detect_wiki_link_form(target: str) -> str:
    t = target.strip()
    if t.startswith("block:"):
        return "block-id"
    if "#" in t and not t.startswith("#"):
        # path#heading -- only if there's something that looks like a path
        head, _, _tail = t.partition("#")
        if "/" in head or head.endswith(".md"):
            return "path-heading"
    if ":" in t and "/" not in t.split(":", 1)[0]:
        # type:slug (no slashes in the type prefix)
        return "type-slug"
    return "term"


def parse_document(
    abs_path: Path,
    rel_path: str,
    default_doc_type: str = "documentation",
    chars_per_token: int = 4,
) -> Tuple[Dict[str, Any], List[Block]]:
    text = abs_path.read_text(encoding="utf-8", errors="replace")
    fm, body, body_start_line = split_front_matter(text)
    doc_type = str(fm.get("type") or default_doc_type)
    universal_default = bool(fm.get("universal_context") or fm.get("universal") or False)
    defines = fm.get("defines") or []
    if not isinstance(defines, list):
        defines = [defines]
    defines = [str(d) for d in defines if d]

    body_lines = body.splitlines()
    # -- locate anchor regions (close>open swallowed heading segmentation)
    anchor_regions: List[Tuple[int, int, str]] = []
    open_idx: Optional[Tuple[int, str]] = None
    for idx, ln in enumerate(body_lines):
        mo = ANCHOR_OPEN_RE.match(ln)
        if mo:
            open_idx = (idx, mo.group(1))
            continue
        mc = ANCHOR_CLOSE_RE.match(ln)
        if mc and open_idx is not None:
            anchor_regions.append((open_idx[0], idx, open_idx[1]))
            open_idx = None

    def in_anchor(body_line_idx: int) -> bool:
        for start, end, _slug in anchor_regions:
            if start <= body_line_idx <= end:
                return True
        return False

    blocks: List[Block] = []

    # -- heading segmentation (skipping lines inside anchor regions)
    heading_positions: List[Tuple[int, int, str]] = []  # (idx, level, title)
    for idx, ln in enumerate(body_lines):
        if in_anchor(idx):
            continue
        m = HEADING_RE.match(ln)
        if m:
            level = len(m.group(1))
            title = m.group(2).strip()
            heading_positions.append((idx, level, title))

    # segment boundaries
    for i, (idx, level, title) in enumerate(heading_positions):
        # content runs from idx (inclusive) through the line before the
        # next heading at equal-or-higher level, bounded to end of body.
        end_idx = len(body_lines) - 1
        for j in range(i + 1, len(heading_positions)):
            nxt_idx, nxt_level, _ = heading_positions[j]
            if nxt_level <= level:
                end_idx = nxt_idx - 1
                break
        # trim trailing blank lines
        while end_idx > idx and body_lines[end_idx].strip() == "":
            end_idx -= 1
        segment = "\n".join(body_lines[idx : end_idx + 1])
        slug = slugify(title)
        bid = block_id_for(segment)
        meta: Dict[str, Any] = {
            "front_matter": fm,
            "heading_title": title,
            "defines": defines if i == 0 else [],
            "kind": "heading",
        }
        if "auto_link" in fm:
            meta["auto_link"] = bool(fm["auto_link"])
        line_start = body_start_line + idx
        line_end = body_start_line + end_idx
        blocks.append(
            Block(
                id=bid,
                doc_path=rel_path,
                doc_type=doc_type,
                heading_slug=slug,
                heading_level=level,
                line_start=line_start,
                line_end=line_end,
                content=segment,
                universal=universal_default,
                token_estimate=estimate_tokens(segment, chars_per_token),
                meta=meta,
                kind="heading",
            )
        )

    # -- explicit anchor regions: always emit a block for each, even if
    #    it also overlaps a heading section (the anchor wins for
    #    direct addressing).
    for start, end, slug in anchor_regions:
        inner_start = start + 1
        inner_end = end - 1
        if inner_end < inner_start:
            continue
        segment = "\n".join(body_lines[inner_start : inner_end + 1])
        bid = block_id_for(segment)
        meta = {
            "front_matter": fm,
            "anchor_slug": slug,
            "kind": "anchor",
        }
        blocks.append(
            Block(
                id=bid,
                doc_path=rel_path,
                doc_type=doc_type,
                heading_slug=slug,
                heading_level=None,
                line_start=body_start_line + inner_start,
                line_end=body_start_line + inner_end,
                content=segment,
                universal=universal_default,
                token_estimate=estimate_tokens(segment, chars_per_token),
                meta=meta,
                kind="anchor",
            )
        )

    # -- line-range blocks (explicit opt-in in front-matter)
    fm_blocks = fm.get("blocks") or []
    if isinstance(fm_blocks, list):
        for rng in fm_blocks:
            if isinstance(rng, list) and len(rng) == 2:
                a, b = rng
                try:
                    a = int(a)
                    b = int(b)
                except Exception:
                    continue
                # absolute line numbers (1-indexed) in the full doc
                # -- clip to body region
                lo = max(a, body_start_line)
                hi = min(b, body_start_line + len(body_lines) - 1)
                if hi < lo:
                    continue
                a_idx = lo - body_start_line
                b_idx = hi - body_start_line
                segment = "\n".join(body_lines[a_idx : b_idx + 1])
                bid = block_id_for(segment)
                blocks.append(
                    Block(
                        id=bid,
                        doc_path=rel_path,
                        doc_type=doc_type,
                        heading_slug=f"lines-{lo}-{hi}",
                        heading_level=None,
                        line_start=lo,
                        line_end=hi,
                        content=segment,
                        universal=universal_default,
                        token_estimate=estimate_tokens(segment, chars_per_token),
                        meta={
                            "front_matter": fm,
                            "kind": "line-range",
                            "range": [lo, hi],
                        },
                        kind="line-range",
                    )
                )

    # -- whole-document fallback if no headings AND no anchors
    if not blocks and body.strip():
        segment = body.rstrip()
        bid = block_id_for(segment)
        blocks.append(
            Block(
                id=bid,
                doc_path=rel_path,
                doc_type=doc_type,
                heading_slug=None,
                heading_level=None,
                line_start=body_start_line,
                line_end=body_start_line + len(body_lines) - 1,
                content=segment,
                universal=universal_default,
                token_estimate=estimate_tokens(segment, chars_per_token),
                meta={"front_matter": fm, "kind": "whole-document", "defines": defines},
                kind="whole-document",
            )
        )

    return fm, blocks
