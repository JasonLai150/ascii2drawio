#!/usr/bin/env python3
"""ascii2drawio — convert ASCII/Unicode box-drawing diagrams into draw.io XML.

Strict-mode prototype: detects closed rectangles as nodes, traces line glyphs
into edges between them, captures arrowhead direction and mid-edge labels.

Usage:
    ascii2drawio.py input.txt > out.drawio
    cat input.txt | ascii2drawio.py -o out.drawio
    ascii2drawio.py input.txt --annotate    # debug view
"""
from __future__ import annotations

import argparse
import html
import json
import os
import sys
from dataclasses import dataclass, field
from typing import Any, Optional

# ---------- glyph classes ----------

H_LINE = set("─-")
V_LINE = set("│|")
CORNERS = {
    "┌": ("R", "D"),
    "┐": ("L", "D"),
    "└": ("R", "U"),
    "┘": ("L", "U"),
}
TEES = {
    "├": ("R", "U", "D"),
    "┤": ("L", "U", "D"),
    "┬": ("L", "R", "D"),
    "┴": ("L", "R", "U"),
    "┼": ("L", "R", "U", "D"),
}
PLUS = {"+"}
ARROW_R = set("→>")
ARROW_L = set("←<")
ARROW_U = set("↑^")
ARROW_D = set("↓v")
ARROWS = ARROW_R | ARROW_L | ARROW_U | ARROW_D
LINE_CHARS = H_LINE | V_LINE | set(CORNERS) | set(TEES) | PLUS

DIRS = {"L": (0, -1), "R": (0, 1), "U": (-1, 0), "D": (1, 0)}
OPP = {"L": "R", "R": "L", "U": "D", "D": "U"}

TL_CORNERS = {"┌", "+"}
TR_CORNERS = {"┐", "+"}
BL_CORNERS = {"└", "+"}
BR_CORNERS = {"┘", "+"}

# Glyphs accepted as part of a horizontal border run (line + tees that have
# horizontal continuation: ┬ ┴ ┼). Allows boxes that share borders with tee'd
# edges to still close.
H_BORDER = H_LINE | {"┬", "┴", "┼"}
# Same for vertical border runs.
V_BORDER = V_LINE | {"├", "┤", "┼"}


def connects(ch: str) -> set[str]:
    """Directions this glyph connects outward to neighboring line glyphs."""
    if ch in H_LINE:
        return {"L", "R"}
    if ch in V_LINE:
        return {"U", "D"}
    if ch in CORNERS:
        return set(CORNERS[ch])
    if ch in TEES:
        return set(TEES[ch])
    if ch in PLUS:
        return {"L", "R", "U", "D"}
    if ch in ARROW_R or ch in ARROW_L:
        return {"L", "R"}
    if ch in ARROW_U or ch in ARROW_D:
        return {"U", "D"}
    return set()


def arrow_dir(ch: str) -> Optional[str]:
    if ch in ARROW_R:
        return "R"
    if ch in ARROW_L:
        return "L"
    if ch in ARROW_U:
        return "U"
    if ch in ARROW_D:
        return "D"
    return None


# ---------- grid ----------


@dataclass
class Grid:
    rows: list[list[str]]

    @classmethod
    def from_text(cls, text: str) -> "Grid":
        lines = text.split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        if not lines:
            return cls([])
        w = max(len(line) for line in lines)
        return cls([list(line.ljust(w)) for line in lines])

    @property
    def h(self) -> int:
        return len(self.rows)

    @property
    def w(self) -> int:
        return len(self.rows[0]) if self.rows else 0

    def at(self, r: int, c: int) -> str:
        if 0 <= r < self.h and 0 <= c < self.w:
            return self.rows[r][c]
        return " "

    def in_bounds(self, r: int, c: int) -> bool:
        return 0 <= r < self.h and 0 <= c < self.w


# ---------- node detection (closed rectangles) ----------


