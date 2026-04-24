"""Core driver for `pedia backfill`.

Takes a project root (and optionally a website seed URL), discovers
source documents, writes them into `.pedia/` with front-matter, and
produces a summary report. Idempotent by content hash.

This module depends on:
  * pedia.backfill_fs  -- filesystem spider
  * pedia.backfill_web -- website crawler
  * pedia.config       -- .pedia/ paths + front-matter helpers
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from pedia import backfill_fs as bfs
from pedia import backfill_web as bweb
from pedia import config as cfg


# ---------------------------------------------------------------------------
# report / state
# ---------------------------------------------------------------------------


@dataclass
class BackfillReport:
    added: int = 0
    updated: int = 0
    unchanged: int = 0
    skipped: int = 0
    web_pages: int = 0
    by_type: Dict[str, int] = field(default_factory=dict)
    unresolved_refs: int = 0
    items: List[Tuple[str, str, str]] = field(default_factory=list)  # (status, dest_rel, reason)

    def total(self) -> int:
        return self.added + self.updated + self.unchanged

    def bump_type(self, t: str) -> None:
        self.by_type[t] = self.by_type.get(t, 0) + 1

    def to_text(self) -> str:
        lines: List[str] = []
        lines.append(
            f"backfill: {self.added} added, {self.updated} updated, "
            f"{self.unchanged} unchanged, {self.skipped} skipped"
        )
        if self.web_pages:
            lines.append(f"  web: {self.web_pages} pages crawled")
        if self.by_type:
            parts = ", ".join(f"{t}={n}" for t, n in sorted(self.by_type.items()))
            lines.append(f"  types: {parts}")
        if self.unresolved_refs:
            lines.append(f"  unresolved references: {self.unresolved_refs}")
        return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# content hashing + front-matter
# ---------------------------------------------------------------------------


_FM_HASH_KEY = "backfill_source_hash"
_FM_SOURCE_KEY = "backfill_source"
_FM_FENCE = "---"
_WIKI_LINK_IN_MD_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def _hash_source(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]


def _existing_hash(dest: Path) -> Optional[str]:
    if not dest.is_file():
        return None
    try:
        text = dest.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    fm, _, _ = cfg.split_front_matter(text)
    h = fm.get(_FM_HASH_KEY)
    return str(h) if h else None


def _wrap_with_front_matter(
    body: str,
    *,
    doc_type: str,
    source_hash: str,
    source_rel: Optional[str] = None,
    extra: Optional[Dict[str, str]] = None,
) -> str:
    """Prepend a front-matter block with the backfill metadata.

    If `body` already starts with a front-matter block, we merge rather
    than duplicate it (preserves upstream `type:` etc, but overwrites
    the backfill keys).
    """
    existing_fm: Dict[str, object] = {}
    rest = body
    if body.startswith(_FM_FENCE):
        existing_fm, rest_text, _ = cfg.split_front_matter(body)
        rest = rest_text
    merged: Dict[str, object] = dict(existing_fm)
    merged.setdefault("type", doc_type)
    merged[_FM_HASH_KEY] = source_hash
    if source_rel:
        merged[_FM_SOURCE_KEY] = source_rel
    if extra:
        for k, v in extra.items():
            merged[k] = v
    fm_text = _emit_front_matter(merged)
    return f"{_FM_FENCE}\n{fm_text}{_FM_FENCE}\n{rest.lstrip(chr(10))}"


def _emit_front_matter(d: Dict[str, object]) -> str:
    """Tiny emitter matching what our yaml-lite parser can round-trip."""
    lines: List[str] = []
    for k, v in d.items():
        if isinstance(v, bool):
            lines.append(f"{k}: {'true' if v else 'false'}")
        elif v is None:
            lines.append(f"{k}:")
        elif isinstance(v, (int, float)):
            lines.append(f"{k}: {v}")
        elif isinstance(v, list):
            inner = ", ".join(_scalar_for_flow(x) for x in v)
            lines.append(f"{k}: [{inner}]")
        elif isinstance(v, dict):
            lines.append(f"{k}:")
            for k2, v2 in v.items():
                lines.append(f"  {k2}: {_scalar_for_flow(v2)}")
        else:
            s = str(v)
            lines.append(f"{k}: {_quote_if_needed(s)}")
    return "\n".join(lines) + "\n"


def _scalar_for_flow(v: object) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if v is None:
        return "null"
    return _quote_if_needed(str(v))


def _quote_if_needed(s: str) -> str:
    if s == "" or re.search(r'[:#\[\]{}]|^\s|\s$', s):
        escaped = s.replace('"', '\\"')
        return f'"{escaped}"'
    return s


# ---------------------------------------------------------------------------
# link rewriting: (relative_md_link) -> [[wiki-link]] when we can resolve
# ---------------------------------------------------------------------------


def _build_link_map(items: List[bfs.IngestItem]) -> Dict[str, str]:
    """Map from source-relative posix paths -> dest posix paths under `.pedia/`.

    Used to rewrite cross-file markdown links in ingested docs to
    `[[wiki-links]]` that pedia can resolve after refresh.
    """
    m: Dict[str, str] = {}
    for it in items:
        # normalize source path with forward slashes
        src = it.source_rel.replace("\\", "/")
        m[src] = it.dest_subpath
        # also register a lowercase + basename key for looser matching
        m[src.lower()] = it.dest_subpath
    return m


def _rewrite_links(
    body: str,
    source_rel: str,
    link_map: Dict[str, str],
    unresolved_acc: List[str],
) -> str:
    """Rewrite relative markdown links like `[x](../specs/042/spec.md)` to
    `[[wiki-link]]` form when the target is a doc we're also ingesting.

    Absolute / http(s) links are left alone. Unresolvable relative
    links are appended to `unresolved_acc` (caller counts them).
    """
    src_dir = Path(source_rel).parent.as_posix()

    def resolve(link: str) -> Optional[str]:
        if link.startswith(("http://", "https://", "mailto:", "#")):
            return None
        # strip fragments for resolution, remember for wiki-link
        path_part, _, frag = link.partition("#")
        if not path_part:
            return None
        # resolve relative to source file
        if src_dir in ("", "."):
            candidate = path_part
        else:
            candidate = (Path(src_dir) / path_part).as_posix()
        # collapse .. / .
        candidate = _normpath(candidate)
        dest = link_map.get(candidate) or link_map.get(candidate.lower())
        if dest is None:
            return None
        # wiki-link as `path#heading` form (resolves via pedia symbols)
        return dest + (f"#{frag}" if frag else "")

    def replacer(m: re.Match) -> str:
        text = m.group(1)
        href = m.group(2).strip()
        wiki = resolve(href)
        if wiki is None:
            if not href.startswith(("http://", "https://", "mailto:", "#")):
                unresolved_acc.append(href)
            return m.group(0)
        # Only emit wiki-link form when we have a heading fragment too.
        # Without a fragment the pedia resolver can't pick a block, and
        # `pedia check` would flag it as unresolved. Keep the plain
        # markdown link in that case -- it still navigates, just isn't
        # a formal wiki-link edge.
        if "#" not in wiki:
            rewritten_path = wiki
            return f"[{text}]({rewritten_path})"
        return f"[[{wiki}|{text}]]"

    return _WIKI_LINK_IN_MD_RE.sub(replacer, body)


def _normpath(p: str) -> str:
    parts = p.split("/")
    out: List[str] = []
    for seg in parts:
        if seg == "" or seg == ".":
            continue
        if seg == "..":
            if out:
                out.pop()
            continue
        out.append(seg)
    return "/".join(out)


# ---------------------------------------------------------------------------
# write (or dry-run) one item
# ---------------------------------------------------------------------------


def _write_item(
    item: bfs.IngestItem,
    root: Path,
    link_map: Dict[str, str],
    unresolved_acc: List[str],
    *,
    dry_run: bool,
    report: BackfillReport,
) -> None:
    src_text = item.source_abs.read_text(encoding="utf-8", errors="replace")
    src_hash = _hash_source(src_text)
    dest_abs = cfg.pedia_dir(root) / item.dest_subpath

    prior = _existing_hash(dest_abs)
    if prior == src_hash and dest_abs.is_file():
        report.unchanged += 1
        report.items.append(("unchanged", item.dest_subpath, item.reason))
        return

    rewritten = _rewrite_links(src_text, item.source_rel, link_map, unresolved_acc)
    wrapped = _wrap_with_front_matter(
        rewritten,
        doc_type=item.doc_type,
        source_hash=src_hash,
        source_rel=item.source_rel,
    )

    if dry_run:
        status = "would-add" if prior is None else "would-update"
        report.items.append((status, item.dest_subpath, item.reason))
        if prior is None:
            report.added += 1
        else:
            report.updated += 1
        report.bump_type(item.doc_type)
        return

    dest_abs.parent.mkdir(parents=True, exist_ok=True)
    dest_abs.write_text(wrapped, encoding="utf-8")
    if prior is None:
        report.added += 1
        report.items.append(("added", item.dest_subpath, item.reason))
    else:
        report.updated += 1
        report.items.append(("updated", item.dest_subpath, item.reason))
    report.bump_type(item.doc_type)


# ---------------------------------------------------------------------------
# web-derived items
# ---------------------------------------------------------------------------


def _write_web_docs(
    docs: List[Tuple[str, str]],
    root: Path,
    *,
    dry_run: bool,
    report: BackfillReport,
) -> None:
    for dest_subpath, body in docs:
        src_hash = _hash_source(body)
        dest_abs = cfg.pedia_dir(root) / dest_subpath
        prior = _existing_hash(dest_abs)
        if prior == src_hash and dest_abs.is_file():
            report.unchanged += 1
            report.items.append(("unchanged", dest_subpath, "web (unchanged)"))
            continue
        wrapped = _wrap_with_front_matter(
            body,
            doc_type="documentation",
            source_hash=src_hash,
            source_rel=None,
        )
        if dry_run:
            status = "would-add" if prior is None else "would-update"
            report.items.append((status, dest_subpath, "web"))
            if prior is None:
                report.added += 1
            else:
                report.updated += 1
            report.bump_type("documentation")
            continue
        dest_abs.parent.mkdir(parents=True, exist_ok=True)
        dest_abs.write_text(wrapped, encoding="utf-8")
        if prior is None:
            report.added += 1
            report.items.append(("added", dest_subpath, "web"))
        else:
            report.updated += 1
            report.items.append(("updated", dest_subpath, "web"))
        report.bump_type("documentation")


# ---------------------------------------------------------------------------
# top-level
# ---------------------------------------------------------------------------


def run_backfill(
    root: Path,
    *,
    source_dir: Optional[Path] = None,
    url: Optional[str] = None,
    depth: int = bweb.DEFAULT_MAX_DEPTH,
    max_pages: int = bweb.DEFAULT_MAX_PAGES,
    timeout_s: int = bweb.DEFAULT_TIMEOUT_S,
    obey_robots: bool = True,
    dry_run: bool = False,
    report_only: bool = False,
    web_opener=None,
) -> BackfillReport:
    """Main entry point. Creates `.pedia/` tree if missing (so a caller
    from `pedia init` can invoke us directly without a separate init).
    """
    root = root.resolve()
    base = cfg.pedia_dir(root)
    base.mkdir(parents=True, exist_ok=True)
    for sub in (
        "north-stars", "vision", "constitution", "specs", "prds",
        "technical-requirements", "decisions", "plans", "docs",
        "docs/imported",
    ):
        (base / sub).mkdir(parents=True, exist_ok=True)

    report = BackfillReport()
    unresolved_acc: List[str] = []

    # -- filesystem phase -----------------------------------------------------
    scan_root = (source_dir or root).resolve()
    fs_plan = bfs.plan_filesystem(scan_root)
    # use source paths relative to the scan root; writes go under the project root's .pedia/
    link_map = _build_link_map(fs_plan.items)

    for it in fs_plan.items:
        if report_only:
            report.items.append(("report-only", it.dest_subpath, it.reason))
            report.bump_type(it.doc_type)
            continue
        _write_item(it, root, link_map, unresolved_acc, dry_run=dry_run, report=report)

    # -- web phase ------------------------------------------------------------
    if url:
        crawl = bweb.crawl(
            url,
            max_depth=depth,
            max_pages=max_pages,
            timeout_s=timeout_s,
            obey_robots=obey_robots,
            opener=web_opener,
        )
        report.web_pages = len(crawl.pages)
        for u, reason in crawl.skipped:
            report.skipped += 1
            report.items.append(("skipped", u, reason))
        docs = bweb.pages_to_markdown_docs(crawl.pages)
        if not report_only:
            _write_web_docs(docs, root, dry_run=dry_run, report=report)
        else:
            for dest_subpath, _body in docs:
                report.items.append(("report-only", dest_subpath, "web"))
                report.bump_type("documentation")

    # -- .claudeignore ensure -------------------------------------------------
    if not dry_run and not report_only:
        _ensure_claudeignore(root)

    report.unresolved_refs = len(unresolved_acc)
    return report


def _ensure_claudeignore(root: Path) -> None:
    """Mirror `pedia init`'s .claudeignore management."""
    block = "# pedia:managed -- agents must query via the `pedia` CLI, never read .pedia/ directly\n.pedia/\n"
    target = root / ".claudeignore"
    if target.exists():
        existing = target.read_text(encoding="utf-8", errors="replace")
        if "pedia:managed" not in existing:
            target.write_text(existing.rstrip() + "\n\n" + block, encoding="utf-8")
    else:
        target.write_text(block, encoding="utf-8")
