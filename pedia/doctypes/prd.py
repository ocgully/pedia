"""PRD validator."""
from __future__ import annotations

from typing import Any, Dict, List, Tuple


RECOMMENDED = ("goals", "non-goals", "success-metrics")


def validate(
    doc_path: str,
    front_matter: Dict[str, Any],
    blocks: List[Any],
) -> List[Tuple[str, str]]:
    findings: List[Tuple[str, str]] = []
    if str(front_matter.get("type", "")).lower() != "prd":
        findings.append(("warning", f"{doc_path}: missing 'type: prd' in front-matter"))
    slugs = {(getattr(b, "heading_slug", None) or "").lower() for b in blocks}
    for req in RECOMMENDED:
        if req not in slugs:
            findings.append(
                ("warning", f"{doc_path}: prd missing recommended section '{req}'")
            )
    return findings