@dataclass
class Node:
    id: int
    top: int
    left: int
    bottom: int
    right: int
    label: str

    @property
    def width(self) -> int:
        return self.right - self.left + 1

    @property
    def height(self) -> int:
        return self.bottom - self.top + 1


def find_rectangles(g: Grid, consumed) -> list[Node]:
    nodes: list[Node] = []
    for r in range(g.h):
        for c in range(g.w):
            if consumed[r][c] is not None:
                continue
            if g.at(r, c) not in TL_CORNERS:
                continue
            node = _try_close_rect(g, r, c)
            if node is None:
                continue
            node.id = len(nodes)
            _mark_node(consumed, node)
            nodes.append(node)
    return nodes


def _try_close_rect(g: Grid, r0: int, c0: int) -> Optional[Node]:
    # walk top edge: accept tees as continuation of horizontal run
    c = c0 + 1
    while c < g.w and g.at(r0, c) in H_BORDER:
        c += 1
    if c >= g.w or g.at(r0, c) not in TR_CORNERS or c - c0 < 2:
        return None
    c1 = c
    # walk right edge with ±1 column tolerance to handle LLM column drift
    r = r0 + 1
    right_col_at_row = {r0: c1}
    while r < g.h:
        if g.at(r, c1) in V_BORDER:
            right_col_at_row[r] = c1
            r += 1
        elif g.at(r, c1 - 1) in V_BORDER:
            right_col_at_row[r] = c1 - 1
            r += 1
        elif c1 + 1 < g.w and g.at(r, c1 + 1) in V_BORDER:
            right_col_at_row[r] = c1 + 1
            r += 1
        else:
            break
    if r >= g.h or g.at(r, c1) not in BR_CORNERS or r - r0 < 2:
        # try BR at c1±1 too, but only if walls drifted there consistently
        if r < g.h and r - r0 >= 2:
            for dc in (-1, 1):
                if 0 <= c1 + dc < g.w and g.at(r, c1 + dc) in BR_CORNERS:
                    c1 = c1 + dc
                    break
            else:
                return None
        else:
            return None
    r1 = r
    # verify bottom + left
    for cc in range(c0 + 1, c1):
        if g.at(r1, cc) not in H_BORDER:
            return None
    if g.at(r1, c0) not in BL_CORNERS:
        return None
    left_col_at_row = {r0: c0, r1: c0}
    for rr in range(r0 + 1, r1):
        if g.at(rr, c0) in V_BORDER:
            left_col_at_row[rr] = c0
        elif g.at(rr, c0 + 1) in V_BORDER:
            left_col_at_row[rr] = c0 + 1
        elif c0 - 1 >= 0 and g.at(rr, c0 - 1) in V_BORDER:
            left_col_at_row[rr] = c0 - 1
        else:
            return None
    # interior label (skip cells that drift overlapped into wall)
    label_lines = []
    for rr in range(r0 + 1, r1):
        lc = left_col_at_row[rr] + 1
        rc = right_col_at_row.get(rr, c1)
        line = "".join(g.at(rr, cc) for cc in range(lc, rc)).strip()
        if line:
            label_lines.append(line)
    return Node(
        id=-1,
        top=r0,
        left=c0,
        bottom=r1,
        right=c1,
        label=" ".join(label_lines),
    )


def _mark_node(consumed, node: Node) -> None:
    for c in range(node.left, node.right + 1):
        consumed[node.top][c] = ("node", node.id)
        consumed[node.bottom][c] = ("node", node.id)
    for r in range(node.top, node.bottom + 1):
        consumed[r][node.left] = ("node", node.id)
        consumed[r][node.right] = ("node", node.id)
    for r in range(node.top + 1, node.bottom):
        for c in range(node.left + 1, node.right):
            consumed[r][c] = ("node-interior", node.id)


# ---------- edge tracing ----------


