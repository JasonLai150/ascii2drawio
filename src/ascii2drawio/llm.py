"""Optional Gemini passes: orphan-region repair (missing nodes/edges) and label
correction. Both are validated against the grid so the model can only use text
that's actually in the diagram — see the grounding guards below."""
from __future__ import annotations

import json
import sys
from typing import Any

from .edges import Edge, _orphan_clusters
from .glyphs import ARROWS, LINE_CHARS
from .grid import Grid
from .nodes import Node

if False:  # typing only, avoid import cycle at runtime
    from .ir import IR

# ---------- LLM repair pass ----------

_REPAIR_SYSTEM_PROMPT = """\
You are a diagram-parser assistant. A deterministic parser processed an ASCII/Unicode \
box-drawing diagram and tagged some cells as "orphan-edge" — characters that look like \
box-drawing or line glyphs but could not be connected to any detected node boundary.

Your job: examine the orphan region and identify what the parser missed. Return JSON \
describing any nodes (closed rectangles) or edges (connections between nodes).

RULES:
- All coordinates are 0-indexed and refer to the FULL grid (not the subgrid excerpt).
- For nodes: top < bottom, left < right.
- For edges: src_node_id and dst_node_id must be a valid node ID from the provided list \
OR the ID of a new node added earlier in the same repairs array. New nodes are numbered \
starting from the "next available node ID" shown in the prompt.
- Only report items you are confident about. If nothing is clearly missed, return \
{"repairs": []}.

Respond with strict JSON only — no markdown fences, no commentary.

SCHEMA:
{
  "repairs": [
    {"type": "node", "top": int, "left": int, "bottom": int, "right": int, "label": "str"},
    {"type": "edge", "src_node_id": int, "dst_node_id": int,
     "has_arrow_dst": bool, "has_arrow_src": bool, "label": "str"}
  ]
}
"""


def _padded_bbox(cluster: list[tuple[int, int]], pad: int, h: int, w: int) -> tuple[int, int, int, int]:
    rows = [r for r, _ in cluster]
    cols = [c for _, c in cluster]
    return (
        max(0, min(rows) - pad),
        max(0, min(cols) - pad),
        min(h - 1, max(rows) + pad),
        min(w - 1, max(cols) + pad),
    )


def _subgrid_text(g: Grid, r0: int, c0: int, r1: int, c1: int) -> str:
    lines = [f"(column offset: {c0} — each row starts at full-grid column {c0})"]
    for r in range(r0, r1 + 1):
        row_chars = "".join(g.at(r, c) for c in range(c0, c1 + 1))
        lines.append(f"[{r:4d}] {row_chars}")
    return "\n".join(lines)


def _build_repair_prompt(g: Grid, nodes: list[Node], cluster: list[tuple[int, int]], next_id: int) -> str:
    r0, c0, r1, c1 = _padded_bbox(cluster, pad=3, h=g.h, w=g.w)
    node_lines = [
        f"  id={n.id} label={n.label!r} bounds=({n.top},{n.left})-({n.bottom},{n.right})"
        for n in nodes
    ]
    nodes_block = "\n".join(node_lines) if node_lines else "  (none)"
    orphan_coords = ", ".join(f"({r},{c})" for r, c in sorted(cluster))
    subgrid = _subgrid_text(g, r0, c0, r1, c1)
    return (
        f"Detected nodes:\n{nodes_block}\n\n"
        f"Next available node ID for new nodes in repairs: {next_id}\n\n"
        f"Orphan-edge cells: {orphan_coords}\n\n"
        f"Subgrid excerpt (rows {r0}–{r1}):\n{subgrid}"
    )


_GEMINI_MODEL = "gemini-2.5-flash"
_GEMINI_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/"
    f"{_GEMINI_MODEL}:generateContent"
)


