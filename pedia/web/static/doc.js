// Doc view: markdown body + right-side panel of incoming/outgoing refs +
// external-system deep links. Uses `marked` from esm.sh for markdown
// rendering, with a [[wiki-link]] pre-processor that resolves against
// /api/doc's block index (so we can distinguish resolved from dangling
// links and style them accordingly).

import { h, Fragment } from "https://esm.sh/preact@10.22.0";
import { useState, useEffect, useMemo, useCallback } from "https://esm.sh/preact@10.22.0/hooks";

const markedMod = await import("https://esm.sh/marked@12");
const marked = markedMod.marked || markedMod.default;
marked.use({
  breaks: false,
  gfm: true,
});

// [[target|display]] or [[target]]
const WIKI_LINK_RE = /\[\[([^\[\]\|]+?)(?:\|([^\[\]]+?))?\]\]/g;

function classifyWikiLink(target) {
  const t = target.trim();
  if (t.startsWith("block:")) return "block-id";
  if (t.includes("#") && !t.startsWith("#")) {
    const head = t.split("#", 1)[0];
    if (head.includes("/") || head.endsWith(".md")) return "path-heading";
  }
  if (t.includes(":") && !t.split(":", 1)[0].includes("/")) return "type-slug";
  return "term";
}

// Produce an HREF for a [[...]] target, or null when we can't route client-side
// (e.g. a bare term we have no symbol map for — the server's `refs` tell us
// what resolved; unresolved ones get styled as dangling).
function wikiLinkHref(target, doc) {
  const t = target.trim();
  const form = classifyWikiLink(t);
  if (form === "block-id") {
    return "#/block/" + encodeURIComponent(t.slice(6).trim());
  }
  if (form === "path-heading") {
    const [path, heading] = t.split("#", 2);
    // Link to the doc view; the client scrolls to the heading anchor.
    return "#/doc/" + encodeURIComponent(path.trim()) + "?h=" + encodeURIComponent(heading.trim());
  }
  if (form === "type-slug") {
    const [typ, slug] = t.split(":", 2);
    const type = typ.trim().toLowerCase();
    const s = slug.trim();
    // Same convention as pedia.symbols.resolve_wiki_link:
    //   spec -> specs/<slug>/spec.md; others -> <typeplural>/<slug>.md
    const TYPE_DIR = {
      "spec": "specs",
      "decision": "decisions",
      "north-star": "north-stars",
      "constitution": "constitution",
      "prd": "prds",
      "technical-requirement": "technical-requirements",
      "tr": "technical-requirements",
      "plan": "plans",
      "documentation": "docs",
      "vision": "vision",
    };
    const dir = TYPE_DIR[type];
    if (!dir) return null;
    const path = type === "spec" ? `specs/${s}/spec.md` : `${dir}/${s}.md`;
    return "#/doc/" + encodeURIComponent(path);
  }
  // Bare term: use /api/query to jump to the best match.
  return "#/search?q=" + encodeURIComponent(t);
}

// Pre-process wiki links into HTML <a> tags BEFORE handing off to marked.
// That way `marked` tokenizes the anchor as html-inline and our link
// survives intact, including the classes we attach for styling.
function preprocessWikiLinks(md, resolvedSet, doc) {
  return md.replace(WIKI_LINK_RE, (m, target, display) => {
    const href = wikiLinkHref(target, doc);
    const resolved = resolvedSet.has(target.trim());
    const cls = resolved ? "wikilink" : "wikilink wikilink-unresolved";
    const label = (display || target).trim();
    if (!href) return `<span class="${cls}">${escapeHtml(label)}</span>`;
    return `<a class="${cls}" href="${href}">${escapeHtml(label)}</a>`;
  });
}

