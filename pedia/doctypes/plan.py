"""Plan validator."""
from __future__ import annotations

from typing import Any, Dict, List, Tuple


def validate(
    doc_path: str,
    front_matter: Dict[str, Any],
    blocks: List[Any],
) -> List[Tuple[str, str]]:
    findings: List[Tuple[str, str]] = []
    if str(front_matter.get("type", "")).lower() != "plan":
        findings.append(("warning", f"{doc_path}: missing 'type: plan' in front-matter"))
    return findings
