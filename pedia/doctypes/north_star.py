"""North-star validator. Should be short, flag as universal."""
from __future__ import annotations

from typing import Any, Dict, List, Tuple


def validate(
    doc_path: str,
    front_matter: Dict[str, Any],
    blocks: List[Any],
) -> List[Tuple[str, str]]:
    findings: List[Tuple[str, str]] = []
    if str(front_matter.get("type", "")).lower() != "north-star":
        findings.append(("warning", f"{doc_path}: missing 'type: north-star' in front-matter"))
    if not front_matter.get("universal_context") and not front_matter.get("universal"):
        findings.append(
            ("warning", f"{doc_path}: north-stars should typically set 'universal_context: true'")
        )
    total_len = sum(len(getattr(b, "content", "") or "") for b in blocks)
    if total_len > 4000:
        findings.append(
            ("warning", f"{doc_path}: north-star is long ({total_len} chars); keep it pithy")
        )
    return findings