@dataclass
class Edge:
    id: int
    src: Optional[int] = None
    dst: Optional[int] = None
    label: str = ""
    has_arrow_dst: bool = False
    has_arrow_src: bool = False
    cells: list[tuple[int, int]] = field(default_factory=list)


LABEL_LOOKAHEAD = 40


def find_edges(g: Grid, consumed, nodes: list[Node]) -> list[Edge]:
    edges: list[Edge] = []
    for r in range(g.h):
        for c in range(g.w):
            if consumed[r][c] is not None:
                continue
            ch = g.at(r, c)
            if ch not in LINE_CHARS and ch not in ARROWS:
                continue
            cells, label = _bfs_edge(g, consumed, r, c)
            if not cells:
                continue
            edge = _build_edge(g, len(edges), cells, label, nodes)
            if edge is None:
                # mark cells consumed anyway so we don't reprocess them
                for rr, cc in cells:
                    if consumed[rr][cc] is None:
                        consumed[rr][cc] = ("orphan-edge", -1)
                continue
            for rr, cc in cells:
                if consumed[rr][cc] is None:
                    consumed[rr][cc] = ("edge", edge.id)
            edges.append(edge)
    return edges


def _bfs_edge(g: Grid, consumed, r0: int, c0: int):
    """Walk connected line/arrow glyphs; bridge label gaps along the same axis."""
    visited: set[tuple[int, int]] = set()
    cells: list[tuple[int, int]] = []
    label_runs: list[str] = []
    stack = [(r0, c0)]
    while stack:
        r, c = stack.pop()
        if (r, c) in visited:
            continue
        if not g.in_bounds(r, c):
            continue
        if consumed[r][c] is not None:
            continue
        ch = g.at(r, c)
        if ch not in LINE_CHARS and ch not in ARROWS:
            continue
        visited.add((r, c))
        cells.append((r, c))
        for d in connects(ch):
            dr, dc = DIRS[d]
            nr, nc = r + dr, c + dc
            if not g.in_bounds(nr, nc):
                continue
            if consumed[nr][nc] is not None:
                # node border = endpoint; node-interior = stop
                continue
            nch = g.at(nr, nc)
            if (nch in LINE_CHARS or nch in ARROWS) and OPP[d] in connects(nch):
                stack.append((nr, nc))
                continue
            # gap-bridging across labels along same axis
            bridged = _look_ahead_bridge(g, consumed, r, c, d)
            if bridged is not None:
                text, end_cell = bridged
                if text:
                    label_runs.append(text)
                stack.append(end_cell)
    return cells, " ".join(label_runs)


def _look_ahead_bridge(g: Grid, consumed, r: int, c: int, d: str):
    """If a label interrupts a line, look ahead for a connecting glyph."""
    dr, dc = DIRS[d]
    text_chars: list[str] = []
    nr, nc = r + dr, c + dc
    steps = 0
    while g.in_bounds(nr, nc) and steps < LABEL_LOOKAHEAD:
        if consumed[nr][nc] is not None:
            return None
        ch = g.at(nr, nc)
        if ch in LINE_CHARS or ch in ARROWS:
            if OPP[d] in connects(ch) and any(t.strip() for t in text_chars):
                return ("".join(text_chars).strip(), (nr, nc))
            return None
        text_chars.append(ch)
        nr += dr
        nc += dc
        steps += 1
    return None


