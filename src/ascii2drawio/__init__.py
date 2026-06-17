"""ascii2drawio — convert ASCII/Unicode box-drawing diagrams into draw.io XML.

Public API:
    convert(text, *, repair=False, labels=False, api_key=None) -> ConvertResult

The package is split by concern (glyphs, grid, nodes, edges, emit, annotate,
llm, convert, cli); this module re-exports the names callers and the test/audit
scripts rely on so ``import ascii2drawio as a2d; a2d.Grid`` keeps working.
"""
from __future__ import annotations

from .glyphs import (
    ARROW_D, ARROW_L, ARROW_R, ARROW_U, ARROWS, ASCII_ARROWS,
    BL_CORNERS, BR_CORNERS, CORNERS, DIRS, H_BORDER, H_LINE, LINE_CHARS, OPP,
    PLUS, TEES, TL_CORNERS, TR_CORNERS, V_BORDER, V_LINE,
    arrow_dir, connects, is_edge_glyph,
)
from .grid import Grid
from .nodes import Node, find_rectangles, find_text_nodes
from .edges import Edge, LABEL_LOOKAHEAD, find_edges
from .emit import CHAR_H, CHAR_W, emit_drawio
from .annotate import ANSI, annotate
from .ir import IR, AmbiguityFlag, TextRun, build_ir
from .llm import llm_label_review, llm_reconcile, llm_repair
from .convert import ConvertResult, convert
from .cli import main

__all__ = [
    "convert", "ConvertResult", "main",
    "Grid", "Node", "Edge",
    "find_rectangles", "find_text_nodes", "find_edges", "emit_drawio", "annotate",
    "IR", "AmbiguityFlag", "TextRun", "build_ir",
    "llm_repair", "llm_label_review", "llm_reconcile",
    "connects", "arrow_dir", "is_edge_glyph",
    "H_LINE", "V_LINE", "CORNERS", "TEES", "PLUS", "LINE_CHARS", "ARROWS",
    "ASCII_ARROWS", "ARROW_R", "ARROW_L", "ARROW_U", "ARROW_D",
    "DIRS", "OPP", "TL_CORNERS", "TR_CORNERS", "BL_CORNERS", "BR_CORNERS",
    "H_BORDER", "V_BORDER", "CHAR_W", "CHAR_H", "LABEL_LOOKAHEAD",
]
