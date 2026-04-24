"""Minimal configuration loader for .pedia/config.yaml.

We read a tiny, deliberately-restricted subset of YAML (flat keys +
nested one-level maps + sequences of scalars). This avoids pulling in
PyYAML and keeps the runtime-deps surface at zero.

For anything more complex, users can keep complex config in JSON
(which the stdlib handles natively) -- `config.yaml` is preferred for
human readability.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


PEDIA_DIRNAME = ".pedia"
CONFIG_FILENAME = "config.yaml"
DB_FILENAME = "index.sqlite"


def find_pedia_root(start: Optional[Path] = None) -> Optional[Path]:
    """Walk upward from `start` (default CWD) looking for a `.pedia/` directory."""
    cur = (start or Path.cwd()).resolve()
    while True:
        candidate = cur / PEDIA_DIRNAME
        if candidate.is_dir():
            return cur
        if cur.parent == cur:
            return None
        cur = cur.parent


def pedia_dir(root: Path) -> Path:
    return root / PEDIA_DIRNAME


def db_path(root: Path) -> Path:
    return pedia_dir(root) / DB_FILENAME


def config_path(root: Path) -> Path:
    return pedia_dir(root) / CONFIG_FILENAME


# ---------------------------------------------------------------------------
# tiny YAML reader (flat scalars + one-level nested maps + list of scalars)
# ---------------------------------------------------------------------------


def _strip_inline_comment(s: str) -> str:
    # respect quoted strings lightly (we don't support multiline quoting)
    in_single = in_double = False
    for i, ch in enumerate(s):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double:
            return s[:i].rstrip()
    return s.rstrip()


def _parse_scalar(raw: str) -> Any:
    s = raw.strip()
    if s == "" or s.lower() in ("null", "~"):
        return None
    if s.lower() == "true":
        return True
    if s.lower() == "false":
        return False
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        return s[1:-1]
    # numbers
    try:
        if "." in s or "e" in s or "E" in s:
            return float(s)
        return int(s)
    except ValueError:
        return s


def _indent_of(line: str) -> int:
    i = 0
    while i < len(line) and line[i] == " ":
        i += 1
    return i


def load_yaml_lite(text: str) -> Dict[str, Any]:
    """Parse a restricted YAML document into a dict.

    Supported:
      key: scalar
      key: [inline, list, of, scalars]
      key:
        subkey: scalar
        subkey2: scalar
      key:
        - item1
        - item2
    """
    lines = text.splitlines()
    result: Dict[str, Any] = {}
    i = 0
    while i < len(lines):
        raw = lines[i]
        stripped = _strip_inline_comment(raw)
        if not stripped.strip() or stripped.strip().startswith("#"):
            i += 1
            continue
        indent = _indent_of(stripped)
        if indent != 0:
            # shouldn't hit the top level here
            i += 1
            continue
        if ":" not in stripped:
            i += 1
            continue
        key, _, rest = stripped.partition(":")
        key = key.strip()
        rest = rest.strip()
        if rest:
            # inline value
            if rest.startswith("[") and rest.endswith("]"):
                inner = rest[1:-1].strip()
                if inner == "":
                    result[key] = []
                else:
                    result[key] = [_parse_scalar(p) for p in _split_flow_list(inner)]
            else:
                result[key] = _parse_scalar(rest)
            i += 1
            continue
        # block value: look ahead
        j = i + 1
        block_lines: List[str] = []
        while j < len(lines):
            nxt = lines[j]
            if not nxt.strip() or nxt.strip().startswith("#"):
                j += 1
                continue
            if _indent_of(nxt) == 0:
                break
            block_lines.append(nxt)
            j += 1
        if block_lines and block_lines[0].lstrip().startswith("- "):
            # list of scalars
            items: List[Any] = []
            for bl in block_lines:
                s = bl.strip()
                if s.startswith("- "):
                    items.append(_parse_scalar(s[2:]))
            result[key] = items
        else:
            # nested map
            sub: Dict[str, Any] = {}
            # determine inner indent
            if block_lines:
                inner_indent = _indent_of(block_lines[0])
                for bl in block_lines:
                    s = _strip_inline_comment(bl)
                    if _indent_of(s) < inner_indent:
                        continue
                    body = s[inner_indent:]
                    if ":" not in body:
                        continue
                    k, _, v = body.partition(":")
                    k = k.strip()
                    v = v.strip()
                    if v == "":
                        sub[k] = None
                    elif v.startswith("[") and v.endswith("]"):
                        inner = v[1:-1].strip()
                        if inner == "":
                            sub[k] = []
                        else:
                            sub[k] = [_parse_scalar(p) for p in _split_flow_list(inner)]
                    else:
                        sub[k] = _parse_scalar(v)
            result[key] = sub
        i = j
    return result


def _split_flow_list(inner: str) -> List[str]:
    parts: List[str] = []
    buf: List[str] = []
    depth = 0
    in_single = in_double = False
    for ch in inner:
        if ch == "'" and not in_double:
            in_single = not in_single
            buf.append(ch)
        elif ch == '"' and not in_single:
            in_double = not in_double
            buf.append(ch)
        elif ch in "[{" and not in_single and not in_double:
            depth += 1
            buf.append(ch)
        elif ch in "]}" and not in_single and not in_double:
            depth -= 1
            buf.append(ch)
        elif ch == "," and depth == 0 and not in_single and not in_double:
            parts.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    tail = "".join(buf).strip()
    if tail:
        parts.append(tail)
    return parts


# ---------------------------------------------------------------------------
# front-matter extraction
# ---------------------------------------------------------------------------


def split_front_matter(text: str) -> Tuple[Dict[str, Any], str, int]:
    """Return (front_matter_dict, body_text, body_first_line_number).

    `body_first_line_number` is 1-indexed; it's the line in the ORIGINAL
    text on which the body starts (useful for line-range mapping).
    """
    if not text.startswith("---"):
        return {}, text, 1
    lines = text.splitlines(keepends=True)
    if not lines or not lines[0].startswith("---"):
        return {}, text, 1
    # find closing `---`
    end_idx = None
    for i in range(1, len(lines)):
        if lines[i].rstrip("\r\n") == "---":
            end_idx = i
            break
    if end_idx is None:
        return {}, text, 1
    fm_text = "".join(lines[1:end_idx])
    body = "".join(lines[end_idx + 1:])
    body_line = end_idx + 2  # 1-indexed
    try:
        fm = load_yaml_lite(fm_text)
    except Exception:
        fm = {}
    return fm, body, body_line


# ---------------------------------------------------------------------------
# project config
# ---------------------------------------------------------------------------


DEFAULT_CONFIG = {
    "version": 1,
    "token_approx_chars_per_token": 4,
    "query": {
        "default_limit": 10,
        "default_token_budget": 2000,
        "universal_reserve": 400,
    },
}


def load_project_config(root: Path) -> Dict[str, Any]:
    p = config_path(root)
    if not p.is_file():
        return dict(DEFAULT_CONFIG)
    text = p.read_text(encoding="utf-8")
    try:
        parsed = load_yaml_lite(text)
    except Exception:
        parsed = {}
    merged = dict(DEFAULT_CONFIG)
    for k, v in parsed.items():
        merged[k] = v
    return merged


def dump_config_yaml(cfg: Dict[str, Any]) -> str:
    """Very small YAML emitter for the default config file."""
    out: List[str] = []
    for k, v in cfg.items():
        if isinstance(v, dict):
            out.append(f"{k}:")
            for k2, v2 in v.items():
                out.append(f"  {k2}: {json.dumps(v2)}")
        else:
            out.append(f"{k}: {json.dumps(v)}")
    return "\n".join(out) + "\n"
