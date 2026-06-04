"""The single library entry point shared by the CLI, the web backend, and
tests. Keep request/CLI handling out of here."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .edges import Edge, _orphan_clusters, find_edges
from .emit import emit_drawio
from .grid import Grid
from .llm import llm_label_review, llm_repair
from .nodes import Node, find_rectangles


@dataclass
class ConvertResult:
    """Everything a caller (CLI, web API, tests) needs from one conversion."""
    xml: str
    nodes: list[Node]
    edges: list[Edge]
    orphan_clusters: int
    report: dict
    grid: Grid
    consumed: list


def convert(
    text: str,
    *,
    repair: bool = False,
    labels: bool = False,
    api_key: Optional[str] = None,
    verbose: bool = False,
) -> ConvertResult:
    """Parse an ASCII diagram into draw.io XML.

    Deterministic by default. ``repair`` runs the Gemini orphan-repair pass and
    ``labels`` runs the Gemini label-correction pass; both are no-ops without an
    ``api_key``.
    """
    g = Grid.from_text(text)
    consumed = [[None] * g.w for _ in range(g.h)]
    nodes = find_rectangles(g, consumed)
    edges = find_edges(g, consumed, nodes)

    if repair and api_key:
        nodes, edges = llm_repair(g, consumed, nodes, edges, api_key, verbose=verbose)
    if labels and api_key:
        nodes, edges = llm_label_review(g, nodes, edges, api_key, verbose=verbose)

    clusters = len(_orphan_clusters(consumed, g))
    report = {"nodes": len(nodes), "edges": len(edges), "orphan_clusters": clusters}
    return ConvertResult(
        xml=emit_drawio(nodes, edges),
        nodes=nodes,
        edges=edges,
        orphan_clusters=clusters,
        report=report,
        grid=g,
        consumed=consumed,
    )
