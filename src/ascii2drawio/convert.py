"""The single library entry point shared by the CLI, the web backend, and
tests. Keep request/CLI handling out of here."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .edges import Edge, _orphan_clusters, find_edges
from .emit import emit_drawio
from .grid import Grid
from .ir import IR, build_ir
from .llm import llm_reconcile
from .nodes import Node, find_rectangles, find_text_nodes


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
    ir: IR


def convert(
    text: str,
    *,
    loose: bool = False,
    repair: bool = False,
    labels: bool = False,
    api_key: Optional[str] = None,
    verbose: bool = False,
) -> ConvertResult:
    """Parse an ASCII diagram into draw.io XML.

    Deterministic by default. ``loose`` additionally recovers borderless
    (text-only) nodes that an edge terminates at. ``repair`` and/or ``labels``
    enable the single LLM **reconciliation** pass over the deterministic IR
    (it adds missed nodes/edges, relabels, and reparents in one grid-grounded,
    validator-gated call); it is a no-op without an ``api_key`` or when the
    parser raised no ambiguity flags.
    """
    g = Grid.from_text(text)
    consumed = [[None] * g.w for _ in range(g.h)]
    nodes = find_rectangles(g, consumed)
    if loose:
        nodes = nodes + find_text_nodes(g, consumed, len(nodes))
    edges = find_edges(g, consumed, nodes)

    # The deterministic IR (with ambiguity flags) is the reconciler's input.
    ir = build_ir(g, consumed, nodes, edges)
    if (repair or labels) and api_key:
        nodes, edges = llm_reconcile(g, ir, api_key, verbose=verbose)

    clusters = len(_orphan_clusters(consumed, g))
    report = {
        "nodes": len(nodes),
        "edges": len(edges),
        "orphan_clusters": clusters,
        "flags": len(ir.flags),
    }
    return ConvertResult(
        xml=emit_drawio(nodes, edges),
        nodes=nodes,
        edges=edges,
        orphan_clusters=clusters,
        report=report,
        grid=g,
        consumed=consumed,
        ir=ir,
    )
