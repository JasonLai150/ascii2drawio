#!/usr/bin/env python3
"""Audit parser output across all sysdesign fixtures.

For each input file:
  - count nodes / edges
  - count orphan-edge cells (line glyphs that couldn't be resolved to nodes)
  - count completely unconsumed line glyphs
  - validate XML output is well-formed
"""
from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import ascii2drawio as a2d  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
INPUTS = sorted((ROOT / "examples" / "sysdesign").glob("*.txt"))
OUT = ROOT / "out_sysdesign"


def audit(path: Path):
    text = path.read_text()
    g = a2d.Grid.from_text(text)
    consumed = [[None] * g.w for _ in range(g.h)]
    nodes = a2d.find_rectangles(g, consumed)
    edges = a2d.find_edges(g, consumed, nodes)

    orphan_cells = 0
    unclassified_line_cells = 0
    for r in range(g.h):
        for c in range(g.w):
            tag = consumed[r][c]
            if tag is None and a2d.is_edge_glyph(g, r, c):
                unclassified_line_cells += 1
            elif tag is not None and tag[0] == "orphan-edge":
                orphan_cells += 1

    edges_missing_endpoint = sum(1 for e in edges if e.src is None or e.dst is None)
    xml_path = OUT / (path.stem + ".drawio")
    xml_ok = False
    try:
        if xml_path.exists():
            ET.parse(xml_path)
            xml_ok = True
    except Exception:
        xml_ok = False

    return {
        "name": path.stem,
        "nodes": len(nodes),
        "edges": len(edges),
        "orphan_cells": orphan_cells,
        "unclassified_line_cells": unclassified_line_cells,
        "missing_endpoint": edges_missing_endpoint,
        "xml_ok": xml_ok,
    }


def main() -> int:
    rows = [audit(p) for p in INPUTS]
    headers = ["name", "nodes", "edges", "orphan", "unclass", "no_ep", "xml"]
    widths = [30, 6, 6, 7, 8, 6, 4]
    print("  ".join(h.ljust(w) for h, w in zip(headers, widths)))
    print("  ".join("-" * w for w in widths))
    totals = {"orphan_cells": 0, "unclassified_line_cells": 0, "missing_endpoint": 0}
    bad_xml = 0
    for r in rows:
        print("  ".join([
            r["name"].ljust(widths[0]),
            str(r["nodes"]).ljust(widths[1]),
            str(r["edges"]).ljust(widths[2]),
            str(r["orphan_cells"]).ljust(widths[3]),
            str(r["unclassified_line_cells"]).ljust(widths[4]),
            str(r["missing_endpoint"]).ljust(widths[5]),
            ("y" if r["xml_ok"] else "N").ljust(widths[6]),
        ]))
        for k in totals:
            totals[k] += r[k]
        if not r["xml_ok"]:
            bad_xml += 1
    print()
    print(f"total orphan-edge cells:        {totals['orphan_cells']}")
    print(f"total unclassified line cells:  {totals['unclassified_line_cells']}")
    print(f"total edges missing endpoint:   {totals['missing_endpoint']}")
    print(f"files w/ malformed xml:         {bad_xml}/{len(rows)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
