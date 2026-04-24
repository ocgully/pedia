// TOC / landing page. Hits /api/toc and renders one card per doc-type
// group (north-star, vision, constitution, spec, ...).

import { h, Fragment } from "https://esm.sh/preact@10.22.0";
import { useState, useEffect } from "https://esm.sh/preact@10.22.0/hooks";

const TYPE_LABELS = {
  "north-star": "North Stars",
  "vision": "Vision",
  "constitution": "Constitution",
  "spec": "Specs",
  "prd": "PRDs",
  "technical-requirement": "Technical Requirements",
  "plan": "Plans",
  "decision": "Decisions (ADRs)",
  "documentation": "Documentation",
};

export function TocView() {
  const [state, setState] = useState({ loading: true });

  useEffect(() => {
    fetch("/api/toc")
      .then((r) => r.json())
      .then((data) => setState({ loading: false, data }))
      .catch((e) => setState({ loading: false, error: String(e) }));
  }, []);

  if (state.loading) return h("div", { class: "empty" }, "Loading index...");
  if (state.error)   return h("div", { class: "error" }, state.error);
  const data = state.data;

  const ordered = Object.keys(TYPE_LABELS).filter(
    (t) => (data.groups[t] || []).length > 0
  );
  // Any extra types the server knows about but TYPE_LABELS doesn't.
  Object.keys(data.groups || {}).forEach((t) => {
    if (!ordered.includes(t) && (data.groups[t] || []).length > 0) ordered.push(t);
  });

  if (ordered.length === 0) {
    return h("div", { class: "empty" },
      "No docs indexed yet. Run ",
      h("code", {}, "pedia init --with-examples"),
      " then ",
      h("code", {}, "pedia refresh"),
      "."
    );
  }

  return h("div", { class: "toc" },
    ordered.map((t) =>
      h("div", { class: `toc-group dt-${t}`, key: t },
        h("h2", {},
          TYPE_LABELS[t] || t,
          h("span", { class: "count" }, `(${data.groups[t].length})`)
        ),
        h("ul", {},
          data.groups[t].map((d) =>
            h("li", { key: d.path },
              h("a", { href: "#/doc/" + encodeURIComponent(d.path) }, d.title),
              h("span", { class: "path" }, d.path)
            )
          )
        )
      )
    )
  );
}
