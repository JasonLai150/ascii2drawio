"""Deterministic intermediate representation.

The parser's structured, grid-grounded view of a diagram — nodes, edges, the
text it could not account for, and the *ambiguity flags* marking where it is
least sure. Built with no LLM. Two uses:

1. **Standalone** — a debug/audit artifact (``--ir`` dumps it as JSON).
2. **Reconciler input** — the grounded context the LLM pass consumes, so it
   reads coordinates off the parser instead of inventing them.

The flags are the key idea: the old pipeline only reacted to *orphan edge
cells*, which catches edge-tracing failures but is blind to "confident but
wrong" cases (a missed borderless node, a truncated label). Flagging
unconsumed text — split into *ungrounded* (free-floating → likely a missed
node) vs *truncation suspect* (hugging a line → likely a label fragment) —
broadens the trigger to those.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from .edges import Edge, _orphan_clusters
from .glyphs import is_edge_glyph
from .grid import Grid
from .nodes import Node

# consumed[][] tags that mean "an edge owns this cell" — text touching one of
# these is more likely a label fragment than a node.
_EDGE_TAGS = ("edge", "edge-label", "orphan-edge")


@dataclass
class TextRun:
    """A run of unconsumed text the parser didn't account for."""
    top: int
    left: int
    bottom: int
    right: int
    text: str


@dataclass
class AmbiguityFlag:
    """A region the deterministic parser is least confident about — the trigger
    for the LLM reconciliation pass (broader than orphan cells alone)."""
    kind: str  # orphan_cluster | ungrounded_text | truncation_suspect
    top: int
    left: int
    bottom: int
    right: int
    detail: str


@dataclass
class IR:
    width: int
    height: int
    nodes: list[Node]
    edges: list[Edge]
    text_runs: list[TextRun]
    flags: list[AmbiguityFlag]

    def to_dict(self) -> dict:
        return {
            "width": self.width,
            "height": self.height,
            "nodes": [
                {
                    "id": n.id, "top": n.top, "left": n.left,
                    "bottom": n.bottom, "right": n.right, "label": n.label,
                    "borderless": n.borderless, "parent": n.parent,
                }
                for n in self.nodes
            ],
            "edges": [
                {
                    "id": e.id, "src": e.src, "dst": e.dst, "label": e.label,
                    "arrow_dst": e.has_arrow_dst, "arrow_src": e.has_arrow_src,
                }
                for e in self.edges
            ],
            "text_runs": [asdict(t) for t in self.text_runs],
            "flags": [asdict(f) for f in self.flags],
        }


def build_ir(g: Grid, consumed, nodes: list[Node], edges: list[Edge]) -> IR:
    """Package the deterministic parse plus ambiguity flags. Pure — no LLM."""
    runs = _unconsumed_text_runs(g, consumed)
    text_runs = [tr for tr, _ in runs]
    flags: list[AmbiguityFlag] = []

    for cluster in _orphan_clusters(consumed, g):
        rs = [r for r, _ in cluster]
        cs = [c for _, c in cluster]
        top, left, bottom, right = min(rs), min(cs), max(rs), max(cs)
        detail = f"{len(cluster)} edge cell(s) did not resolve to two nodes"
        near = _nearby_nodes(nodes, top, left, bottom, right)
        if near:
            # Give the reconciler grounded ids to wire (fan-in/merge) instead of
            # guessing — these are the nodes flanking the unresolved region.
            detail += "; nearby nodes: " + ", ".join(
                f"n{n.id} {n.label!r}" for n in near)
        flags.append(AmbiguityFlag("orphan_cluster", top, left, bottom, right, detail))

    for tr, cells in runs:
        if _hugs_edge(g, consumed, cells):
            flags.append(AmbiguityFlag(
                "truncation_suspect", tr.top, tr.left, tr.bottom, tr.right,
                f"unconsumed text {tr.text!r} hugging an edge — possible "
                f"truncated or missed label",
            ))
        else:
            flags.append(AmbiguityFlag(
                "ungrounded_text", tr.top, tr.left, tr.bottom, tr.right,
                f"unconsumed text {tr.text!r} — possible missed node",
            ))

    return IR(width=g.w, height=g.h, nodes=nodes, edges=edges,
              text_runs=text_runs, flags=flags)


def _nearby_nodes(nodes: list[Node], top: int, left: int, bottom: int,
                  right: int, pad: int = 3) -> list[Node]:
    """Nodes whose bbox lies within ``pad`` cells of the region — the candidates
    an unresolved (merge/fan) region most likely connects."""
    return [
        n for n in nodes
        if (n.top - pad <= bottom and n.bottom + pad >= top
            and n.left - pad <= right and n.right + pad >= left)
    ]


def _unconsumed_text_runs(g: Grid, consumed):
    """Flood unconsumed, non-edge text into runs (single-space horizontal
    bridge, like loose mode). Returns (TextRun, cells) for runs with alnum."""
    def is_text(r: int, c: int) -> bool:
        return (g.in_bounds(r, c) and consumed[r][c] is None
                and g.at(r, c) != " " and not is_edge_glyph(g, r, c))

    seen: set[tuple[int, int]] = set()
    runs = []
    for r in range(g.h):
        for c in range(g.w):
            if (r, c) in seen or not is_text(r, c):
                continue
            cells: list[tuple[int, int]] = []
            stack = [(r, c)]
            while stack:
                cr, cc = stack.pop()
                if (cr, cc) in seen or not is_text(cr, cc):
                    continue
                seen.add((cr, cc))
                cells.append((cr, cc))
                for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    stack.append((cr + dr, cc + dc))
                if g.at(cr, cc + 1) == " " and is_text(cr, cc + 2):
                    stack.append((cr, cc + 2))
                if g.at(cr, cc - 1) == " " and is_text(cr, cc - 2):
                    stack.append((cr, cc - 2))
            top = min(p[0] for p in cells)
            bottom = max(p[0] for p in cells)
            left = min(p[1] for p in cells)
            right = max(p[1] for p in cells)
            lines = []
            for rr in range(top, bottom + 1):
                rcols = [pc for pr, pc in cells if pr == rr]
                if not rcols:
                    continue
                line = "".join(g.at(rr, x) for x in range(min(rcols), max(rcols) + 1)).strip()
                if line:
                    lines.append(line)
            text = " ".join(lines)
            if any(ch.isalnum() for ch in text):
                runs.append((TextRun(top, left, bottom, right, text), cells))
    return runs


def _hugs_edge(g: Grid, consumed, cells) -> bool:
    """True if any cell of the run is 8-adjacent to an edge-owned cell."""
    for r, c in cells:
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                nr, nc = r + dr, c + dc
                tag = consumed[nr][nc] if g.in_bounds(nr, nc) else None
                if tag is not None and tag[0] in _EDGE_TAGS:
                    return True
    return False
