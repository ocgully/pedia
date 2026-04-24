"""Documentation / vision / catch-all validator. Permissive."""
from __future__ import annotations

from typing import Any, Dict, List, Tuple


def validate(
    doc_path: str,
    front_matter: Dict[str, Any],
    blocks: List[Any],
) -> List[Tuple[str, str]]:
    findings: List[Tuple[str, str]] = []
    if not blocks:
        findings.append(("warning", f"{doc_path}: document has no content blocks"))
    return findings
