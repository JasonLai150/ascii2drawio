#!/usr/bin/env python3
"""Phase-0 behavior pins for the convert() library entry point.

Self-contained — run directly: `python3 tests/test_convert.py` (no pytest
needed). Also discoverable by pytest if installed.
"""
from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import ascii2drawio as a2d  # noqa: E402


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_simple_counts_and_valid_xml():
    r = a2d.convert(_read("examples/simple.txt"))
    assert r.report == {"nodes": 3, "edges": 3, "orphan_clusters": 0, "flags": 0}, r.report
    ET.fromstring(r.xml)  # well-formed


def test_labeled_floating_labels():
    r = a2d.convert(_read("examples/labeled.txt"))
    labels = {e.label for e in r.edges}
    assert "request" in labels and "response" in labels, labels


def test_url_shortener_fanout_and_clean_labels():
    r = a2d.convert(_read("examples/sysdesign/01-url-shortener.txt"))
    assert r.report["edges"] == 7, r.report          # App Server fan-out split
    labels = {e.label for e in r.edges}
    assert {"HTTPS", "route", "lookup", "async"} <= labels, labels
    assert "SPTTH" not in " ".join(labels)            # no reversed-dup regression


_LOOSE_DIAGRAM = """\
Client
  │
  ↓
┌───┐
│ A │
└───┘
  │ note
  ↓
┌───┐
│ B │
└───┘"""


def test_loose_mode_recovers_borderless_nodes():
    # Strict: only the two closed rectangles.
    strict = a2d.convert(_LOOSE_DIAGRAM)
    assert strict.report["nodes"] == 2, strict.report

    # Loose: the text-only "Client" becomes a node and connects into A.
    loose = a2d.convert(_LOOSE_DIAGRAM, loose=True)
    assert loose.report["nodes"] == 3, loose.report
    borderless = {n.label for n in loose.nodes if n.borderless}
    assert borderless == {"Client"}, borderless
    assert any(n.label == "Client" and n.borderless for n in loose.nodes)
    ET.fromstring(loose.xml)  # well-formed

    # "note" sits *beside* a vertical line (the ‖ connects up/down, not toward
    # it): it must stay an edge label, never get promoted to a node.
    assert "note" not in borderless
    assert "note" in {e.label for e in loose.edges}


def test_loose_mode_horizontal_chain_with_gutters():
    # The natural form puts a space between text and the line ("Client ──> X").
    # Grounding must hop that single-space gutter, or the chain recovers nothing.
    diagram = "Client ──────> Gateway ──────> Service"
    loose = a2d.convert(diagram, loose=True)
    labels = [n.label for n in loose.nodes]
    assert labels == ["Client", "Gateway", "Service"], labels
    assert all(n.borderless for n in loose.nodes)
    assert loose.report["edges"] == 2, loose.report
    ET.fromstring(loose.xml)


def test_arrowhead_fused_to_text_not_glued():
    # No gutter: "Client──>Gateway". The arrowhead must read as an arrow (not
    # get absorbed into the node text / edge label) when its line is on one
    # side and text on the other.
    loose = a2d.convert("Client──────>Gateway──────>Service", loose=True)
    assert [n.label for n in loose.nodes] == ["Client", "Gateway", "Service"]
    assert all(e.label == "" for e in loose.edges)  # no ">Gateway" glue
    # ...but letters that merely look like arrows stay text:
    g = a2d.Grid.from_text("event")
    assert not a2d.is_edge_glyph(g, 0, 2)  # the 'v'
    g = a2d.Grid.from_text("a>b")
    assert not a2d.is_edge_glyph(g, 0, 1)  # comparison, no line attached


def test_nested_boxes_containment_and_clean_labels():
    # One container holding two boxes: parents link, container label is clean.
    r = a2d.convert(_read("examples/nested/containers.txt"))
    assert r.report["nodes"] == 3, r.report
    by_label = {n.label: n for n in r.nodes}
    assert set(by_label) == {"Cluster", "Web", "API"}, by_label
    assert by_label["Cluster"].parent is None
    assert by_label["Web"].parent == by_label["Cluster"].id
    assert by_label["API"].parent == by_label["Cluster"].id
    # The container must be emitted as a draw.io container, children parented to it.
    assert 'container=1' in r.xml
    assert f'parent="n{by_label["Cluster"].id}"' in r.xml
    ET.fromstring(r.xml)


def test_nested_two_levels_with_sibling_edge():
    # Region > Cluster > {Web, API}, and an edge between the two innermost boxes
    # that lives inside the container interior must still be traced.
    r = a2d.convert(_read("examples/nested/two-levels-edge.txt"))
    by_label = {n.label: n for n in r.nodes}
    assert set(by_label) == {"Region", "Cluster", "Web", "API"}, by_label
    assert by_label["Cluster"].parent == by_label["Region"].id
    assert by_label["Web"].parent == by_label["Cluster"].id
    # exactly the Web -> API edge, no phantom container touchpoints
    assert r.report["edges"] == 1, r.report
    e = r.edges[0]
    assert (e.src, e.dst) == (by_label["Web"].id, by_label["API"].id)
    assert e.has_arrow_dst
    ET.fromstring(r.xml)


def test_multiple_arrows_from_one_box():
    # Arrows leaving all four sides of one hub — each a distinct edge sourced
    # from the hub (the existing source/sink pairing already handles this).
    r = a2d.convert(_read("examples/multi-arrow/four-sides.txt"))
    by_label = {n.label: n for n in r.nodes}
    hub = by_label["Hub"].id
    assert r.report["edges"] == 4, r.report
    assert all(e.src == hub and e.has_arrow_dst for e in r.edges)
    assert {e.dst for e in r.edges} == {
        by_label[l].id for l in ("Up", "Down", "Left", "Right")
    }