def _build_edge(g: Grid, eid: int, cells, label: str, nodes: list[Node]) -> Optional[Edge]:
    endpoints = []  # (node_id, is_arrow_into_this_node)
    for r, c in cells:
        ch = g.at(r, c)
        ad = arrow_dir(ch)
        for d in connects(ch):
            dr, dc = DIRS[d]
            nr, nc = r + dr, c + dc
            for n in nodes:
                if n.top <= nr <= n.bottom and n.left <= nc <= n.right:
                    endpoints.append((n.id, ad == d))
        if ad is not None:
            dr, dc = DIRS[ad]
            nr, nc = r + dr, c + dc
            for n in nodes:
                if n.top <= nr <= n.bottom and n.left <= nc <= n.right:
                    endpoints.append((n.id, True))
    if not endpoints:
        return None
    # collapse duplicates while preserving order
    distinct = []
    for nid, is_arrow in endpoints:
        existing = next((d for d in distinct if d[0] == nid), None)
        if existing is None:
            distinct.append([nid, is_arrow])
        elif is_arrow:
            existing[1] = True
    if len(distinct) < 2:
        return None
    (a_id, a_arr), (b_id, b_arr) = distinct[0], distinct[1]
    edge = Edge(id=eid, label=label, cells=list(cells))
    if b_arr and not a_arr:
        edge.src, edge.dst = a_id, b_id
        edge.has_arrow_dst = True
    elif a_arr and not b_arr:
        edge.src, edge.dst = b_id, a_id
        edge.has_arrow_dst = True
    else:
        edge.src, edge.dst = a_id, b_id
        edge.has_arrow_dst = a_arr or b_arr
        edge.has_arrow_src = a_arr and b_arr
    return edge


# ---------- drawio XML emit ----------

CHAR_W = 9
CHAR_H = 18


def emit_drawio(nodes: list[Node], edges: list[Edge]) -> str:
    cells_xml: list[str] = []
    for n in nodes:
        x = n.left * CHAR_W
        y = n.top * CHAR_H
        w = n.width * CHAR_W
        h = n.height * CHAR_H
        label = html.escape(n.label) if n.label else f"Node {n.id}"
        cells_xml.append(
            f'        <mxCell id="n{n.id}" value="{label}" '
            f'style="rounded=0;whiteSpace=wrap;html=1;fillColor=#ffffff;strokeColor=#000000;" '
            f'vertex="1" parent="1">\n'
            f'          <mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry" />\n'
            f"        </mxCell>"
        )
    for e in edges:
        if e.src is None or e.dst is None:
            continue
        end_arrow = "classic" if e.has_arrow_dst else "none"
        start_arrow = "classic" if e.has_arrow_src else "none"
        label = html.escape(e.label) if e.label else ""
        cells_xml.append(
            f'        <mxCell id="e{e.id}" value="{label}" '
            f'style="endArrow={end_arrow};startArrow={start_arrow};html=1;rounded=0;" '
            f'edge="1" source="n{e.src}" target="n{e.dst}" parent="1">\n'
            f'          <mxGeometry relative="1" as="geometry" />\n'
            f"        </mxCell>"
        )
    body = "\n".join(cells_xml)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<mxfile host="ascii2drawio" version="1.0">\n'
        '  <diagram name="ASCII Diagram" id="d1">\n'
        '    <mxGraphModel dx="800" dy="600" grid="1" gridSize="10" guides="1" '
        'tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" '
        'pageWidth="850" pageHeight="1100" math="0" shadow="0">\n'
        "      <root>\n"
        '        <mxCell id="0" />\n'
        '        <mxCell id="1" parent="0" />\n'
        f"{body}\n"
        "      </root>\n"
        "    </mxGraphModel>\n"
        "  </diagram>\n"
        "</mxfile>\n"
    )


# ---------- annotate (debug) ----------

ANSI = {
    "node": "\x1b[44;97m",          # blue bg
    "node-interior": "\x1b[104;30m",
    "edge": "\x1b[42;30m",           # green bg
    "orphan-edge": "\x1b[41;97m",   # red bg
    "reset": "\x1b[0m",
}


def annotate(g: Grid, consumed) -> str:
    out = []
    for r in range(g.h):
        line = []
        for c in range(g.w):
            ch = g.rows[r][c]
            tag = consumed[r][c]
            if tag is None:
                line.append(ch)
                continue
            kind = tag[0]
            color = ANSI.get(kind, "")
            if color:
                line.append(f"{color}{ch}{ANSI['reset']}")
            else:
                line.append(ch)
        out.append("".join(line))
    return "\n".join(out)


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


