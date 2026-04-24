"""Smoke tests for pedia.

These exercise the major pieces end-to-end in a scratch directory.
Tests are deliberately loose -- phase 1 is about having the shape
right, not about property-based coverage.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture
def scratch(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "pedia", *args],
        capture_output=True,
        text=True,
        check=False,
    )


def test_version_via_module():
    proc = _run("--version")
    assert proc.returncode == 0
    assert "pedia" in proc.stdout.lower() or "pedia" in proc.stderr.lower()


def test_init_refresh_query(scratch: Path):
    proc = _run("init", "--with-examples")
    assert proc.returncode == 0, proc.stderr
    assert (scratch / ".pedia").is_dir()
    assert (scratch / ".claudeignore").is_file()

    proc = _run("refresh")
    assert proc.returncode == 0, proc.stderr

    proc = _run("query", "flow network")
    assert proc.returncode == 0
    assert "universal context" in proc.stdout
    assert "matches" in proc.stdout

    proc = _run("query", "flow network", "--format", "json")
    assert proc.returncode == 0
    import json
    data = json.loads(proc.stdout)
    assert "universal" in data and "matches" in data and "see_also" in data


def test_symbol_linking(scratch: Path):
    _run("init", "--with-examples")
    _run("refresh")
    # The example spec already contains [[decision:0001-content-hash-block-ids]]
    # and the example constitution defines "Determinism Over Magic".
    proc = _run("check")
    assert proc.returncode in (0, 1)  # warnings allowed, errors fail
    # Add a new wiki link + refresh
    spec = scratch / ".pedia" / "specs" / "001-example" / "spec.md"
    existing = spec.read_text(encoding="utf-8")
    # The spec already uses [[decision:0001-content-hash-block-ids]]
    # Add a [[Term]] reference that the constitution defines
    appended = existing + "\n## See also\n\nThis depends on [[Determinism Over Magic]].\n"
    spec.write_text(appended, encoding="utf-8")
    proc = _run("refresh")
    assert proc.returncode == 0
    proc = _run("query", "determinism")
    assert proc.returncode == 0


def test_hooks_install_dry(scratch: Path, tmp_path: Path):
    _run("init")
    fake = tmp_path / "fake_settings.json"
    proc = _run(
        "hooks", "install", "--claude-code", "--settings-path", str(fake),
    )
    assert proc.returncode == 0
    assert fake.is_file()
    txt = fake.read_text(encoding="utf-8")
    assert "pedia:managed" in txt
    assert "Stop" in txt and "SubagentStop" in txt
