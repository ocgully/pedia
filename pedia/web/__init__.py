"""pedia.web -- read-only local web UI (HW-0046).

`pedia web` starts a stdlib `http.server`-based site that renders the
Pedia knowledge base with wiki-link navigation, bidirectional impact
panels, search, and an interactive dep/prov graph via React Flow.

Read-only by design: every endpoint is GET. All mutations still go
through the `pedia` CLI or direct markdown edits. No auth, no
multi-user editing, no external content fetching (external systems are
surfaced as clickable URLs only).

Stack mirrors the TaskFlow canvas (TF-0042):

  * stdlib http.server + sqlite3 + json on the server
  * Preact@10.22.0 via esm.sh, no bundler
  * marked@12 for markdown (wiki-links pre-processed)
  * @xyflow/react@12.3.6 + elkjs@0.9.3 for the graph view, with
    `alias=react:preact/compat,react-dom:preact/compat` so the same
    preact instance powers both the app and React Flow.
"""

from __future__ import annotations

__all__ = ["server"]
