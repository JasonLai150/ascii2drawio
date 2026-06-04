"""Debug view: re-print the grid with cells colorized by classification."""
from __future__ import annotations

from .grid import Grid

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