def _call_gemini(user_prompt: str, api_key: str,
                 system_prompt: str = _REPAIR_SYSTEM_PROMPT) -> dict[str, Any]:
    import urllib.request

    body = json.dumps({
        "system_instruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }).encode()
    req = urllib.request.Request(
        f"{_GEMINI_URL}?key={api_key}",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())
    text = data["candidates"][0]["content"]["parts"][0]["text"]
    return json.loads(text)


def _border_evidence(g: Grid, top: int, left: int, bottom: int, right: int) -> float:
    """Fraction of the claimed rectangle's border cells that are line glyphs."""
    border = [(top, c) for c in range(left, right + 1)]
    border += [(bottom, c) for c in range(left, right + 1)]
    border += [(r, left) for r in range(top + 1, bottom)]
    border += [(r, right) for r in range(top + 1, bottom)]
    if not border:
        return 0.0
    hits = sum(1 for r, c in border if g.at(r, c) in LINE_CHARS or g.at(r, c) in ARROWS)
    return hits / len(border)


def _label_evidence(g: Grid, top: int, left: int, bottom: int, right: int, label: str) -> bool:
    """True if the label's substantive word tokens actually appear in the region.

    Guards against the LLM inventing a node whose text is nowhere in the ASCII.
    """
    import re
    tokens = [t for t in re.findall(r"[A-Za-z0-9]+", label) if len(t) >= 3]
    if not tokens:
        return True  # nothing substantive to verify
    region = " ".join(
        "".join(g.at(r, c) for c in range(left, right + 1))
        for r in range(top, bottom + 1)
    )
    found = sum(1 for t in tokens if t in region)
    return found * 2 >= len(tokens)  # majority of tokens grounded in the grid


def _validate_repair(rep: Any, known_ids: set[int], g: Grid) -> bool:
    if not isinstance(rep, dict):
        return False
    t = rep.get("type")
    if t == "node":
        for k in ("top", "left", "bottom", "right"):
            if not isinstance(rep.get(k), int):
                return False
        if rep["top"] >= rep["bottom"] or rep["left"] >= rep["right"]:
            return False
        if rep["top"] < 0 or rep["left"] < 0 or rep["bottom"] >= g.h or rep["right"] >= g.w:
            return False
        # Hallucination guard: a real-but-missed box has border glyphs in the
        # grid, and its label text actually appears there. Invented boxes don't.
        if _border_evidence(g, rep["top"], rep["left"], rep["bottom"], rep["right"]) < 0.5:
            return False
        if not _label_evidence(g, rep["top"], rep["left"], rep["bottom"], rep["right"],
                               str(rep.get("label", ""))):
            return False
        return True
    if t == "edge":
        for k in ("src_node_id", "dst_node_id"):
            if not isinstance(rep.get(k), int):
                return False
        if rep["src_node_id"] == rep["dst_node_id"]:
            return False
        if rep["src_node_id"] not in known_ids or rep["dst_node_id"] not in known_ids:
            return False
        return True
    return False


def llm_repair(
    g: Grid,
    consumed,
    nodes: list[Node],
    edges: list[Edge],
    api_key: str,
    verbose: bool = False,
) -> tuple[list[Node], list[Edge]]:
    """Cluster orphan cells → call Gemini per cluster → validate → merge repairs."""
    clusters = _orphan_clusters(consumed, g)
    if not clusters:
        return nodes, edges

    new_nodes = list(nodes)
    new_edges = list(edges)

    for i, cluster in enumerate(clusters):
        next_id = len(new_nodes)
        prompt = _build_repair_prompt(g, new_nodes, cluster, next_id)
        try:
            result = _call_gemini(prompt, api_key)
        except Exception as exc:
            if verbose:
                sys.stderr.write(f"[llm] cluster {i}: API error: {exc}\n")
            continue

        repairs = result.get("repairs", [])
        if not isinstance(repairs, list):
            continue

        known_ids = {n.id for n in new_nodes}

        # First pass: nodes (so edges in the same batch can reference them)
        for rep in repairs:
            if rep.get("type") != "node":
                continue
            if not _validate_repair(rep, known_ids, g):
                if verbose:
                    sys.stderr.write(f"[llm] cluster {i}: rejected node repair: {rep}\n")
                continue
            node = Node(
                id=len(new_nodes),
                top=rep["top"],
                left=rep["left"],
                bottom=rep["bottom"],
                right=rep["right"],
                label=str(rep.get("label", "")),
            )
            new_nodes.append(node)
            known_ids.add(node.id)
            if verbose:
                sys.stderr.write(f"[llm] cluster {i}: added node {node.id} {node.label!r}\n")

        # Second pass: edges
        for rep in repairs:
            if rep.get("type") != "edge":
                continue
            if not _validate_repair(rep, known_ids, g):
                if verbose:
                    sys.stderr.write(f"[llm] cluster {i}: rejected edge repair: {rep}\n")
                continue
            edge = Edge(
                id=len(new_edges),
                src=rep["src_node_id"],
                dst=rep["dst_node_id"],
                label=str(rep.get("label", "")),
                has_arrow_dst=bool(rep.get("has_arrow_dst", True)),
                has_arrow_src=bool(rep.get("has_arrow_src", False)),
            )
            new_edges.append(edge)
            if verbose:
                sys.stderr.write(
                    f"[llm] cluster {i}: added edge n{edge.src}→n{edge.dst}"
                    f" label={edge.label!r}\n"
                )

    return new_nodes, new_edges


# ---------- LLM label-correction pass ----------

_LABEL_REVIEW_SYSTEM_PROMPT = """\
You are a diagram-parser proof-reader. A deterministic parser converted an \
ASCII/Unicode box-drawing diagram into nodes (boxes) and edges (arrows), and \
attached a text label to each where it could. Its label extraction is \
imperfect: labels can be truncated (only one word of a multi-word label \
captured), split, mis-attached to the wrong edge, or missed entirely.

Your job: compare each detected label against the ASCII and return corrections. \
A label is the short text written on or beside an arrow (e.g. "publish", \
"on fail", "hash key"), or the text inside a box.

RULES:
- Only propose a correction when the parser's label differs from what the \
diagram clearly shows. If a label is already correct, do not include it.
- Every label you return MUST be text that literally appears in the diagram. \
Never invent, expand, paraphrase, or translate. Copy the exact characters.
- Refer to edges and nodes by the IDs given. Do not add new edges or nodes.
- If nothing needs fixing, return {"repairs": []}.

Respond with strict JSON only — no markdown fences, no commentary.

SCHEMA:
{
  "repairs": [
    {"type": "label", "target": "edge", "id": int, "label": "str"},
    {"type": "label", "target": "node", "id": int, "label": "str"}
  ]
}
"""


def _full_grid_text(g: Grid) -> str:
    return "\n".join("".join(g.at(r, c) for c in range(g.w)) for r in range(g.h))


def _label_grounded(g: Grid, label: str) -> bool:
    """True if the label's text actually appears in the diagram.

    Mirrors the node guard: a majority of substantive (>=3 char) tokens must be
    present; for labels with no such token, the whole string must appear. This
    is what stops the model from inventing or paraphrasing label text.
    """
    import re
    grid_text = _full_grid_text(g)
    tokens = [t for t in re.findall(r"[A-Za-z0-9]+", label) if len(t) >= 3]
    if not tokens:
        s = label.strip()
        return (not s) or (s in grid_text)
    found = sum(1 for t in tokens if t in grid_text)
    return found * 2 >= len(tokens)


def _validate_label_repair(rep: Any, node_ids: set[int], edge_ids: set[int], g: Grid) -> bool:
    if not isinstance(rep, dict) or rep.get("type") != "label":
        return False
    if not isinstance(rep.get("id"), int) or not isinstance(rep.get("label"), str):
        return False
    target = rep.get("target")
    if target == "edge" and rep["id"] not in edge_ids:
        return False
    if target == "node" and rep["id"] not in node_ids:
        return False
    if target not in ("edge", "node"):
        return False
    return _label_grounded(g, rep["label"])


def _build_label_prompt(g: Grid, nodes: list[Node], edges: list[Edge]) -> str:
    node_lines = [f"  id={n.id} label={n.label!r}" for n in nodes] or ["  (none)"]
    edge_lines = [
        f"  id={e.id} n{e.src}->n{e.dst} label={e.label!r}" for e in edges
    ] or ["  (none)"]
    return (
        f"ASCII diagram:\n{_full_grid_text(g)}\n\n"
        f"Detected nodes:\n" + "\n".join(node_lines) + "\n\n"
        f"Detected edges (src->dst):\n" + "\n".join(edge_lines) + "\n\n"
        f"Return label corrections only."
    )


def llm_label_review(
    g: Grid,
    nodes: list[Node],
    edges: list[Edge],
    api_key: str,
    verbose: bool = False,
) -> tuple[list[Node], list[Edge]]:
    """One Gemini call to correct inaccurate/truncated labels on existing items.

    Validated against the grid so corrections can only use text that is really
    in the diagram. Applies in place to copies and returns them.
    """
    if not nodes and not edges:
        return nodes, edges
    prompt = _build_label_prompt(g, nodes, edges)
    try:
        result = _call_gemini(prompt, api_key, system_prompt=_LABEL_REVIEW_SYSTEM_PROMPT)
    except Exception as exc:
        if verbose:
            sys.stderr.write(f"[llm] label review: API error: {exc}\n")
        return nodes, edges

    repairs = result.get("repairs", [])
    if not isinstance(repairs, list):
        return nodes, edges

    node_by_id = {n.id: n for n in nodes}
    edge_by_id = {e.id: e for e in edges}
    applied: set[tuple[str, int]] = set()
    for rep in repairs:
        if not _validate_label_repair(rep, set(node_by_id), set(edge_by_id), g):
            if verbose:
                sys.stderr.write(f"[llm] label review: rejected {rep}\n")
            continue
        key = (rep["target"], rep["id"])
        if key in applied:                       # one correction per item, first wins
            continue
        applied.add(key)
        target = node_by_id[rep["id"]] if rep["target"] == "node" else edge_by_id[rep["id"]]
        if verbose:
            sys.stderr.write(
                f"[llm] label review: {rep['target']} {rep['id']} "
                f"{target.label!r} -> {rep['label']!r}\n"
            )
        target.label = rep["label"]
    return nodes, edges


# ---------- Holistic IR-grounded reconciler ----------
#
# One call that sees the WHOLE parse (the deterministic IR) plus the ambiguity
# flags, and returns a diff in grid coordinates. It supersedes the per-cluster
# orphan patcher + the separate label pass: a single view lets the model make
# cross-cutting decisions (a leftover word between two nodes is a node *because*
# of what flanks it) that a keyhole per-cluster prompt cannot. The IR anchors
# coordinates so the model reads them off the parser; every op is still gated by
# the same deterministic validators, so the model can only use real glyphs/text.

_RECONCILE_SYSTEM_PROMPT = """\
You are a diagram-parser reconciler. A deterministic parser converted an \
ASCII/Unicode box-drawing diagram into nodes (boxes / text-only nodes) and edges \
(arrows), and listed the text it could not account for plus "flags" marking \
regions it is unsure about. Reconcile its output with what the diagram actually \
shows by returning a diff.

You may:
- add a node the parser missed (a real box, or a text-only node an arrow points to),
- add an edge the parser missed between existing or newly-added nodes,
- relabel a node or edge whose text is wrong/truncated,
- reparent a node into the box that visually contains it.

RULES:
- All coordinates are 0-indexed into the FULL grid shown. For nodes: top < bottom, \
left < right.
- Every label MUST be text that literally appears in the diagram — copy the exact \
characters. Never invent, expand, paraphrase, or translate.
- add_edge src/dst must be an existing node id OR a node added earlier in this same \
ops array (new nodes are numbered from the "next node id" given).
- reparent only when the parent box strictly contains the child's bounds.
- Only include an op you are confident about. If nothing needs changing, return \
{"ops": []}.

Respond with strict JSON only — no markdown fences, no commentary.

SCHEMA:
{
  "ops": [
    {"op": "add_node", "top": int, "left": int, "bottom": int, "right": int, "label": "str"},
    {"op": "add_edge", "src": int, "dst": int, "has_arrow_dst": bool, "has_arrow_src": bool, "label": "str"},
    {"op": "relabel_node", "id": int, "label": "str"},
    {"op": "relabel_edge", "id": int, "label": "str"},
    {"op": "reparent_node", "id": int, "parent": int}
  ]
}
"""


def _build_reconcile_prompt(g: Grid, ir: "IR") -> str:
    node_lines = [
        f"  id={n.id} label={n.label!r} bounds=({n.top},{n.left})-({n.bottom},{n.right})"
        f"{' borderless' if n.borderless else ''}"
        f"{f' parent={n.parent}' if n.parent is not None else ''}"
        for n in ir.nodes
    ] or ["  (none)"]
    edge_lines = [
        f"  id={e.id} n{e.src}->n{e.dst} arrow_dst={e.has_arrow_dst} label={e.label!r}"
        for e in ir.edges
    ] or ["  (none)"]
    text_lines = [
        f"  {t.text!r} at ({t.top},{t.left})-({t.bottom},{t.right})"
        for t in ir.text_runs
    ] or ["  (none)"]
    flag_lines = [
        f"  [{f.kind}] ({f.top},{f.left})-({f.bottom},{f.right}): {f.detail}"
        for f in ir.flags
    ] or ["  (none)"]
    return (
        f"ASCII diagram:\n{_full_grid_text(g)}\n\n"
        f"Detected nodes:\n" + "\n".join(node_lines) + "\n\n"
        f"Detected edges:\n" + "\n".join(edge_lines) + "\n\n"
        f"Unaccounted text runs:\n" + "\n".join(text_lines) + "\n\n"
        f"Ambiguity flags (regions to reconcile):\n" + "\n".join(flag_lines) + "\n\n"
        f"Next node id for added nodes: {len(ir.nodes)}\n\n"
        f"Return a reconciliation diff."
    )


def _node_contains(parent: Node, child: Node) -> bool:
    return (parent.top < child.top and parent.bottom > child.bottom
            and parent.left < child.left and parent.right > child.right)


def llm_reconcile(
    g: Grid,
    ir: "IR",
    api_key: str,
    verbose: bool = False,
) -> tuple[list[Node], list[Edge]]:
    """One holistic Gemini call over the full IR, returning a validated diff.

    Fires only when the parser raised at least one ambiguity flag. Every op is
    gated by the same grid-grounding validators used by the granular passes, so
    the model can only add/relabel using glyphs and text actually present.
    """
    if not ir.flags:
        return ir.nodes, ir.edges
    prompt = _build_reconcile_prompt(g, ir)
    try:
        result = _call_gemini(prompt, api_key, system_prompt=_RECONCILE_SYSTEM_PROMPT)
    except Exception as exc:
        if verbose:
            sys.stderr.write(f"[llm] reconcile: API error: {exc}\n")
        return ir.nodes, ir.edges
    ops = result.get("ops", [])
    if not isinstance(ops, list):
        return ir.nodes, ir.edges
    return _apply_ops(g, ir, ops, verbose)


def _apply_ops(g: Grid, ir: "IR", ops: list, verbose: bool) -> tuple[list[Node], list[Edge]]:
    nodes = list(ir.nodes)
    edges = list(ir.edges)
    node_by_id = {n.id: n for n in nodes}
    edge_by_id = {e.id: e for e in edges}

    def log(msg: str) -> None:
        if verbose:
            sys.stderr.write(f"[llm] reconcile: {msg}\n")

    # 1. add_node (so later add_edge / reparent can reference new ids)
    for op in ops:
        if not isinstance(op, dict) or op.get("op") != "add_node":
            continue
        rep = {"type": "node", "top": op.get("top"), "left": op.get("left"),
               "bottom": op.get("bottom"), "right": op.get("right"),
               "label": op.get("label", "")}
        if not _validate_repair(rep, set(node_by_id), g):
            log(f"rejected add_node: {op}")
            continue
        n = Node(id=len(nodes), top=rep["top"], left=rep["left"],
                 bottom=rep["bottom"], right=rep["right"], label=str(rep["label"]))
        nodes.append(n)
        node_by_id[n.id] = n
        log(f"added node {n.id} {n.label!r}")

    known_ids = set(node_by_id)

    # 2. add_edge
    for op in ops:
        if not isinstance(op, dict) or op.get("op") != "add_edge":
            continue
        rep = {"type": "edge", "src_node_id": op.get("src"), "dst_node_id": op.get("dst")}
        if not _validate_repair(rep, known_ids, g):
            log(f"rejected add_edge: {op}")
            continue
        e = Edge(id=len(edges), src=rep["src_node_id"], dst=rep["dst_node_id"],
                 label=str(op.get("label", "")),
                 has_arrow_dst=bool(op.get("has_arrow_dst", True)),
                 has_arrow_src=bool(op.get("has_arrow_src", False)))
        edges.append(e)
        log(f"added edge n{e.src}->n{e.dst} label={e.label!r}")

    # 3. relabels (one per item, first wins)
    relabeled: set[tuple[str, int]] = set()
    for op in ops:
        if not isinstance(op, dict):
            continue
        kind = op.get("op")
        if kind not in ("relabel_node", "relabel_edge"):
            continue
        target_kind = "node" if kind == "relabel_node" else "edge"
        oid, label = op.get("id"), op.get("label")
        registry = node_by_id if target_kind == "node" else edge_by_id
        if not isinstance(oid, int) or not isinstance(label, str):
            log(f"rejected {kind}: {op}")
            continue
        if oid not in registry or not _label_grounded(g, label):
            log(f"rejected {kind}: {op}")
            continue
        key = (target_kind, oid)
        if key in relabeled:
            continue
        relabeled.add(key)
        log(f"relabel {target_kind} {oid} {registry[oid].label!r} -> {label!r}")
        registry[oid].label = label

    # 4. reparent (geometric containment grounded)
    for op in ops:
        if not isinstance(op, dict) or op.get("op") != "reparent_node":
            continue
        oid, pid = op.get("id"), op.get("parent")
        if not isinstance(oid, int) or oid not in node_by_id:
            log(f"rejected reparent_node: {op}")
            continue
        if pid is None:
            node_by_id[oid].parent = None
            log(f"reparent node {oid} -> root")
            continue
        if (not isinstance(pid, int) or pid not in node_by_id or pid == oid
                or not _node_contains(node_by_id[pid], node_by_id[oid])):
            log(f"rejected reparent_node: {op}")
            continue
        node_by_id[oid].parent = pid
        log(f"reparent node {oid} -> {pid}")

    return nodes, edges
