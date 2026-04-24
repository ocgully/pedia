---
type: decision
id: 0003-stdlib-only-runtime
status: accepted
date: 2026-04-24
defines: [Stdlib-Only Runtime Decision]
---

# ADR 0003 — Stdlib-only runtime

## Status

Accepted.

## Context

Pedia targets developer laptops, CI workers, and air-gapped servers.
It is one of three sibling tools (mercator, hopewell, pedia) sharing
an install posture. Every runtime dependency is a long-term tax —
security advisories, version pinning, install latency, offline
breakage.

The question: should Pedia's default install pull dependencies
(PyYAML, markdown libraries, FTS bindings, etc.) the way most Python
tools do?

## Decision

Pedia ships with **zero runtime dependencies** beyond Python 3.10+
stdlib.

Required modules: `sqlite3`, `urllib`, `html.parser`, `argparse`,
`hashlib`, `json`, `http.server`, `pathlib`, `datetime`, `re`.
Anything beyond this list is hand-rolled or shipped as an opt-in
extras.

## Rationale

Install simplicity. `pip install pedia` works behind a corporate
proxy, on an air-gapped runner, or on a fresh container with no
network — anywhere Python itself works.

Long-term stability. PyYAML (the most likely candidate) has had
security advisories that forced cascading version bumps in dependent
projects. Pedia avoids that cost entirely.

Offline-first. No PyPI hits at runtime. `pedia` invocations on a
plane behave the same as on a desk. See [[Offline-First]].

Predictable behavior. No accidentally-loaded plugin modules. No
"works on my machine" caused by a different version of a transitive
dep. The runtime surface is exactly what stdlib provides.

## Alternatives considered

**Allow PyYAML as a default dep.** Rejected — the YAML we parse
(front-matter) is a tiny subset. Hand-rolled is ~150 lines, well-
tested, and removes a long-term maintenance liability.

**Allow `marked` or `mistune` for markdown rendering.** Rejected for
the CLI path (we don't render markdown there). The web view ships
markdown rendering via esm.sh in the browser, so the *server* still
has zero deps; the browser pulls `marked` from a CDN at view time.

**Optional but auto-installed deps.** Rejected — "auto-installed
optional" is just "required" with extra steps. If a feature needs a
dep, it's explicit-opt-in via extras (`pip install pedia[embed]` for
phase-5 embeddings).

## Consequences

Positive:

- One-line install on any machine with Python.
- Works offline, in CI, in containers, behind corporate proxies.
- No CVE chase for transitive deps.
- Smaller attack surface.

Negative:

- Some features cost more engineering to build (YAML parsing,
  HTML→markdown conversion, robots.txt parsing). We pay it once.
- Niche features (vector embeddings, advanced markdown rendering)
  must live behind extras. Cleaner anyway — the default code path
  stays predictable.

## Where this binds

The constraint is captured constitutionally in [[Stdlib-Only Runtime]]
in [[constitution/technical.md]]. Every `pyproject.toml` change goes
through ADR review if it adds a runtime dep.

## Supersedes / superseded by

None.
