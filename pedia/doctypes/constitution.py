"""Constitution-chapter validator."""
from __future__ import annotations

from typing import Any, Dict, List, Tuple


def validate(
    doc_path: str,
    front_matter: Dict[str, Any],
    blocks: List[Any],
) -> List[Tuple[str, str]]:
    findings: List[Tuple[str, str]] = []
    if str(front_matter.get("type", "")).lower() != "constitution":
        findings.append(("warning", f"{doc_path}: missing 'type: constitution' in front-matter"))
    if not front_matter.get("universal_context") and not front_matter.get("universal"):
        findings.append(
            ("warning", f"{doc_path}: constitution chapters should typically be universal")
        )
    return findings
