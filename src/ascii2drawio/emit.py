"""mxGraph XML emit. Nodes are pinned at (col×CHAR_W, row×CHAR_H) so the
imported diagram preserves the ASCII layout instead of being re-laid-out."""
from __future__ import annotations

import html

from .edges import Edge
from .nodes import Node

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
        style = (
            "text;html=1;whiteSpace=wrap;align=center;verticalAlign=middle;"
            "strokeColor=none;fillColor=none;"
            if n.borderless
            else "rounded=0;whiteSpace=wrap;html=1;fillColor=#ffffff;strokeColor=#000000;"
        )
        cells_xml.append(
            f'        <mxCell id="n{n.id}" value="{label}" '
            f'style="{style}" '
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
