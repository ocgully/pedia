// Graph view — interactive dep/prov graph around a block.
//
// Mirrors the Hopewell HW-0042 canvas stack exactly:
//   * React Flow v12 via esm.sh with preact/compat alias
//   * elkjs layered layout (LR), same options Hopewell uses
//   * Custom node type with left/right Handles so edges terminate on
//     anchors rather than floating around a rect boundary.

import { h, Fragment } from "https://esm.sh/preact@10.22.0";
import {
  useState, useEffect, useMemo, useCallback,
} from "https://esm.sh/preact@10.22.0/hooks";

// --- React Flow via esm.sh with preact/compat alias. The deps=preact
//     pin is what makes esm.sh resolve the SAME preact instance as the
//     rest of the app — otherwise two preact copies cross-talk on hooks.
const XYFLOW_URL =
  "https://esm.sh/@xyflow/react@12.3.6" +
  "?alias=react:preact/compat,react-dom:preact/compat" +
  "&deps=preact@10.22.0";

const {
  ReactFlow,
  ReactFlowProvider,
  Controls,
  MiniMap,
  Background,
  BackgroundVariant,
  Handle,
  Position,
} = await import(XYFLOW_URL);

// Inject React Flow's stylesheet once.
(function ensureRfStylesheet() {
  const href = "https://esm.sh/@xyflow/react@12.3.6/dist/style.css";
  if (document.querySelector(`link[data-rfstyle][href="${href}"]`)) return;
  const link = document.createElement("link");
  link.rel = "stylesheet";
  link.href = href;
  link.setAttribute("data-rfstyle", "1");
  document.head.appendChild(link);
})();

const ELK = (await import("https://esm.sh/elkjs@0.9.3/lib/elk.bundled.js")).default;
const elk = new ELK();


// --- Custom node type: left target handle, right source handle.
const HANDLE_STYLE = {
  width: 6, height: 6,
  background: "transparent",
  border: "none",
  opacity: 0,
};

function PediaBlockNode({ data }) {
  const typ = data.doc_type || "documentation";
  const cls = `graph-node dt-${typ}${data.is_anchor ? " anchor" : ""}`;
  return h("div", { class: cls },
    h(Handle, { type: "target", position: Position.Left, style: HANDLE_STYLE }),
    h("div", { class: "gn-type" }, typ),
    h("div", { style: "font-weight:500" }, data.label || ""),
    h("div", { class: "muted", style: "font-size:10px;margin-top:2px" }, data.doc_path),
    h(Handle, { type: "source", position: Position.Right, style: HANDLE_STYLE })
  );
}

const nodeTypes = { pediaBlock: PediaBlockNode };

// --- elkjs layered layout. Input is a {nodes, edges} pair from our API,
//     output mutates node.position. Same algorithm + spacing values
//     Hopewell uses so visuals stay consistent across products.
async function layoutWithElk(nodes, edges) {
  const nodeSize = (n) => {
    // rough: wider label -> wider node
    const w = Math.max(160, 40 + (n.data?.label?.length || 10) * 6);
    return { width: w, height: 54 };
  };

  const graph = {
    id: "root",
    layoutOptions: {
      "elk.algorithm": "layered",
      "elk.direction": "RIGHT",
      "elk.layered.spacing.nodeNodeBetweenLayers": "60",
      "elk.spacing.nodeNode": "20",
      "elk.layered.nodePlacement.strategy": "BRANDES_KOEPF",
    },
    children: nodes.map((n) => ({ id: n.id, ...nodeSize(n) })),
    edges: edges.map((e) => ({ id: e.id, sources: [e.source], targets: [e.target] })),
  };

  const res = await elk.layout(graph);
  const byId = new Map((res.children || []).map((c) => [c.id, c]));
  return nodes.map((n) => {
    const c = byId.get(n.id);
    if (c && typeof c.x === "number") {
      return { ...n, position: { x: c.x, y: c.y } };
    }
    return n;
  });
}

export function GraphView({ id }) {
  const [state, setState] = useState({ loading: true });
  const [laidOut, setLaidOut] = useState(null);

  useEffect(() => {
    setState({ loading: true });
    fetch(`/api/graph?block=${encodeURIComponent(id)}&depth=2`)
      .then(r => r.ok ? r.json() : r.json().then(j => Promise.reject(j.error || r.statusText)))
      .then(data => setState({ loading: false, data }))
      .catch(e => setState({ loading: false, error: String(e) }));
  }, [id]);

  useEffect(() => {
    if (!state.data) return;
    let cancelled = false;
    layoutWithElk(state.data.nodes, state.data.edges)
      .then((nodes) => { if (!cancelled) setLaidOut({ nodes, edges: state.data.edges }); })
      .catch((e) => { if (!cancelled) setState(s => ({ ...s, error: String(e) })); });
    return () => { cancelled = true; };
  }, [state.data]);

  const onNodeClick = useCallback((evt, node) => {
    window.location.hash = "#/block/" + node.id;
  }, []);

  if (state.loading) return h("div", { class: "empty" }, `Building graph around ${id}...`);
  if (state.error)   return h("div", { class: "error" }, state.error);
  if (!laidOut)      return h("div", { class: "empty" }, "Laying out...");

  return h(Fragment, {},
    h("div", { class: "crumbs" },
      h("a", { href: "#/" }, "TOC"),
      " / ",
      h("a", { href: "#/block/" + id }, id),
      " / graph"
    ),
    h("div", { class: "graph-wrap" },
      h(ReactFlowProvider, {},
        h(ReactFlow, {
          nodes: laidOut.nodes,
          edges: laidOut.edges,
          nodeTypes,
          nodesDraggable: false,
          nodesConnectable: false,
          elementsSelectable: true,
          fitView: true,
          onNodeClick,
          proOptions: { hideAttribution: true },
        },
          h(Background, { variant: BackgroundVariant.Dots, gap: 16, size: 1, color: "#2b303a" }),
          h(Controls, { showInteractive: false }),
          h(MiniMap, { pannable: true, zoomable: true, nodeColor: () => "#6ea8ff" }),
        )
      )
    )
  );
}
