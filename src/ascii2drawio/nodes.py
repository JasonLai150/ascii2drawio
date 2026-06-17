"""Node detection — closed rectangles, with ±1 column-drift tolerance."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .glyphs import (
    BL_CORNERS,
    BR_CORNERS,
    H_BORDER,
    TL_CORNERS,
    TR_CORNERS,
    V_BORDER,
    arrow_dir,
    connects,
    is_edge_glyph,
)
from .grid import Grid


@dataclass
class Node:
    id: int
    top: int
    left: int
    bottom: int
    right: int
    label: str
    borderless: bool = False  # loose-mode text node (no box outline)

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


# Loose-mode tuning: bridge at most this many spaces inside a text block, so
# "Load Balancer" (one internal space) reads as one node while two diagram
# elements separated by a wider gutter stay distinct.
_TEXT_GAP = 1


def find_text_nodes(g: Grid, consumed, start_id: int) -> list[Node]:
    """Loose mode: promote borderless text (no box outline) into nodes when an
    edge line terminates at it.

    Runs after ``find_rectangles`` (so boxes and their labels are already
    consumed) and before ``find_edges``. The edge-adjacency grounding is what
    keeps titles, legends and inline edge labels from being mistaken for nodes:
    a real node sits at the *end* of a line, so only a line/arrow that connects
    *toward* the text counts. A label beside a vertical line (the ``│`` connects
    up/down, not sideways toward it) and a pure pass-through inline label
    (``──text──``) are both rejected.
    """

    def is_text(r: int, c: int) -> bool:
        if not g.in_bounds(r, c) or consumed[r][c] is not None:
            return False
        ch = g.at(r, c)
        return ch != " " and not is_edge_glyph(g, r, c)

    # Phase 1: group unconsumed text into blocks (no marking yet, so the flood
    # sees a stable grid). Phase 2: ground + promote.
    seen: set[tuple[int, int]] = set()
    blocks: list[list[tuple[int, int]]] = []
    for r in range(g.h):
        for c in range(g.w):
            if (r, c) in seen or not is_text(r, c):
                continue
            blocks.append(_flood_text(g, is_text, r, c, seen))

    nodes: list[Node] = []
    for block in blocks:
        node = _try_text_node(g, consumed, block, start_id + len(nodes))
        if node is not None:
            _mark_node(consumed, node)
            nodes.append(node)
    return nodes


def _flood_text(g: Grid, is_text, r0: int, c0: int, seen) -> list[tuple[int, int]]:
    """Connected run of text cells, bridging single-space horizontal gaps."""
    stack = [(r0, c0)]
    block: list[tuple[int, int]] = []
    while stack:
        r, c = stack.pop()
        if (r, c) in seen or not is_text(r, c):
            continue
        seen.add((r, c))
        block.append((r, c))
        for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            stack.append((r + dr, c + dc))
        # Hop a narrow gutter of spaces to keep multi-word labels together.
        for gap in range(2, _TEXT_GAP + 2):
            if all(g.at(r, c + k) == " " for k in range(1, gap)) and is_text(r, c + gap):
                stack.append((r, c + gap))
            if all(g.at(r, c - k) == " " for k in range(1, gap)) and is_text(r, c - gap):
                stack.append((r, c - gap))
    return block


# Spaces tolerated between a borderless node's text and its connector line.
# Diagrams conventionally write "Client ──> Gateway" with a gutter, so the
# line never sits flush against the text; without this the most natural form
# grounds nothing. One space is enough — wider risks grabbing a neighbor's line.
_GROUND_GAP = 1


def _edge_into(
    g: Grid, consumed, ar: int, ac: int, into: str, dr: int, dc: int
) -> tuple[bool, bool]:
    """Scan outward from (ar,ac) in step (dr,dc) across up to ``_GROUND_GAP``
    spaces for an unconsumed edge glyph connecting in direction ``into`` (back
    toward the block). Returns (grounds, points_arrowhead_in)."""
    for _ in range(_GROUND_GAP + 1):
        if not g.in_bounds(ar, ac) or consumed[ar][ac] is not None:
            return (False, False)
        ch = g.at(ar, ac)
        if ch == " ":  # hop the gutter between text and its line
            ar, ac = ar + dr, ac + dc
            continue
        if not is_edge_glyph(g, ar, ac):
            return (False, False)
        if into not in connects(ch):
            return (False, False)
        return (True, arrow_dir(ch) == into)
    return (False, False)


def _try_text_node(g: Grid, consumed, block, node_id: int) -> Optional[Node]:
    if not block:
        return None
    rows = [r for r, _ in block]
    cols = [c for _, c in block]
    top, bottom, left, right = min(rows), max(rows), min(cols), max(cols)

    # Reconstruct the label row by row (slicing the grid keeps internal spaces).
    lines: list[str] = []
    for rr in range(top, bottom + 1):
        rcols = [c for r, c in block if r == rr]
        if not rcols:
            continue
        line = "".join(g.at(rr, cc) for cc in range(min(rcols), max(rcols) + 1)).strip()
        if line:
            lines.append(line)
    label = " ".join(lines)
    if not any(ch.isalnum() for ch in label):
        return None  # stray punctuation, not a node

    # Grounding: which sides have a line/arrow connecting toward the block?
    sides: set[str] = set()
    arrow_in = False
    checks: list[tuple[str, tuple[bool, bool]]] = []
    for cc in range(left, right + 1):
        checks.append(("top", _edge_into(g, consumed, top - 1, cc, "D", -1, 0)))
        checks.append(("bottom", _edge_into(g, consumed, bottom + 1, cc, "U", 1, 0)))
    for rr in range(top, bottom + 1):
        checks.append(("left", _edge_into(g, consumed, rr, left - 1, "R", 0, -1)))
        checks.append(("right", _edge_into(g, consumed, rr, right + 1, "L", 0, 1)))
    for side, (grounds, is_arrow) in checks:
        if grounds:
            sides.add(side)
            arrow_in = arrow_in or is_arrow
    if not sides:
        return None
    # Collinear lines on opposite sides with no terminating arrowhead = a line
    # passing through an inline label; leave it for edge gap-bridging.
    if not arrow_in and sides in ({"left", "right"}, {"top", "bottom"}):
        return None

    return Node(
        id=node_id, top=top, left=left, bottom=bottom, right=right,
        label=label, borderless=True,
    )
