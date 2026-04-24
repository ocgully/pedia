// Search view. Hits /api/query (which re-uses pedia.query.run_query —
// same FTS5 path the CLI uses, same universal + matches + see_also
// shape).

import { h, Fragment } from "https://esm.sh/preact@10.22.0";
import { useState, useEffect } from "https://esm.sh/preact@10.22.0/hooks";

export function SearchView({ q }) {
  const [state, setState] = useState({ loading: true });

  useEffect(() => {
    if (!q) { setState({ loading: false, data: null }); return; }
    setState({ loading: true });
    fetch("/api/query?q=" + encodeURIComponent(q) + "&limit=20&budget=4000")
      .then(r => r.ok ? r.json() : r.json().then(j => Promise.reject(j.error || r.statusText)))
      .then(data => setState({ loading: false, data }))
      .catch(e => setState({ loading: false, error: String(e) }));
  }, [q]);

  if (!q) {
    return h("div", { class: "empty" }, "Type a query in the search bar above.");
  }
  if (state.loading) return h("div", { class: "empty" }, `Searching for "${q}"...`);
  if (state.error)   return h("div", { class: "error" }, state.error);

  const d = state.data;
  return h("div", { class: "search-results" },
    d.universal && d.universal.length > 0
      ? h(Fragment, {},
          h("div", { class: "sr-section-hdr" }, "Universal context (always)"),
          d.universal.map((r) => h(ResultItem, { key: "u:" + r.id, r }))
        )
      : null,
    h("div", { class: "sr-section-hdr" },
      `Matches for "${q}" — ${d.matches.length} (budget ${d.tokens_used}/${d.token_budget} tokens)`
    ),
    d.matches.length === 0
      ? h("div", { class: "empty" }, "No matches.")
      : d.matches.map((r) => h(ResultItem, { key: "m:" + r.id, r })),
    d.see_also && d.see_also.length > 0
      ? h(Fragment, {},
          h("div", { class: "sr-section-hdr" }, "See also"),
          d.see_also.map((r) => h(ResultItem, { key: "s:" + r.id, r, seeAlso: true }))
        )
      : null
  );
}

function ResultItem({ r, seeAlso }) {
  const title = r.heading_title || r.heading_slug || "(body)";
  return h("div", { class: "sr-item" },
    h("div", { class: "sr-head" },
      h("a", { href: "#/block/" + r.id }, title),
      h("span", {}, r.doc_type),
      h("span", { class: "muted" }, r.doc_path),
      r.score != null ? h("span", { class: "muted" }, `score ${r.score}`) : null,
      seeAlso && r.relation ? h("span", { class: "muted" }, r.relation) : null
    ),
    h("div", { class: "sr-body" }, r.content || "")
  );
}
