"""Node detection — closed rectangles, with ±1 column-drift tolerance."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .glyphs import BL_CORNERS, BR_CORNERS, H_BORDER, TL_CORNERS, TR_CORNERS, V_BORDER
from .grid import Grid


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
