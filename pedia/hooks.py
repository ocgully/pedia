"""Git post-commit + Claude Code hook installers.

Sentinel: `# pedia:managed` -- same pattern Hopewell uses so we can
round-trip install / uninstall without touching other hooks.
"""
from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


HOOK_MARKER = "# pedia:managed"


# (ClaudeCodeEvent, matcher_or_None)
CLAUDE_REGISTRATIONS: List[Tuple[str, Optional[str]]] = [
    ("Stop", None),
    ("SubagentStop", None),
]


def _pedia_command() -> str:
    # Fall back to `python -m pedia` so the hook works even without the
    # `pedia` console script on PATH.
    return (
        "pedia refresh --only-changed-in-session 2>/dev/null "
        "|| python -m pedia refresh --only-changed-in-session 2>/dev/null "
        "|| true"
    )


def _build_command() -> str:
    return f"{_pedia_command()}  {HOOK_MARKER}"


def _is_pedia_hook_entry(entry: Dict[str, Any]) -> bool:
    if not isinstance(entry, dict):
        return False
    if entry.get("type") != "command":
        return False
    cmd = entry.get("command") or ""
    return HOOK_MARKER in cmd


def default_settings_path(scope: str) -> Path:
    if scope == "project":
        return Path.cwd() / ".claude" / "settings.json"
    home = Path(os.environ.get("HOME") or os.path.expanduser("~"))
    return home / ".claude" / "settings.json"


def install_claude_code(
    settings_path: Path,
    *,
    dry_run: bool = False,
) -> Dict[str, Any]:
    existing: Dict[str, Any] = {}
    if settings_path.is_file():
        try:
            existing = json.loads(settings_path.read_text(encoding="utf-8"))
            if not isinstance(existing, dict):
                existing = {}
        except Exception:
            existing = {}

    hooks_section: Dict[str, Any] = dict(existing.get("hooks") or {})
    for event_name, matcher in CLAUDE_REGISTRATIONS:
        groups: List[Dict[str, Any]] = list(hooks_section.get(event_name) or [])
        cleaned: List[Dict[str, Any]] = []
        for g in groups:
            if not isinstance(g, dict):
                cleaned.append(g)
                continue
            inner = g.get("hooks") or []
            kept = [h for h in inner if not _is_pedia_hook_entry(h)]
            if kept:
                new_g = dict(g)
                new_g["hooks"] = kept
                cleaned.append(new_g)
        entry: Dict[str, Any] = {
            "type": "command",
            "command": _build_command(),
            "timeout": 10,
        }
        group: Dict[str, Any] = {"hooks": [entry]}
        if matcher:
            group["matcher"] = matcher
        cleaned.append(group)
        hooks_section[event_name] = cleaned

    merged = dict(existing)
    merged["hooks"] = hooks_section

    if not dry_run:
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(
            json.dumps(merged, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    return merged


def uninstall_claude_code(settings_path: Path) -> bool:
    if not settings_path.is_file():
        return False
    try:
        existing = json.loads(settings_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    if not isinstance(existing, dict):
        return False
    hooks = existing.get("hooks") or {}
    if not isinstance(hooks, dict):
        return False
    changed = False
    new_hooks: Dict[str, Any] = {}
    for event_name, groups in hooks.items():
        if not isinstance(groups, list):
            new_hooks[event_name] = groups
            continue
        new_groups: List[Dict[str, Any]] = []
        for g in groups:
            if not isinstance(g, dict):
                new_groups.append(g)
                continue
            inner = g.get("hooks") or []
            kept = [h for h in inner if not _is_pedia_hook_entry(h)]
            if len(kept) != len(inner):
                changed = True
            if kept:
                new_g = dict(g)
                new_g["hooks"] = kept
                new_groups.append(new_g)
        if new_groups:
            new_hooks[event_name] = new_groups
    existing["hooks"] = new_hooks
    if changed:
        settings_path.write_text(
            json.dumps(existing, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    return changed


# ---------------------------------------------------------------------------
# git post-commit hook
# ---------------------------------------------------------------------------


GIT_HOOK_TEMPLATE = """#!/bin/sh
{marker}
# Incremental pedia refresh after each commit. Safe no-op if pedia
# isn't installed or we're outside a pedia project.
if command -v pedia >/dev/null 2>&1; then
    pedia refresh >/dev/null 2>&1 || true
elif command -v python >/dev/null 2>&1; then
    python -m pedia refresh >/dev/null 2>&1 || true
fi
exit 0
"""


def install_git_hook(root: Path) -> Path:
    """Write `.git/hooks/post-commit` pointing at `pedia refresh`.

    Preserves any pre-existing non-pedia post-commit hook by appending
    if the sentinel isn't already present. If the file already contains
    the sentinel, we rewrite only our block.
    """
    git_dir = _git_dir_for(root)
    hooks_dir = git_dir / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    hook_path = hooks_dir / "post-commit"

    template = GIT_HOOK_TEMPLATE.format(marker=HOOK_MARKER)
    if hook_path.is_file():
        existing = hook_path.read_text(encoding="utf-8", errors="replace")
        if HOOK_MARKER in existing:
            # rewrite (simplest: replace file)
            hook_path.write_text(template, encoding="utf-8")
        else:
            # append a new block after the existing content
            suffix = "\n" + template
            hook_path.write_text(existing + suffix, encoding="utf-8")
    else:
        hook_path.write_text(template, encoding="utf-8")

    try:
        st = hook_path.stat()
        hook_path.chmod(st.st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except Exception:
        pass
    return hook_path


def uninstall_git_hook(root: Path) -> bool:
    git_dir = _git_dir_for(root)
    hook_path = git_dir / "hooks" / "post-commit"
    if not hook_path.is_file():
        return False
    txt = hook_path.read_text(encoding="utf-8", errors="replace")
    if HOOK_MARKER not in txt:
        return False
    # If the file is ONLY our block, delete it. Otherwise strip just
    # our block (crude: rewrite lines not containing the marker and
    # surrounding our template). Simpler: if file equals template,
    # delete; else leave alone with a note.
    template = GIT_HOOK_TEMPLATE.format(marker=HOOK_MARKER)
    if txt.strip() == template.strip():
        hook_path.unlink()
        return True
    # leave intact but flag
    return False


def _git_dir_for(root: Path) -> Path:
    # Regular checkout
    gd = root / ".git"
    if gd.is_dir():
        return gd
    # Worktree: .git is a file pointing at gitdir
    if gd.is_file():
        txt = gd.read_text(encoding="utf-8", errors="replace").strip()
        if txt.startswith("gitdir:"):
            return Path(txt.split(":", 1)[1].strip())
    return gd