def test_labeled_fanout_branches_keep_their_labels():
    r = a2d.convert(_read("examples/multi-arrow/labeled-fanout.txt"))
    by_label = {n.label: n for n in r.nodes}
    a = by_label["A"].id
    pairs = {(e.dst, e.label) for e in r.edges if e.src == a}
    assert (by_label["B"].id, "read") in pairs, pairs
    assert (by_label["C"].id, "write") in pairs, pairs


def test_drifted_walls_recovered_via_edges():
    # Malformed boxes whose middle-row walls are shifted several columns but
    # whose top+bottom edges align: recovered by the edge-based close, with a
    # clean label and the inbound arrow attached.
    cases = [
        ("examples/sysdesign/16-online-auction.txt", "Settlement"),
        ("examples/sysdesign/18-log-aggregation.txt", "Kibana UI"),
        ("examples/sysdesign/19-api-gateway.txt", "Quota Store"),
    ]
    for path, label in cases:
        r = a2d.convert(_read(path))
        node = next((n for n in r.nodes if n.label == label), None)
        assert node is not None, f"{label} not detected in {path}"
        assert any(e.dst == node.id for e in r.edges), f"{label} has no inbound edge"


def test_ir_flags_surface_unconsumed_text():
    import json

    # A clean diagram: no leftover text, no flags.
    clean = a2d.convert(_read("examples/simple.txt"))
    assert clean.ir.flags == [], clean.ir.flags
    assert clean.report["flags"] == 0

    # Strict mode on a borderless diagram leaves the text unaccounted: each run
    # becomes a flag — free-floating text -> ungrounded_text (missed node),
    # edge-hugging text -> truncation_suspect. The IR is the reconciler trigger.
    r = a2d.convert(_read("examples/borderless/mixed-h-v.txt"))
    kinds = {f.kind for f in r.ir.flags}
    assert "ungrounded_text" in kinds, r.ir.flags
    texts = {t.text for t in r.ir.text_runs}
    assert {"Client", "Service"} <= texts, texts
    # every flag is grid-grounded (inside the diagram bounds)
    for f in r.ir.flags:
        assert 0 <= f.top <= f.bottom < r.ir.height
        assert 0 <= f.left <= f.right < r.ir.width
    # IR serializes to JSON (the reconciler/--ir dump path)
    json.dumps(r.ir.to_dict())


def test_convert_result_shape():
    r = a2d.convert(_read("examples/ascii.txt"))
    assert isinstance(r, a2d.ConvertResult)
    assert r.grid is not None and r.consumed is not None
    assert r.xml.startswith("<?xml")


def test_hallucination_guards_are_pure_and_strict():
    from ascii2drawio import llm
    g = a2d.Grid.from_text(_read("examples/sysdesign/01-url-shortener.txt"))
    # invented node over an empty region -> rejected
    bad = {"type": "node", "top": 3, "left": 2, "bottom": 5, "right": 15, "label": "Kafka"}
    assert not llm._validate_repair(bad, {0, 1, 2}, g)
    # invented label text -> rejected; real text -> accepted
    assert not llm._label_grounded(g, "asynchronous elasticsearch")
    assert llm._label_grounded(g, "lookup")


def test_llm_label_review_applies_and_rejects(monkeypatch_call):
    from ascii2drawio import llm
    g = a2d.Grid.from_text(_read("examples/sysdesign/20-saga-microservices.txt"))
    consumed = [[None] * g.w for _ in range(g.h)]
    nodes = a2d.find_rectangles(g, consumed)
    edges = a2d.find_edges(g, consumed, nodes)
    e8 = next(e for e in edges if e.id == 8)
    assert e8.label == "on"  # truncated from "on fail"

    monkeypatch_call(lambda *a, **k: {"repairs": [
        {"type": "label", "target": "edge", "id": 8, "label": "on fail"},      # valid
        {"type": "label", "target": "edge", "id": 0, "label": "asynchronous"},  # hallucinated
        {"type": "label", "target": "edge", "id": 999, "label": "order"},       # bad id
    ]})
    llm.llm_label_review(g, nodes, edges, api_key="x")
    assert e8.label == "on fail"
    assert next(e for e in edges if e.id == 0).label == "order"  # unchanged


# --- tiny runner so this works without pytest ---

def _run():
    from ascii2drawio import llm
    passed = 0
    plain = [
        test_simple_counts_and_valid_xml,
        test_labeled_floating_labels,
        test_url_shortener_fanout_and_clean_labels,
        test_loose_mode_recovers_borderless_nodes,
        test_loose_mode_horizontal_chain_with_gutters,
        test_arrowhead_fused_to_text_not_glued,
        test_nested_boxes_containment_and_clean_labels,
        test_nested_two_levels_with_sibling_edge,
        test_multiple_arrows_from_one_box,
        test_labeled_fanout_branches_keep_their_labels,
        test_drifted_walls_recovered_via_edges,
        test_ir_flags_surface_unconsumed_text,
        test_convert_result_shape,
        test_hallucination_guards_are_pure_and_strict,
    ]
    for fn in plain:
        fn()
        passed += 1
        print(f"  ok  {fn.__name__}")

    # stub the network boundary for the label-review test
    def monkeypatch_call(fake):
        llm._call_gemini = fake
    test_llm_label_review_applies_and_rejects(monkeypatch_call)
    passed += 1
    print(f"  ok  test_llm_label_review_applies_and_rejects")
    print(f"\n{passed} passed")


if __name__ == "__main__":
    _run()
