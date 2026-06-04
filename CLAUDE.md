# ascii2drawio

A tool that converts ASCII / Unicode box-drawing diagrams (the kind LLMs commonly emit) into draw.io-importable XML. Single-file Python parser with a deterministic core plus optional Gemini-backed repair/label passes. **Release 1 is done**; next step is migrating this MVP to a web app (plan below).

## Goal

Take input like:

```
┌─────────┐         ┌─────────┐
│  Alpha  │────────>│  Beta   │
└─────────┘         └─────────┘
     │                   │
     │                   ↓
     │              ┌─────────┐
     └─────────────>│  Gamma  │
                    └─────────┘
```

…and produce a `.drawio` file that opens in draw.io / diagrams.net with the boxes in the same spatial positions the LLM chose, edges connecting them, arrowheads in the right direction, and edge labels attached.

## Why mxGraph XML and not Mermaid

Mermaid would be cheaper to emit (draw.io supports Mermaid import natively), but the whole point of preserving an ASCII diagram is preserving the LLM's chosen layout. Mermaid recomputes layout. mxGraph XML lets us pin each node at `(col × CHAR_W, row × CHAR_H)` so the imported diagram looks like the ASCII source, not a re-laid-out version.

## Approach: deterministic primary, LLM repair fallback

Decided early against pure-LLM extraction (slow, costly, non-reproducible) and against actor-critic loops (this is a parsing task with ground truth — a critic mostly re-derives what the actor produced). The hybrid, now fully implemented:

1. **Deterministic parser** handles the ~95% — clean Unicode/ASCII boxes, traced edges (incl. fan-out/fan-in), arrowheads, inline + floating labels. Fast, free, debuggable.
2. **Confidence signal** — orphan-edge cells flag localized failures and cluster into "regions needing repair."
3. **LLM repair pass** (`--llm`) looks at flagged regions only, returns missing nodes/edges as JSON, validates against the grid, merges before XML emit.
4. **LLM label pass** (`--llm-labels`) proof-reads existing labels and corrects truncations/mistakes, grid-grounded.
5. **No critic loop.** Deterministic validators on the LLM output catch errors more cheaply than a second LLM pass.

## What's built today

`src/ascii2drawio/` — an installable Python package split by concern (was a single ~1130-line file; refactored in Phase 0 of the web migration). Pipeline: `Grid → find_rectangles → find_edges → (optional LLM passes) → emit_drawio`. The public entry point is `convert(text, *, repair=False, labels=False, api_key=None) -> ConvertResult`; `import ascii2drawio as a2d` re-exports the full surface (`a2d.Grid`, `a2d.find_edges`, …). Modules: `glyphs` · `grid` · `nodes` · `edges` (incl. `_orphan_clusters`) · `emit` · `annotate` · `llm` · `convert` · `cli`.

**Glyph classification**
- `H_LINE`/`V_LINE`/`CORNERS`/`TEES`/`PLUS`/`ARROWS` glyph sets; `H_BORDER`/`V_BORDER` accept tees as border continuation.
- `ASCII_ARROWS = {v ^ < >}` — these double as letters. `is_edge_glyph()` treats them as text when flanked by alphanumerics (the `v` in "e**v**ent"/"**v**alid"), as arrows otherwise. **This predicate is the single source of truth** for "does this cell participate in edge tracing" and is used everywhere (tracing, bridging, off-line labels, and `scripts/audit.py`).

**Node detection** — `find_rectangles` / `_try_close_rect`: closed rectangles from `┌`/`+` corners, with ±1 column-drift tolerance on walls/corners and per-row wall tracking for label extraction.