def _orphan_clusters(consumed, g: Grid) -> list[list[tuple[int, int]]]:
    """8-connected flood fill to group orphan-edge cells into clusters."""
    orphan_set: set[tuple[int, int]] = {
        (r, c)
        for r in range(g.h)
        for c in range(g.w)
        if consumed[r][c] is not None and consumed[r][c][0] == "orphan-edge"
    }
    visited: set[tuple[int, int]] = set()
    clusters: list[list[tuple[int, int]]] = []
    for start in sorted(orphan_set):
        if start in visited:
            continue
        cluster: list[tuple[int, int]] = []
        stack = [start]
        while stack:
            pos = stack.pop()
            if pos in visited or pos not in orphan_set:
                continue
            visited.add(pos)
            cluster.append(pos)
            r, c = pos
            for dr in (-1, 0, 1):
                for dc in (-1, 0, 1):
                    if dr == 0 and dc == 0:
                        continue
                    stack.append((r + dr, c + dc))
        clusters.append(cluster)
    return clusters


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


def _call_gemini(user_prompt: str, api_key: str) -> dict[str, Any]:
    import urllib.request

    body = json.dumps({
        "system_instruction": {"parts": [{"text": _REPAIR_SYSTEM_PROMPT}]},
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


# ---------- .env loader ----------


def _load_dotenv(path: str) -> None:
    """Load KEY=VALUE pairs from a .env file into os.environ (no-op if missing)."""
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = val
    except FileNotFoundError:
        pass


# ---------- CLI ----------


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Convert ASCII diagrams to draw.io XML.")
    p.add_argument("input", nargs="?", help="Input file (stdin if omitted)")
    p.add_argument("-o", "--output", help="Output file (stdout if omitted)")
    p.add_argument("--annotate", action="store_true",
                   help="Print colored grid showing parser classification")
    p.add_argument("--report", action="store_true",
                   help="Print summary of nodes/edges to stderr")
    p.add_argument("--llm", action="store_true",
                   help="Enable Gemini LLM repair pass for orphan regions "
                        "(requires GEMINI_API_KEY env var and 'pip install google-generativeai')")
    args = p.parse_args(argv)

    text = sys.stdin.read() if args.input is None else open(args.input, encoding="utf-8").read()
    g = Grid.from_text(text)
    consumed = [[None] * g.w for _ in range(g.h)]
    nodes = find_rectangles(g, consumed)
    edges = find_edges(g, consumed, nodes)

    if args.llm:
        _load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
        api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            sys.stderr.write(
                "Error: --llm requires GEMINI_API_KEY or GOOGLE_API_KEY environment variable\n"
            )
            return 1
        try:
            nodes, edges = llm_repair(g, consumed, nodes, edges, api_key, verbose=args.report)
        except ImportError:
            sys.stderr.write(
                "Error: --llm requires 'pip install google-generativeai'\n"
            )
            return 1

    if args.report or args.annotate:
        sys.stderr.write(f"nodes: {len(nodes)}, edges: {len(edges)}\n")
        for n in nodes:
            sys.stderr.write(f"  n{n.id} @ ({n.top},{n.left})-({n.bottom},{n.right}): {n.label!r}\n")
        for e in edges:
            sys.stderr.write(
                f"  e{e.id}: n{e.src} -> n{e.dst} "
                f"(arrow_dst={e.has_arrow_dst}, label={e.label!r})\n"
            )

    if args.annotate:
        sys.stdout.write(annotate(g, consumed) + "\n")
        return 0

    xml = emit_drawio(nodes, edges)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(xml)
    else:
        sys.stdout.write(xml)
    return 0


if __name__ == "__main__":
    sys.exit(main())