function escapeHtml(s) {
  return String(s)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function stripFrontMatter(md) {
  if (!md.startsWith("---")) return md;
  const lines = md.split("\n");
  for (let i = 1; i < lines.length; i++) {
    if (lines[i].trim() === "---") {
      return lines.slice(i + 1).join("\n");
    }
  }
  return md;
}

// Collect wiki-link targets we know resolved so we can style them.
// The server's /api/doc doesn't return this directly per-link, so we
// approximate: any target form we can route client-side is considered
// "resolved" for styling purposes, which matches the UX expectation
// (dangling links show red wavy underline once `pedia check` flags them).
function resolvedLinkSet(doc) {
  // Build from the raw markdown — any link whose client-side resolver
  // returns an href is treated as resolved. Unresolved routing means
  // no type match or empty reference, which should surface as dangling.
  const md = doc.markdown || "";
  const resolved = new Set();
  let m;
  WIKI_LINK_RE.lastIndex = 0;
  while ((m = WIKI_LINK_RE.exec(md)) !== null) {
    const target = m[1].trim();
    if (wikiLinkHref(target, doc)) resolved.add(target);
  }
  return resolved;
}

export function DocView({ path }) {
  const [state, setState] = useState({ loading: true });
  const [externalTemplates, setExternalTemplates] = useState({});

  useEffect(() => {
    setState({ loading: true });
    fetch("/api/doc?path=" + encodeURIComponent(path))
      .then((r) => r.ok ? r.json() : r.json().then((j) => Promise.reject(j.error || r.statusText)))
      .then((data) => setState({ loading: false, data }))
      .catch((e) => setState({ loading: false, error: String(e) }));
  }, [path]);

  useEffect(() => {
    fetch("/api/external-links").then(r => r.json()).then(d => setExternalTemplates(d.templates || {})).catch(() => {});
  }, []);

  const htmlBody = useMemo(() => {
    if (!state.data) return "";
    const body = stripFrontMatter(state.data.markdown || "");
    const resolved = resolvedLinkSet(state.data);
    const pre = preprocessWikiLinks(body, resolved, state.data);
    return marked.parse(pre);
  }, [state.data]);

  if (state.loading) return h("div", { class: "empty" }, "Loading ", h("code", {}, path), "...");
  if (state.error)   return h("div", { class: "error" }, state.error);
  const doc = state.data;

  return h("div", { class: "doc-layout" },
    // --- Main column -----------------------------------------------
    h("div", { class: "doc-main" },
      h("div", { class: "crumbs" },
        h("span", { class: "dt" }, doc.doc_type),
        h("a", { href: "#/" }, "TOC"),
        " / ",
        h("span", {}, doc.path)
      ),
      // Dangerously-set the rendered markdown; it's local trusted content.
      h("div", { dangerouslySetInnerHTML: { __html: htmlBody } })
    ),
    // --- Side panel ------------------------------------------------
    h("aside", { class: "doc-side" },
      h(BlocksPanel, { blocks: doc.blocks, path: doc.path }),
      h(RefsPanel, { title: "This cites", refs: doc.refs_out }),
      h(RefsPanel, { title: "Cited by", refs: doc.refs_in }),
      h(ExternalPanel, { docMeta: doc.doc_meta, templates: externalTemplates })
    )
  );
}

function BlocksPanel({ blocks, path }) {
  return h("div", { class: "panel" },
    h("h3", {}, "Blocks in this doc"),
    h("ul", {},
      (blocks || []).map((b) =>
        h("li", { key: b.id },
          h("a", { href: "#/block/" + b.id, title: b.heading_slug },
            b.heading_title || b.heading_slug || "(body)"
          ),
          " ",
          h("span", { class: "kind-tag" }, b.kind || "heading"),
          b.universal ? h("span", { class: "kind-tag", style: "color:var(--warn)" }, "universal") : null
        )
      )
    )
  );
}

export function RefsPanel({ title, refs }) {
  if (!refs || refs.length === 0) {
    return h("div", { class: "panel" },
      h("h3", {}, title),
      h("div", { class: "muted", style: "font-size:12px" }, "(none)")
    );
  }
  return h("div", { class: "panel" },
    h("h3", {}, title),
    h("ul", {},
      refs.map((r) =>
        h("li", { key: r.id },
          h("span", { class: "kind-tag" }, r.kind || "cites"),
          h("a", { href: "#/block/" + r.id }, r.heading_title || r.heading_slug || r.doc_path),
          h("span", { class: "muted", style: "display:block;font-size:11px" }, r.doc_path)
        )
      )
    )
  );
}

// External-system deep-link panel. The wiki advertises the URL — it
// doesn't fetch content. Link appears when a template's activation
// condition is satisfied by doc front-matter.
export function ExternalPanel({ docMeta, templates }) {
  if (!templates || Object.keys(templates).length === 0) return null;
  docMeta = docMeta || {};

  const links = [];
  // hopewell: if docMeta has hopewell_id, fill {id}
  if (templates.hopewell && docMeta.hopewell_id) {
    links.push({
      label: "Hopewell " + docMeta.hopewell_id,
      href: templates.hopewell.template.replace("{id}", docMeta.hopewell_id),
    });
  }
  // github_issues: if docMeta has github_issue (and github_repo or templates.github_issues.repo)
  if (templates.github_issues && (docMeta.github_issue || docMeta.issue_id)) {
    const id = docMeta.github_issue || docMeta.issue_id;
    const repo = docMeta.github_repo || templates.github_issues.repo || "{repo}";
    links.push({
      label: `GitHub issue #${id}`,
      href: templates.github_issues.template
        .replace("{repo}", repo)
        .replace("{id}", id),
    });
  }
  // jira: if docMeta has jira_key
  if (templates.jira && docMeta.jira_key) {
    const instance = docMeta.jira_instance || templates.jira.instance || "{instance}";
    links.push({
      label: "JIRA " + docMeta.jira_key,
      href: templates.jira.template
        .replace("{instance}", instance)
        .replace("{id}", docMeta.jira_key),
    });
  }
  // github_code: if docMeta has source_path (and optionally source_sha + source_line)
  if (templates.github_code && docMeta.source_path) {
    const repo = docMeta.github_repo || templates.github_code.repo || "{repo}";
    const sha = docMeta.source_sha || "main";
    const line = docMeta.source_line || 1;
    links.push({
      label: "Source " + docMeta.source_path,
      href: templates.github_code.template
        .replace("{repo}", repo)
        .replace("{sha}", sha)
        .replace("{path}", docMeta.source_path)
        .replace("{line}", line),
    });
  }

  if (links.length === 0) return null;
  return h("div", { class: "panel" },
    h("h3", {}, "External links"),
    links.map((l) =>
      h("a", { class: "ext-link", href: l.href, target: "_blank", rel: "noopener" }, l.label)
    )
  );
}
