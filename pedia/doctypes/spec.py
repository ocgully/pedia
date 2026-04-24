"""Spec validator. Requires `type: spec` in front-matter and a top-level H1."""
from __future__ import annotations

from typing import Any, Dict, List, Tuple


def validate(
    doc_path: str,
    front_matter: Dict[str, Any],
    blocks: List[Any],
) -> List[Tuple[str, str]]:
    findings: List[Tuple[str, str]] = []
    if str(front_matter.get("type", "")).lower() != "spec":
        findings.append(("warning", f"{doc_path}: missing 'type: spec' in front-matter"))
    if not any(getattr(b, "heading_level", None) == 1 for b in blocks):
        findings.append(("warning", f"{doc_path}: spec should have a single top-level H1"))
    return findings
