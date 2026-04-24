"""Doc-type schemas + validators.

Each module exports a `validate(doc_path, front_matter, blocks)` function
that returns a list of `(severity, message)` tuples. Severity is
`'error'` or `'warning'`. Empty list = OK.
"""
from __future__ import annotations

from typing import Callable, Dict, List, Tuple

from pedia.doctypes import (
    constitution as _constitution,
    decision as _decision,
    documentation as _documentation,
    north_star as _north_star,
    plan as _plan,
    prd as _prd,
    spec as _spec,
    technical_requirement as _tr,
)

Finding = Tuple[str, str]


VALIDATORS: Dict[str, Callable[..., List[Finding]]] = {
    "spec": _spec.validate,
    "decision": _decision.validate,
    "north-star": _north_star.validate,
    "constitution": _constitution.validate,
    "prd": _prd.validate,
    "technical-requirement": _tr.validate,
    "tr": _tr.validate,
    "plan": _plan.validate,
    "documentation": _documentation.validate,
    "vision": _documentation.validate,  # vision reuses the permissive schema
}


def validator_for(doc_type: str) -> Callable[..., List[Finding]]:
    return VALIDATORS.get(doc_type, _documentation.validate)
