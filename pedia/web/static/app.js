// Pedia wiki — SPA root + hashchange router.
//
// Routes:
//   #/              -> TOC
//   #/doc/<path>    -> doc view (path is .pedia-relative, URL-encoded)
//   #/block/<id>    -> block deep-link (shareable)
//   #/search?q=...  -> search results
//   #/graph/<id>    -> graph view anchored on block <id>
//   #/trace/<id>    -> thread-of-impact view (up + down)
//
// Preact is loaded via esm.sh (same pin as Hopewell so the two UIs
// share a single preact instance when a dev opens both). No bundler,
// no npm — every dependency is a direct ESM import.

import { h, render, Fragment } from "https://esm.sh/preact@10.22.0";
import { useState, useEffect, useMemo, useCallback } from "https://esm.sh/preact@10.22.0/hooks";

import { TocView } from "/static/toc.js";
import { DocView } from "/static/doc.js";
import { BlockView, TraceView } from "/static/block.js";
import { SearchView } from "/static/search.js";
import { GraphView } from "/static/graph.js";

// ---------------------------------------------------------------------------
// Minimal hash router. Returns a {route, params} pair.
// ---------------------------------------------------------------------------

function parseHash(hash) {
  const raw = (hash || "").replace(/^#/, "") || "/";
  const [pathPart, queryPart] = raw.split("?", 2);
  const segs = pathPart.split("/").filter(Boolean);
  const qs = new URLSearchParams(queryPart || "");
  if (segs.length === 0) return { route: "toc", params: {}, qs };
  const head = segs[0];
  const rest = segs.slice(1);
  if (head === "doc") {
    return { route: "doc", params: { path: decodeURIComponent(rest.join("/")) }, qs };
  }
  if (head === "block") {
    return { route: "block", params: { id: decodeURIComponent(rest[0] || "") }, qs };
  }
  if (head === "search") {
    return { route: "search", params: { q: qs.get("q") || "" }, qs };
  }
  if (head === "graph") {
    return { route: "graph", params: { id: decodeURIComponent(rest[0] || "") }, qs };
  }
  if (head === "trace") {
    return { route: "trace", params: { id: decodeURIComponent(rest[0] || "") }, qs };
  }
  return { route: "toc", params: {}, qs };
}

function useRoute() {
  const [r, setR] = useState(() => parseHash(window.location.hash));
  useEffect(() => {
    const onChange = () => setR(parseHash(window.location.hash));
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return r;
}

// ---------------------------------------------------------------------------
// App shell
// ---------------------------------------------------------------------------

function App() {
  const route = useRoute();

  // One-time: load meta so we can display project root + version
  useEffect(() => {
    fetch("/api/meta").then(r => r.json()).then(m => {
      const el = document.getElementById("project-root");
      const ver = document.getElementById("pedia-version");
      if (el && m.project_root) {
        // show only the trailing two segments so the header stays short
        const parts = m.project_root.replaceAll("\\", "/").split("/").filter(Boolean);
        const tail = parts.slice(-2).join("/");
        el.textContent = tail;
        el.title = m.project_root;
      }
      if (ver && m.pedia_version) ver.textContent = "v" + m.pedia_version;
    }).catch(() => {});
  }, []);

  // Wire the header search box. Pressing Enter goes to #/search?q=...
  useEffect(() => {
    const form = document.getElementById("searchbox");
    const input = document.getElementById("searchq");
    if (!form || !input) return;
    const onSubmit = (e) => {
      e.preventDefault();
      const q = input.value.trim();
      if (q) window.location.hash = "#/search?q=" + encodeURIComponent(q);
    };
    form.addEventListener("submit", onSubmit);
    return () => form.removeEventListener("submit", onSubmit);
  }, []);

  // Update tab active state
  useEffect(() => {
    const tabs = document.querySelectorAll("nav.tabs a.tab");
    tabs.forEach(t => {
      const href = t.getAttribute("href") || "";
      const isActive =
        (href === "#/" && route.route === "toc") ||
        (href === "#/search" && route.route === "search");
      t.classList.toggle("active", isActive);
    });
  }, [route.route]);

  switch (route.route) {
    case "toc":    return h(TocView, {});
    case "doc":    return h(DocView, { path: route.params.path });
    case "block":  return h(BlockView, { id: route.params.id });
    case "trace":  return h(TraceView, { id: route.params.id });
    case "search": return h(SearchView, { q: route.params.q });
    case "graph":  return h(GraphView, { id: route.params.id });
    default:       return h(TocView, {});
  }
}

render(h(App, {}), document.getElementById("app"));