**Edge tracing** — `find_edges`:
- `_bfs_edge` flood-fills a connected line/arrow component and bridges label gaps (`── label ──`) via `_look_ahead_bridge` (which reverses text for left/up walks and reports label cell positions; dedups the gap bridged from both ends).
- `_build_edges` (note: **plural** — splits one component into multiple edges) collects every node touchpoint, classes each as a **sink** (arrowhead into it) or **source** (plain line leaves it), then pairs them: 1 source→N sinks = fan-out, N sources→1 sink = fan-in, M×N = nearest-pairing, arrows-both-ends = bidirectional, no-arrows = undirected. Endpoint resolution uses `_node_at(..., tol=1)` fallback to absorb ±1 column drift.
- A shared trunk label attaches to the **nearest branch only** (the edge whose node-center midpoint is closest to the label's grid position).
- `_offline_label` captures floating labels beside/above/below a line. **Directional reach**: 3 cols sideways from vertical lines (`_OFFLINE_SIDE_REACH`), 1 row up/down from horizontal lines (`_OFFLINE_VERT_REACH`). No column-overlap guard — a label beside a vertical line lies entirely off to one side, so a guard would reject all of them. (Empirically: this asymmetry recovers ~48 labels with 0 false positives; a uniform/wider reach steals neighbors' inline labels.)

**XML emit** — `emit_drawio`: mxGraph XML at pixel coords scaled by `CHAR_W=9`, `CHAR_H=18`.

**CLI** — `python3 ascii2drawio.py input.txt -o out.drawio`, with `--annotate` (colorized cell classification), `--report` (stderr node/edge summary), `--llm` (orphan repair), `--llm-labels` (label correction).

### LLM integration (implemented, Gemini via REST)

- **Auth/transport resolved**: direct **Gemini REST** (`gemini-2.5-flash`, `thinkingBudget: 0`) via `urllib` — no SDK dependency. Key from `GEMINI_API_KEY`/`GOOGLE_API_KEY`, loaded from a repo-root `.env` by `_load_dotenv`. (The earlier Anthropic-SDK/Bedrock/Vertex fork is moot.)
- **`llm_repair`** — clusters orphan-edge cells (`_orphan_clusters`, 8-connected), builds a localized per-cluster prompt (detected nodes + padded sub-grid), validates each repair (`_validate_repair`), merges. **One call per cluster.**
- **`llm_label_review`** — **one** call with the full (small) diagram + all node/edge labels; returns label corrections only.
- **Anti-hallucination guards (deterministic, grid-grounded):**
  - Node repairs: `_border_evidence` (≥50% of the claimed rectangle border must be real line glyphs) + `_label_evidence` (majority of label tokens must appear in the region). A real-but-missed box passes; an invented box/label is rejected.
  - Label repairs: `_label_grounded` (label text must literally appear in the grid). Stops the model inventing/paraphrasing.
- LLM passes are **blocking** (`urllib`); a web backend must run them in a threadpool (sync handler), not on the event loop.

## Self-test corpus and audit harness

`examples/` — `simple.txt`, `labeled.txt`, `ascii.txt` (hand-written), plus `spotify.txt`. `examples/sysdesign/` — 20 system-design architecture diagrams (URL shortener, rate limiter, news feed, web crawler, distributed cache, video streaming, ride sharing, payment, distributed storage, task queue, load balancer, CDN, online auction, stock trading, log aggregation, API gateway, saga microservices, etc.).

`scripts/audit.py` — runs the parser on every fixture: nodes, edges, orphan-edge cells, unclassified glyph cells, edges missing an endpoint, XML validity. (Uses `is_edge_glyph`, so it doesn't miscount letter-`v`s.)

`scripts/diagnose_orphans.py` — explains why each unclaimed top-left corner failed to close.

## Current accuracy (Release 1)

20/20 fixtures: **valid XML, 0 unclassified glyph cells, 0 edges missing endpoint, 0 splittable misses** (every orphan component now resolves ≤1 node). Orphan-edge cells: **762 → 190**, concentrated entirely in 4 files (`13`, `16`, `18`, `19`). Fan-out/fan-in edges, arrowhead directions, inline labels, and most floating labels are correct.

## Bug-fix history

### Rounds 1–2 (node detection)
Strict-mode parser; `H_BORDER`/`V_BORDER` tee handling; ±1 drift tolerance; per-row wall tracking. Orphans 762 → 389.

### Round 3 (this release — edges & labels)
1. **Doubled/reversed labels** (`HTTPS SPTTH`): gaps bridged from both ends; left/up walks read reversed. Fixed by reversing for L/U and deduping runs.
2. **Missing arrows / wrong targets**: `find_edges` collapsed a whole connected component into one edge keeping only the first two touchpoints — fan-outs lost branches and mis-targeted. Fixed by `_build_edges` (source/sink pairing).
3. **Arrow-letters in labels** (`v ^ < >`): the `v` in "event"/"valid" parsed as an arrowhead — aborted bridging and (with drift tolerance) produced phantom duplicate edges. Fixed by the `is_edge_glyph` predicate.
4. **±1 column drift in endpoint resolution**: detected boxes whose wall sat 1 col outside the bbox were unreachable by arrows. Fixed by `_node_at(tol=1)` fallback.
5. **Floating labels** (`request`/`response`, and beside-vertical labels): added directional `_offline_label`.
6. **LLM hallucination**: a repair added a box with text nowhere in the ASCII. Fixed by the grid-grounding guards.

### Remaining limitations
1. **≥2-column drift boxes go undetected** → their inbound arrow is dropped. The 4 hotspots: `13` Result Store (corners col 83 / walls col 81), `16` Settlement, `18` Kibana UI (3-col drift), `19` Quota Store. Pushing `_try_close_rect` to ±2/±3 risks false positives — these are the canonical **LLM-repair** cases (their orphan regions are exactly what `llm_repair` clusters on; the hardened node validator accepts them because real border glyphs + real label text are present).
2. **Multi-word floating labels truncate** to the word nearest the line (`hash key`→`key`, `on fail`→`on`). The **label-correction** pass (`--llm-labels`) is designed to fix these.
3. **Strict mode only** — every node must be a closed rectangle. Loose mode (label-only nodes) sketched as `--loose`, not implemented.

## Repo layout

```
ascii2drawio/
├── src/ascii2drawio/        # the package (pip-installable)
│   ├── __init__.py          # re-exports public API + back-compat names
│   ├── __main__.py          # `python -m ascii2drawio`
│   ├── glyphs.py            # glyph sets, connects/arrow_dir/is_edge_glyph
│   ├── grid.py              # Grid
│   ├── nodes.py             # Node, find_rectangles, _try_close_rect
│   ├── edges.py             # Edge, find_edges, _build_edges, _offline_label, _orphan_clusters
│   ├── emit.py              # emit_drawio (CHAR_W/CHAR_H)
│   ├── annotate.py          # debug colorization
│   ├── llm.py               # Gemini repair + label-review passes, validators
│   ├── convert.py           # convert() + ConvertResult  ← shared entry point
│   └── cli.py               # argparse main(), .env loader
├── pyproject.toml           # package metadata + `ascii2drawio` console script
├── tests/test_convert.py    # behavior pins (runs with plain python3 or pytest)
├── CLAUDE.md                # this file
├── README.md
├── .env                     # GEMINI_API_KEY=... (gitignored)
├── examples/                # simple/labeled/ascii/spotify + sysdesign/ (20 diagrams)
├── scripts/
│   ├── audit.py             # parser on all fixtures: nodes/edges/orphans/XML validity
│   └── diagnose_orphans.py  # explains why each unclaimed corner failed to close
└── out/                     # generated .drawio files (gitignored)
```

Scripts/tests put `src/` on `sys.path`, so they run without installing. For the
CLI command and the web backend's `from ascii2drawio import convert`, install
once: `pip install -e .`.

## Useful invocations

```sh
# Run the CLI without installing (src on path). After `pip install -e .` you can
# drop the prefix: `ascii2drawio …` or `python3 -m ascii2drawio …`.

# Smoke test (write to out/, NOT /tmp)
PYTHONPATH=src python3 -m ascii2drawio examples/simple.txt --report -o out/simple.drawio

# Visual debug — colorized cell classification
PYTHONPATH=src python3 -m ascii2drawio examples/sysdesign/20-saga-microservices.txt --annotate

# LLM passes (needs GEMINI_API_KEY in .env)
PYTHONPATH=src python3 -m ascii2drawio examples/sysdesign/16-online-auction.txt --llm --report -o out/16.drawio
PYTHONPATH=src python3 -m ascii2drawio examples/sysdesign/20-saga-microservices.txt --llm-labels --report -o out/20.drawio

# Behavior tests
python3 tests/test_convert.py

# Full audit
python3 scripts/audit.py
```

## Web migration (in progress)

Migrating the CLI MVP to a web app. **Decisions locked:**

- **Parser**: Python backend, reuses the `ascii2drawio` package (LLM key stays server-side; single source of truth).
- **API**: FastAPI, one `POST /api/convert` (`{text, enhance:{repair,labels}}` → `{xml, report}`).
- **Frontend**: React + Vite SPA (monospace editor, debounced free conversion, "Enhance with AI" button for the LLM path).
- **Preview**: embed editable `embed.diagrams.net` via postMessage (`init` → `load` xml; `autosave` flows edits back).
- **Packaging**: one container (Vite build served by FastAPI static; Vite dev-proxies `/api`, so same-origin, no CORS).
- **Hosting**: Cloud Run + Secret Manager for `GEMINI_API_KEY`, min-instances 0.

**Phases:**
- ✅ **0** — `convert()` + `ConvertResult` extracted; monolith split into `src/ascii2drawio/`; `tests/test_convert.py` pins behavior.
- ✅ **1** — `server/app.py`: `POST /api/convert` + `/healthz` + static serving + `.env` load + input cap (`MAX_INPUT_CHARS=20k`) + 503 when AI requested without a key. Barebones `server/index.html` (textarea → convert → download). Deployable.
- ✅ **2** — React+Vite SPA in `web/`: monospace editor (debounced auto-convert), embedded editable draw.io preview (`DrawioPreview.tsx`, JSON postMessage protocol), `Load example` dropdown (served by `GET /api/examples`), Download `.drawio`. `vite build` → `web/dist/`, which FastAPI serves. *(Live embed render not yet browser-verified — protocol is standard.)*
- ✅ **3** — "✨ Enhance with AI" button (repair + labels) with loading/error states + stale-response guard; backend gates the AI path: 503 without a key, per-IP rate limit + concurrency cap (`server/limits.py`) → 429, deterministic path stays unmetered. Button auto-disables via `/healthz` `llm` flag. `tests/test_server.py` covers it (stubbed Gemini, no live calls).
- ⬜ **4** — Dockerfile (node build → python runtime) + Cloud Run + logging.

Run, two ways:
- **Prod-style (single origin):** `cd web && npm run build` then `.venv/bin/uvicorn server.app:app --port 8000` → `http://127.0.0.1:8000`.
- **Dev (HMR):** `.venv/bin/uvicorn server.app:app --reload --port 8000` and, separately, `cd web && npm run dev` → `http://127.0.0.1:5173` (Vite proxies `/api` to :8000).

Gotchas: LLM calls are blocking → sync handlers (threadpool); cap input size; don't log diagram text; disclose to users that diagrams hit the server (and Google only when Enhance is used).
