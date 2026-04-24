"""Decision (ADR) validator."""
from __future__ import annotations

from typing import Any, Dict, List, Tuple


REQUIRED_SECTIONS = ("context", "decision", "consequences")


def validate(
    doc_path: str,
    front_matter: Dict[str, Any],
    blocks: List[Any],
) -> List[Tuple[str, str]]:
    findings: List[Tuple[str, str]] = []
    if str(front_matter.get("type", "")).lower() != "decision":
        findings.append(("warning", f"{doc_path}: missing 'type: decision' in front-matter"))
    slugs = {(getattr(b, "heading_slug", None) or "").lower() for b in blocks}
    for req in REQUIRED_SECTIONS:
        if req not in slugs:
            findings.append(
                ("warning", f"{doc_path}: decision missing recommended section '{req}'")
            )
    return findings
