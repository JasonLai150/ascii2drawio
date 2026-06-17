"""Edge tracing: flood-fill line components, bridge label gaps, split a
component into the edges it actually encodes (fan-out / fan-in), and attach
inline + floating labels. Also the orphan-cell clustering used downstream."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .glyphs import DIRS, OPP, arrow_dir, connects, is_edge_glyph
from .grid import Grid
from .nodes import Node


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
            if not is_edge_glyph(g, r, c):
                continue
            cells, label, label_pos = _bfs_edge(g, consumed, r, c)
            if not cells:
                continue
            new_edges = _build_edges(g, len(edges), cells, label, label_pos, nodes)
            if not new_edges:
                # mark cells consumed anyway so we don't reprocess them
                for rr, cc in cells:
                    if consumed[rr][cc] is None:
                        consumed[rr][cc] = ("orphan-edge", -1)
                continue
            for rr, cc in cells:
                if consumed[rr][cc] is None:
                    consumed[rr][cc] = ("edge", new_edges[0].id)
            # The off-line label post-pass walks edge.cells; give the component
            # cells to the first split edge so it can still find floating labels.
            new_edges[0].cells = list(cells)
            edges.extend(new_edges)
    # Post-pass: edges with no inline label may have a floating label sitting
    # beside / above / below the line (the request/response case). Grab it.
    for edge in edges:
        if edge.label:
            continue
        edge.label = _offline_label(g, consumed, edge)
    return edges


# How far to reach perpendicular to a line when hunting a floating label.
# Reaching sideways from a *vertical* line is safe — box columns are far apart
# (median gap ~18 cols), and the common "word··│" layout puts the label up to 3
# columns away. Reaching up/down from a *horizontal* line must stay tight (1
# row): rows are packed, and a larger reach grabs a parallel line's inline label
# (e.g. a fan-out branch stealing the trunk label one row above).
_OFFLINE_SIDE_REACH = 3
_OFFLINE_VERT_REACH = 1


def _offline_label(g: Grid, consumed, edge: Edge) -> str:
    """Find a floating label sitting beside / above / below an edge's line.

    The reach is directional (see the constants above) and no column-overlap
    guard is applied — a label beside a vertical line lies entirely off to one
    side of that line's column, so an overlap guard would reject every one.
    """
    if not edge.cells:
        return ""
    words: list[tuple[int, int, str]] = []
    seen_starts: set[tuple[int, int]] = set()

    def is_text(r: int, c: int) -> bool:
        if not g.in_bounds(r, c) or consumed[r][c] is not None:
            return False
        return g.at(r, c) != " " and not is_edge_glyph(g, r, c)

    for r, c in edge.cells:
        conns = connects(g.at(r, c))
        perp = []
        if "U" in conns or "D" in conns:      # vertical run → look sideways, far
            for d in range(1, _OFFLINE_SIDE_REACH + 1):
                perp += [(r, c - d), (r, c + d)]
        if "L" in conns or "R" in conns:      # horizontal run → look up/down, tight
            for d in range(1, _OFFLINE_VERT_REACH + 1):
                perp += [(r - d, c), (r + d, c)]
        for pr, pc in perp:
            if not is_text(pr, pc):
                continue
            cs = pc
            while is_text(pr, cs - 1):
                cs -= 1
            ce = pc
            while is_text(pr, ce + 1):
                ce += 1
            if (pr, cs) in seen_starts:
                continue
            seen_starts.add((pr, cs))
            word = "".join(g.at(pr, x) for x in range(cs, ce + 1)).strip()
            if word:
                words.append((pr, cs, word))
                for x in range(cs, ce + 1):
                    consumed[pr][x] = ("edge-label", edge.id)

    words.sort()
    return " ".join(w for _, _, w in words)


def _bfs_edge(g: Grid, consumed, r0: int, c0: int):
    """Walk connected line/arrow glyphs; bridge label gaps along the same axis."""
    visited: set[tuple[int, int]] = set()
    cells: list[tuple[int, int]] = []
    label_runs: list[tuple[str, list[tuple[int, int]]]] = []
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
        if not is_edge_glyph(g, r, c):
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
            if is_edge_glyph(g, nr, nc) and OPP[d] in connects(nch):
                stack.append((nr, nc))
                continue
            # gap-bridging across labels along same axis
            bridged = _look_ahead_bridge(g, consumed, r, c, d)
            if bridged is not None:
                text, end_cell, text_cells = bridged
                if text:
                    label_runs.append((text, text_cells))
                stack.append(end_cell)
    # A gap is bridged from both endpoints, yielding the same label twice;
    # collapse duplicates while preserving first-seen order.
    seen: set[str] = set()
    kept = [(t, tc) for t, tc in label_runs if not (t in seen or seen.add(t))]
    label = " ".join(t for t, _ in kept)
    label_cells = [cell for _, tc in kept for cell in tc]
    if label_cells:
        lr = sum(r for r, _ in label_cells) / len(label_cells)
        lc = sum(c for _, c in label_cells) / len(label_cells)
        label_pos = (lr, lc)
    else:
        label_pos = None
    return cells, label, label_pos


def _look_ahead_bridge(g: Grid, consumed, r: int, c: int, d: str):
    """If a label interrupts a line, look ahead for a connecting glyph.

    Returns (text, end_cell, text_cells) where text_cells are the grid
    positions of the non-blank label characters (for later placement).
    """
    dr, dc = DIRS[d]
    text_chars: list[str] = []
    text_cells: list[tuple[int, int]] = []
    nr, nc = r + dr, c + dc
    steps = 0
    while g.in_bounds(nr, nc) and steps < LABEL_LOOKAHEAD:
        if consumed[nr][nc] is not None:
            return None
        ch = g.at(nr, nc)
        # A letter inside the label (the 'v' in "event") is not a glyph that
        # should terminate the bridge — only real line/arrow glyphs do.
        if is_edge_glyph(g, nr, nc):
            if OPP[d] in connects(ch) and any(t.strip() for t in text_chars):
                # When walking left/up the chars are collected in reverse
                # reading order; flip them so the label reads correctly.
                ordered = text_chars if d in ("R", "D") else list(reversed(text_chars))
                return ("".join(ordered).strip(), (nr, nc), text_cells)
            return None
        text_chars.append(ch)
        if ch != " ":
            text_cells.append((nr, nc))
        nr += dr
        nc += dc
        steps += 1
    return None


def _node_at(nodes: list[Node], r: int, c: int, tol: int = 0) -> Optional[Node]:
    """Smallest box whose *border* passes through (r,c). An edge touches a node
    at its perimeter, so a line merely crossing a container's interior must not
    register the container as a touchpoint — only a cell on the box's border (or
    within ``tol`` of it, for column-drift) counts. With nested boxes a border
    cell can be shared; prefer the smallest-area match (the innermost box). For
    non-nested diagrams the line abuts exactly one border, so this is equivalent
    to the old bbox test."""
    best = None
    best_area = None
    for n in nodes:
        if not (n.top - tol <= r <= n.bottom + tol
                and n.left - tol <= c <= n.right + tol):
            continue
        on_border = (abs(r - n.top) <= tol or abs(r - n.bottom) <= tol
                     or abs(c - n.left) <= tol or abs(c - n.right) <= tol)
        if not on_border:
            continue
        area = (n.bottom - n.top) * (n.right - n.left)
        if best_area is None or area < best_area:
            best, best_area = n, area
    return best


def _node_center(n: Node) -> tuple[float, float]:
    return ((n.top + n.bottom) / 2, (n.left + n.right) / 2)


def _dist(a: tuple[float, float], b: tuple[float, float]) -> float:
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2


def _build_edges(
    g: Grid,
    eid_start: int,
    cells,
    label: str,
    label_pos: Optional[tuple[float, float]],
    nodes: list[Node],
) -> list[Edge]:
    """Split one connected line component into the edges it actually encodes.

    A component can fan out (one source → many arrowheads) or fan in (many
    sources → one arrowhead). We collect every node touchpoint, mark it as a
    sink (an arrowhead points into the node) or a source (a plain line leaves
    it), then pair them up rather than collapsing to a single edge.
    """
    touch: dict[int, bool] = {}  # node_id -> arrow points into it
    for r, c in cells:
        ch = g.at(r, c)
        ad = arrow_dir(ch)
        for d in connects(ch):
            dr, dc = DIRS[d]
            nr, nc = r + dr, c + dc
            # Exact match first; fall back to ±1 to absorb column drift, where
            # a box's wall on this row sits one column off its detected bbox.
            n = _node_at(nodes, nr, nc) or _node_at(nodes, nr, nc, tol=1)
            if n is None:
                continue
            touch[n.id] = touch.get(n.id, False) or (ad == d)
    if len(touch) < 2:
        return []

    sinks = [nid for nid, arr in touch.items() if arr]
    sources = [nid for nid, arr in touch.items() if not arr]
    center = {nid: _node_center(_node_by_id(nodes, nid)) for nid in touch}

    # triples: (src, dst, arrow_dst, arrow_src)
    triples: list[tuple[int, int, bool, bool]] = []
    if sources and sinks:
        if len(sources) == 1:                       # fan-out: one src → each sink
            s = sources[0]
            triples = [(s, t, True, False) for t in sinks]
        elif len(sinks) == 1:                       # fan-in: each src → one sink
            t = sinks[0]
            triples = [(s, t, True, False) for s in sources]
        else:                                       # M×N: pair on proximity
            used = set()
            for t in sinks:
                s = min(sources, key=lambda s: _dist(center[s], center[t]))
                triples.append((s, t, True, False))
                used.add(s)
            for s in sources:
                if s not in used:
                    t = min(sinks, key=lambda t: _dist(center[s], center[t]))
                    triples.append((s, t, True, False))
    elif sinks:                                     # arrowheads only → bidirectional
        h = sinks[0]
        triples = [(h, t, True, True) for t in sinks[1:]]
    else:                                           # no arrows → undirected
        h = sources[0]
        triples = [(h, t, False, False) for t in sources[1:]]

    # "Nearest branch only": the shared trunk label goes on the single edge
    # whose corridor (midpoint of its two node centers) is closest to the label.
    label_idx: Optional[int] = None
    if label:
        if label_pos is not None and triples:
            def corridor_dist(tp):
                a, b = center[tp[0]], center[tp[1]]
                mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
                return _dist(mid, label_pos)
            label_idx = min(range(len(triples)), key=lambda i: corridor_dist(triples[i]))
        elif triples:
            label_idx = 0

    edges: list[Edge] = []
    for i, (s, t, arr_dst, arr_src) in enumerate(triples):
        edges.append(Edge(
            id=eid_start + i,
            src=s,
            dst=t,
            has_arrow_dst=arr_dst,
            has_arrow_src=arr_src,
            label=label if i == label_idx else "",
        ))
    return edges


def _node_by_id(nodes: list[Node], nid: int) -> Node:
    return next(n for n in nodes if n.id == nid)


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
