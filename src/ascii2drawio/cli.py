"""Command-line interface — a thin wrapper over convert()."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Optional

from .annotate import annotate
from .convert import convert


def _load_dotenv(path: str) -> None:
    """Load KEY=VALUE pairs from a .env file into os.environ (no-op if missing)."""
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = val
    except FileNotFoundError:
        pass


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Convert ASCII diagrams to draw.io XML.")
    p.add_argument("input", nargs="?", help="Input file (stdin if omitted)")
    p.add_argument("-o", "--output", help="Output file (stdout if omitted)")
    p.add_argument("--annotate", action="store_true",
                   help="Print colored grid showing parser classification")
    p.add_argument("--report", action="store_true",
                   help="Print summary of nodes/edges to stderr")
    p.add_argument("--llm", action="store_true",
                   help="Enable Gemini LLM repair pass for orphan regions "
                        "(requires GEMINI_API_KEY env var)")
    p.add_argument("--llm-labels", action="store_true",
                   help="Enable Gemini LLM pass to correct inaccurate/truncated "
                        "labels on detected nodes and edges (requires GEMINI_API_KEY)")
    args = p.parse_args(argv)

    text = sys.stdin.read() if args.input is None else open(args.input, encoding="utf-8").read()

    api_key = None
    if args.llm or args.llm_labels:
        # .env lives at the repo root (src/ascii2drawio/cli.py -> parents[2]).
        _load_dotenv(str(Path(__file__).resolve().parents[2] / ".env"))
        api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            sys.stderr.write(
                "Error: --llm/--llm-labels require GEMINI_API_KEY or GOOGLE_API_KEY\n"
            )
            return 1

    result = convert(
        text,
        repair=args.llm,
        labels=args.llm_labels,
        api_key=api_key,
        verbose=args.report,
    )
    nodes, edges = result.nodes, result.edges

    if args.report or args.annotate:
        sys.stderr.write(f"nodes: {len(nodes)}, edges: {len(edges)}\n")
        for n in nodes:
            sys.stderr.write(f"  n{n.id} @ ({n.top},{n.left})-({n.bottom},{n.right}): {n.label!r}\n")
        for e in edges:
            sys.stderr.write(
                f"  e{e.id}: n{e.src} -> n{e.dst} "
                f"(arrow_dst={e.has_arrow_dst}, label={e.label!r})\n"
            )

    if args.annotate:
        sys.stdout.write(annotate(result.grid, result.consumed) + "\n")
        return 0

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(result.xml)
    else:
        sys.stdout.write(result.xml)
    return 0


if __name__ == "__main__":
    sys.exit(main())
