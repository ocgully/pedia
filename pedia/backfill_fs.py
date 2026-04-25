"""Filesystem spider for `pedia backfill`.

Walks a project root, classifies markdown / text documentation sources,
and produces `IngestItem`s describing where each one should land inside
`.pedia/`. The spider is deliberately conservative: when unsure, it
prefers `documentation` over a more specific type.

This module knows nothing about writing to `.pedia/` -- it only
discovers + classifies. The writer (`backfill.py`) applies the plan
and handles idempotency + content hashing.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional, Sequence


# ---------------------------------------------------------------------------
# types
# ---------------------------------------------------------------------------


@dataclass
class IngestItem:
    """A single source file slated for ingestion into `.pedia/`.

    - `source_abs`   : absolute path to the source file on disk
    - `source_rel`   : path relative to the project root (for reporting)
    - `doc_type`     : one of the pedia doc types (see DOCTYPES below)
    - `dest_subpath` : path under `.pedia/` where this should land, e.g.
                       `specs/042-canvas/spec.md` or `docs/imported/foo.md`
    - `reason`       : short human-readable classification rationale
    """
    source_abs: Path
    source_rel: str
    doc_type: str
    dest_subpath: str
    reason: str


DOCTYPES = (
    "north-star",
    "vision",
    "constitution",
    "spec",
    "plan",
    "prd",
    "technical-requirement",
    "decision",
    "documentation",
)


# ---------------------------------------------------------------------------
# traversal
# ---------------------------------------------------------------------------


# directories we never descend into (build artifacts, vendor trees, VCS)
_SKIP_DIRS = frozenset({
    ".git", ".hg", ".svn",
    "node_modules", "__pycache__", ".venv", "venv", "env",
    "dist", "build", "target", "out", ".next", ".nuxt",
    ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".idea", ".vscode",
    # don't re-ingest our own output or sibling-tool storage
    ".pedia",
    ".taskflow", ".hopewell",       # taskflow (formerly hopewell)
    ".codeatlas", ".mercator", ".codemap",  # codeatlas (formerly mercator/codemap)
    ".diffsextant", ".sextant",     # diffsextant (formerly sextant)
    # bundle output of build-bundle.sh etc.
    ".claude",
})


def _should_skip_dir(name: str) -> bool:
    if name.startswith(".") and name not in {".specify", ".github"}:
        # skip most dotdirs; allow .specify (SpecKit) and .github (docs live there sometimes)
        return True
    return name in _SKIP_DIRS


def iter_candidate_files(root: Path) -> Iterable[Path]:
    """Walk `root`, yielding absolute paths of candidate markdown files.

    We consider `.md`, `.markdown`, and `.mdx`. Plain `.txt` is skipped
    (too noisy) except for well-known top-level files -- those are
    picked up by name-based heuristics in the classifier.
    """
    root = root.resolve()
    for dirpath, dirnames, filenames in os.walk(root):
        # prune in-place to avoid descending
        dirnames[:] = [d for d in dirnames if not _should_skip_dir(d)]
        for fn in filenames:
            lower = fn.lower()
            if lower.endswith((".md", ".markdown", ".mdx")):
                yield Path(dirpath) / fn


# ---------------------------------------------------------------------------
# classification
# ---------------------------------------------------------------------------


_ADR_SECTION_RE = re.compile(
    r"^\s*##\s+(status|context|decision|consequences)\b",
    re.IGNORECASE | re.MULTILINE,
)
_ADR_NAME_RE = re.compile(r"(?:^|[/\\])(\d{3,4})[-_]", re.MULTILINE)
_SPECS_DIR_RE = re.compile(r"(?:^|[/\\])(specs|\.specify[/\\]specs)[/\\](\d{3,4})[-_]([^/\\]+)[/\\]")
_NORTH_STAR_WORDS = ("north-star", "north_star", "northstar", "vision", "charter", "mission")
_CONSTITUTION_WORDS = ("constitution", "tenet", "tenets", "principles", "principle")
_PRD_WORDS = ("prd", "product-requirements", "product_requirements")
_TR_WORDS = ("technical-requirements", "technical_requirements", "nonfunctional", "non-functional")


def _peek_head(path: Path, n_bytes: int = 4096) -> str:
    try:
        with path.open("rb") as f:
            return f.read(n_bytes).decode("utf-8", errors="replace")
    except OSError:
        return ""


def _slug_from_filename(name: str) -> str:
    stem = Path(name).stem
    slug = re.sub(r"[^A-Za-z0-9]+", "-", stem).strip("-").lower()
    return slug or "doc"


def _slug_from_relpath(rel: str) -> str:
    """Slug that preserves enough path context to avoid collisions.

    `marketplaces/core/CLAUDE.md` -> `marketplaces-core-claude`
    `CLAUDE.md`                   -> `claude`
    """
    # drop the extension
    p = Path(rel.replace("\\", "/"))
    parts = list(p.parts[:-1]) + [p.stem]
    joined = "-".join(parts)
    slug = re.sub(r"[^A-Za-z0-9]+", "-", joined).strip("-").lower()
    return slug or "doc"


def _contains_any(hay: str, needles: Sequence[str]) -> bool:
    low = hay.lower()
    return any(n in low for n in needles)


def classify(root: Path, abs_path: Path) -> IngestItem:
    """Map a source file to a doc_type + dest_subpath under `.pedia/`.

    Path-based signals win over content-based signals (per the plan).
    """
    root = root.resolve()
    rel = abs_path.resolve().relative_to(root).as_posix()
    name = abs_path.name
    low_rel = rel.lower()
    stem_low = Path(name).stem.lower()

    # ---- SpecKit-shaped spec trees: specs/NNN-slug/{spec,plan,prd}.md ----
    m = _SPECS_DIR_RE.search("/" + rel)
    if m:
        nnn = m.group(2)
        slug = m.group(3)
        base_name = stem_low
        dir_slug = f"{nnn}-{slug}"
        # figure out whether we're directly inside specs/NNN-slug/ or nested deeper
        after = rel.split(f"{nnn}-{slug}/", 1)[1].replace("\\", "/") if f"{nnn}-{slug}/" in rel.replace("\\", "/") else ""
        depth_in_spec = after.count("/")  # 0 means directly inside the spec dir

        # when there are multiple spec-NNN-slug across the repo, prefix by the
        # parent path above specs/ (e.g. `marketplaces/gulliver/`) to avoid collisions
        above = rel.replace("\\", "/").split("specs/", 1)[0].rstrip("/")
        prefix = _slug_from_relpath(above) if above else ""
        dir_slug_q = f"{prefix}-{dir_slug}" if prefix else dir_slug

        if depth_in_spec == 0 and base_name == "spec":
            return IngestItem(
                source_abs=abs_path, source_rel=rel,
                doc_type="spec",
                dest_subpath=f"specs/{dir_slug_q}/spec.md",
                reason=f"under specs/{dir_slug}/, spec.md",
            )
        if depth_in_spec == 0 and base_name == "plan":
            return IngestItem(
                source_abs=abs_path, source_rel=rel,
                doc_type="plan",
                dest_subpath=f"specs/{dir_slug_q}/plan.md",
                reason=f"under specs/{dir_slug}/, plan.md",
            )
        if depth_in_spec == 0 and base_name in ("prd", "product"):
            return IngestItem(
                source_abs=abs_path, source_rel=rel,
                doc_type="prd",
                dest_subpath=f"specs/{dir_slug_q}/prd.md",
                reason=f"under specs/{dir_slug}/, prd",
            )
        # any other file under a spec dir (including subdirs): documentation
        return IngestItem(
            source_abs=abs_path, source_rel=rel,
            doc_type="documentation",
            dest_subpath=f"docs/imported/specs-{dir_slug_q}-{_slug_from_relpath(after) or base_name}.md",
            reason=f"under specs/{dir_slug}/{after}",
        )

    # ---- constitution: .specify/memory/constitution.md + top-level tenets ----
    if low_rel.endswith(".specify/memory/constitution.md") or stem_low == "constitution":
        # chapter slug from filename if under constitution/, else a single chapter
        return IngestItem(
            source_abs=abs_path, source_rel=rel,
            doc_type="constitution",
            dest_subpath=f"constitution/{_slug_from_relpath(rel)}.md",
            reason="constitution path/name match",
        )
    if "/constitution/" in ("/" + low_rel) or low_rel.startswith("constitution/"):
        return IngestItem(
            source_abs=abs_path, source_rel=rel,
            doc_type="constitution",
            dest_subpath=f"constitution/{_slug_from_relpath(rel)}.md",
            reason="lives under a constitution/ dir",
        )

    # ---- ADRs / decisions ----
    adr_dir_hit = any(
        seg in ("adr", "adrs", "decisions", "decision-records") for seg in low_rel.split("/")
    )
    adr_name_hit = "adr" in stem_low or bool(_ADR_NAME_RE.search(rel))
    adr_shape = False
    if adr_dir_hit or adr_name_hit:
        head = _peek_head(abs_path)
        adr_shape = bool(_ADR_SECTION_RE.search(head))
    if adr_dir_hit or (adr_name_hit and adr_shape):
        return IngestItem(
            source_abs=abs_path, source_rel=rel,
            doc_type="decision",
            dest_subpath=f"decisions/{_slug_from_relpath(rel)}.md",
            reason=(
                "adr/decisions directory" if adr_dir_hit
                else "adr-shaped filename + sections"
            ),
        )

    # ---- north-star / vision ----
    if _contains_any(stem_low, _NORTH_STAR_WORDS):
        if "vision" in stem_low:
            return IngestItem(
                source_abs=abs_path, source_rel=rel,
                doc_type="vision",
                dest_subpath=f"vision/{_slug_from_filename(name)}.md",
                reason="filename contains 'vision'",
            )
        return IngestItem(
            source_abs=abs_path, source_rel=rel,
            doc_type="north-star",
            dest_subpath=f"north-stars/{_slug_from_filename(name)}.md",
            reason="filename contains north-star/charter/mission",
        )

    # ---- PRDs ----
    if _contains_any(stem_low, _PRD_WORDS):
        return IngestItem(
            source_abs=abs_path, source_rel=rel,
            doc_type="prd",
            dest_subpath=f"prds/{_slug_from_relpath(rel)}.md",
            reason="filename contains prd/product-requirements",
        )

    # ---- Technical requirements ----
    if _contains_any(stem_low, _TR_WORDS) or "nonfunctional" in low_rel.replace("-", ""):
        return IngestItem(
            source_abs=abs_path, source_rel=rel,
            doc_type="technical-requirement",
            dest_subpath=f"technical-requirements/{_slug_from_relpath(rel)}.md",
            reason="filename/path signals technical-requirements",
        )

    # ---- constitution via name-only tenets/principles (after PRD/TR to avoid stealing) ----
    if _contains_any(stem_low, _CONSTITUTION_WORDS):
        return IngestItem(
            source_abs=abs_path, source_rel=rel,
            doc_type="constitution",
            dest_subpath=f"constitution/{_slug_from_relpath(rel)}.md",
            reason="filename contains tenets/principles",
        )

    # ---- well-known top-level docs (at repo root only) ----
    if rel.lower() in {"readme.md", "changelog.md", "contributing.md", "architecture.md"}:
        return IngestItem(
            source_abs=abs_path, source_rel=rel,
            doc_type="documentation",
            dest_subpath=f"docs/imported/{_slug_from_filename(name)}.md",
            reason=f"well-known top-level file: {name}",
        )

    # ---- anything under docs/ preserves its docs/ subpath ----
    if low_rel.startswith("docs/") or "/docs/" in ("/" + low_rel):
        under = rel.split("docs/", 1)[1] if "docs/" in rel else rel
        safe = re.sub(r"[^A-Za-z0-9/_.\-]+", "-", under)
        return IngestItem(
            source_abs=abs_path, source_rel=rel,
            doc_type="documentation",
            dest_subpath=f"docs/imported/{safe}",
            reason="lives under docs/",
        )

    # ---- final fallback: preserve path context to avoid collisions ----
    return IngestItem(
        source_abs=abs_path, source_rel=rel,
        doc_type="documentation",
        dest_subpath=f"docs/imported/{_slug_from_relpath(rel)}.md",
        reason="fallback: generic documentation",
    )


# ---------------------------------------------------------------------------
# high-level entry point
# ---------------------------------------------------------------------------


@dataclass
class FsPlan:
    items: List[IngestItem] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)


def plan_filesystem(root: Path) -> FsPlan:
    """Discover + classify all candidate docs under `root`.

    The returned plan is deterministic (sorted by source_rel) so reports
    read the same way across runs.
    """
    plan = FsPlan()
    for abs_path in iter_candidate_files(root):
        # never ingest files that live inside the pedia output itself
        try:
            rel_posix = abs_path.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            continue
        if rel_posix.startswith(".pedia/") or rel_posix == ".pedia":
            continue
        plan.items.append(classify(root, abs_path))
    plan.items.sort(key=lambda it: it.source_rel)
    return plan


def has_discoverable_sources(root: Path) -> bool:
    """Cheap check used by `pedia init` to decide whether to auto-fire backfill."""
    root = root.resolve()
    for name in ("README.md", "CLAUDE.md", "AGENTS.md", "CHANGELOG.md", "CONTRIBUTING.md", "ARCHITECTURE.md"):
        if (root / name).is_file():
            return True
    for dname in ("docs", "specs", "patterns", "marketplaces", "memory"):
        if (root / dname).is_dir():
            return True
    if (root / ".specify" / "memory" / "constitution.md").is_file():
        return True
    # any top-level markdown file at all
    try:
        for p in root.iterdir():
            if p.is_file() and p.suffix.lower() in (".md", ".markdown", ".mdx"):
                return True
    except OSError:
        pass
    return False
