// Block-level deep link view + thread-of-impact trace view.

import { h, Fragment } from "https://esm.sh/preact@10.22.0";
import { useState, useEffect } from "https://esm.sh/preact@10.22.0/hooks";

import { RefsPanel } from "/static/doc.js";

export function BlockView({ id }) {
  const [state, setState] = useState({ loading: true });

  useEffect(() => {
    setState({ loading: true });
    fetch("/api/block/" + encodeURIComponent(id))
      .then(r => r.ok ? r.json() : r.json().then(j => Promise.reject(j.error || r.statusText)))
      .then(data => setState({ loading: false, data }))
      .catch(e => setState({ loading: false, error: String(e) }));
  }, [id]);

  if (state.loading) return h("div", { class: "empty" }, `Loading block ${id}...`);
  if (state.error)   return h("div", { class: "error" }, state.error);
  const b = state.data;

  return h("div", { class: "block-view" },
    h("div", { class: "block-body" },
      h("div", { class: "crumbs" },
        h("a", { href: "#/" }, "TOC"),
        " / ",
        h("a", { href: "#/doc/" + encodeURIComponent(b.doc_path) }, b.doc_path),
        " / ",
        h("code", {}, b.id)
      ),
      h("h2", {}, b.heading_title || b.heading_slug || "(body)"),
      h("div", { class: "muted", style: "font-size:11px;margin-bottom:8px" },
        `${b.doc_type} @ lines ${b.line_start}-${b.line_end}`,
        b.universal ? " • universal-context" : ""
      ),
      h("pre", { style: "white-space:pre-wrap;background:var(--bg-3);padding:10px;border-radius:6px" }, b.content),
      h("div", { style: "margin-top:12px" },
        h("a", { href: "#/graph/" + b.id }, "View in graph"),
        " • ",
        h("a", { href: "#/trace/" + b.id }, "Thread of impact")
      )
    ),
    h("aside", { class: "doc-side" },
      h(RefsPanel, { title: "This cites", refs: b.refs_out }),
      h(RefsPanel, { title: "Cited by", refs: b.refs_in }),
      (b.unresolved_wiki_links || []).length > 0
        ? h("div", { class: "panel" },
            h("h3", {}, "Unresolved wiki links"),
            h("ul", {}, b.unresolved_wiki_links.map((u, i) =>
              h("li", { key: i },
                h("code", {}, `[[${u.raw}]]`),
                " ",
                h("span", { class: "muted" }, `(${u.form})`)
              )
            ))
          )
        : null
    )
  );
}

// Thread of impact: two collapsible trees (upstream + downstream). Uses
// /api/trace which wraps pedia.trace.walk — the same CLI code path.
export function TraceView({ id }) {
  const [state, setState] = useState({ loading: true });

  useEffect(() => {
    setState({ loading: true });
    fetch("/api/trace?block=" + encodeURIComponent(id) + "&depth=5")
      .then(r => r.ok ? r.json() : r.json().then(j => Promise.reject(j.error || r.statusText)))
      .then(data => setState({ loading: false, data }))
      .catch(e => setState({ loading: false, error: String(e) }));
  }, [id]);

  if (state.loading) return h("div", { class: "empty" }, `Tracing ${id}...`);
  if (state.error)   return h("div", { class: "error" }, state.error);
  const t = state.data;

  return h(Fragment, {},
    h("div", { class: "crumbs" },
      h("a", { href: "#/" }, "TOC"),
      " / ",
      h("a", { href: "#/block/" + t.anchor }, t.anchor),
      " / trace"
    ),
    h("div", { class: "trace-wrap" },
      h("div", { class: "trace-col" },
        h("h3", {}, `Upstream (what led to this) — ${t.up.length}`),
        t.up.map((r) =>
          h("div", {
              class: "trace-row",
              style: "padding-left:" + (r.depth * 14) + "px",
              key: r.id + ":u:" + r.depth,
            },
            r.via !== "self" ? h("span", { class: "via" }, r.via) : null,
            h("a", { href: "#/block/" + r.id }, r.doc_path),
            r.heading_slug ? h("span", { class: "muted" }, ` @ ${r.heading_slug}`) : null
          )
        )
      ),
      h("div", { class: "trace-col" },
        h("h3", {}, `Downstream (what cites this) — ${t.down.length}`),
        t.down.map((r) =>
          h("div", {
              class: "trace-row",
              style: "padding-left:" + (r.depth * 14) + "px",
              key: r.id + ":d:" + r.depth,
            },
            r.via !== "self" ? h("span", { class: "via" }, r.via) : null,
            h("a", { href: "#/block/" + r.id }, r.doc_path),
            r.heading_slug ? h("span", { class: "muted" }, ` @ ${r.heading_slug}`) : null
          )
        )
      )
    )
  );
}
