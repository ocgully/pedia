---
description: Query Pedia for spec/decision context relevant to the user's request
---

If the user's request mentions a TaskFlow node id (e.g. `TF-0042`), run `pedia show --for TF-0042` and surface the cited specs and decisions. Otherwise, run `pedia query "<keywords from the user's request>"` and report the top hits with their block ids. Then propose: continue with the cited context, or author a new spec/decision via `@pedia-keeper`.

This is a read-only command. To author content, invoke `@pedia-keeper` directly or route through `@orchestrator`.
