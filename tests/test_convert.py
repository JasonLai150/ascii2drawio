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
    assert r.report == {"nodes": 3, "edges": 3, "orphan_clusters": 0}, r.report
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
