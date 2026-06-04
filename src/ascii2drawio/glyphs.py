"""Glyph classification: which characters are lines/corners/tees/arrows and how
they connect. The lowest layer — everything else depends on this."""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .grid import Grid

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
# ASCII arrowheads double as ordinary letters/punctuation; inside label text
# (e.g. the 'v' in "event") they must be read as text, not as arrows.
ASCII_ARROWS = set("v^<>")
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


def is_edge_glyph(g: "Grid", r: int, c: int) -> bool:
    """Whether a cell participates in edge tracing.

    Line glyphs always count. Unicode arrows always count. ASCII arrowheads
    (v ^ < >) double as letters, so they only count when NOT flanked by
    alphanumerics — i.e. the 'v' in "event"/"valid" is text, not an arrow.
    """
    ch = g.at(r, c)
    if ch in LINE_CHARS:
        return True
    if ch in ARROWS:
        if ch in ASCII_ARROWS and (g.at(r, c - 1).isalnum() or g.at(r, c + 1).isalnum()):
            return False
        return True
    return False
