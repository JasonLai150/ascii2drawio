#!/usr/bin/env python3
"""For each fixture with orphan edges, find why rectangles failed to close.

Walks all top-left corner candidates that did NOT become nodes and reports
which step of `_try_close_rect` rejected them.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import ascii2drawio as a2d  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
INPUTS = sorted((ROOT / "examples" / "sysdesign").glob("*.txt"))


def diagnose_corner(g: a2d.Grid, r0: int, c0: int) -> str:
    """Mirror _try_close_rect but report failure reason."""
    c = c0 + 1
    while c < g.w and g.at(r0, c) in a2d.H_LINE:
        c += 1
    if c >= g.w:
        return "top edge ran off grid"
    if g.at(r0, c) not in a2d.TR_CORNERS:
        return f"top edge hit non-corner '{g.at(r0, c)}' at ({r0},{c})"
    if c - c0 < 2:
        return "rectangle too narrow"
    c1 = c
    r = r0 + 1
    while r < g.h and g.at(r, c1) in a2d.V_LINE:
        r += 1
    if r >= g.h:
        return f"right edge from ({r0},{c1}) ran off grid"
    if g.at(r, c1) not in a2d.BR_CORNERS:
        return f"right edge from ({r0},{c1}) hit non-corner '{g.at(r, c1)}' at ({r},{c1})"
    if r - r0 < 2:
        return "rectangle too short"
    r1 = r
    for cc in range(c0 + 1, c1):
        if g.at(r1, cc) not in a2d.H_LINE:
            return f"bottom edge missing line at ({r1},{cc}) — got '{g.at(r1, cc)}'"
    if g.at(r1, c0) not in a2d.BL_CORNERS:
        return f"bottom-left corner missing at ({r1},{c0}) — got '{g.at(r1, c0)}'"
    for rr in range(r0 + 1, r1):
        if g.at(rr, c0) not in a2d.V_LINE:
            return f"left edge missing wall at ({rr},{c0}) — got '{g.at(rr, c0)}'"
    return "OK (would close)"


def main():
    for path in INPUTS:
        text = path.read_text()
        g = a2d.Grid.from_text(text)
        consumed = [[None] * g.w for _ in range(g.h)]
        nodes = a2d.find_rectangles(g, consumed)
        # find unconsumed top-left corners
        failures = []
        for r in range(g.h):
            for c in range(g.w):
                if consumed[r][c] is not None:
                    continue
                if g.at(r, c) in a2d.TL_CORNERS:
                    failures.append((r, c, diagnose_corner(g, r, c)))
        if not failures:
            continue
        print(f"\n=== {path.stem}  ({len(failures)} unclaimed corners) ===")
        for r, c, reason in failures:
            print(f"  ({r:3d},{c:3d}): {reason}")


if __name__ == "__main__":
    main()
