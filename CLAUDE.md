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

## Approach: deterministic perception, LLM reconciliation

Decided early against pure-LLM extraction (slow, costly, non-reproducible) and against actor-critic loops (this is a parsing task with ground truth — a critic mostly re-derives what the actor produced).

**Failure taxonomy (sort by root nature, not symptom — the bucket says which tool owns it):**
- **A. Glyph perception** — `v`/`^` as letters vs arrows, `-` hyphen vs line, `>Gateway` glue, doubled/reversed labels. *Has local ground truth → deterministic predicate (`is_edge_glyph`). LLM is the wrong tool here.*
- **B. Geometry/tolerance** — ≥2-col-drift boxes, space-gap grounding, nested-box containment, multi-arrow. *Pixels + coordinates → deterministic.*
- **C. Structural interpretation** — mid-chain node vs pass-through label, which fan-out branch a trunk label attaches to. *No local ground truth → genuinely wants the model.*
- **D. Label content** — multi-word truncation. *Reading task → model-friendly.*

A and B are most of the residual work and are deterministic; only C (and D) want the LLM. The pipeline:

1. **Deterministic parser** handles the ~95% — clean boxes, **nested containers**, traced edges (fan-out/fan-in, multi-arrow), arrowheads, inline + floating labels, **drifted-wall recovery**, **borderless nodes (loose)**. Fast, free, reproducible.
2. **Deterministic IR + ambiguity flags** (`build_ir`) — packages the parse and flags where it's least sure: `orphan_cluster`, `ungrounded_text` (likely missed node), `truncation_suspect` (likely label fragment). This **broadens the repair trigger** beyond orphan-edge cells, which only catch edge-tracing failures and miss "confident but wrong" cases (a missed node / truncated label produce well-formed-but-wrong output with no orphan signal).
3. **One holistic LLM reconciliation pass** (`llm_reconcile`, `--llm`/`--llm-labels`) — fires only when a flag was raised; sees the *whole* IR (ASCII + nodes + edges + leftover text + flags) and returns a **diff in grid coordinates** (add/relabel/reparent). Seeing everything at once is what lets it resolve cross-cutting C-type decisions; the IR anchors coordinates so it reads them off the parser.
4. **Validators gate every diff op** (border/label evidence, label-grounded, id validity, geometric containment) — the model can only use glyphs/text actually in the grid.
5. **No critic loop.** The deterministic validators on the LLM output are cheaper and stricter than a second LLM pass.

*(Earlier model: deterministic-first + per-cluster orphan **patching** + a separate label pass. The flaw was the trigger, not the ordering — orphan cells are a smoke detector wired only to the kitchen. The reconciler keeps "deterministic proposes, LLM disposes, validators gate" but proposes a *complete structured scene* and audits it *once* instead of patching self-flagged holes.)*

## What's built today

`src/ascii2drawio/` — an installable Python package split by concern (was a single ~1130-line file; refactored in Phase 0 of the web migration). Pipeline: `Grid → find_rectangles (nested) → [find_text_nodes if loose] → find_edges → build_ir → (optional LLM reconcile) → emit_drawio`. The public entry point is `convert(text, *, loose=False, repair=False, labels=False, api_key=None) -> ConvertResult`; `import ascii2drawio as a2d` re-exports the full surface (`a2d.Grid`, `a2d.find_edges`, …). Modules: `glyphs` · `grid` · `nodes` · `edges` (incl. `_orphan_clusters`) · `emit` · `ir` · `annotate` · `llm` · `convert` · `cli`.

**Glyph classification**
- `H_LINE`/`V_LINE`/`CORNERS`/`TEES`/`PLUS`/`ARROWS` glyph sets; `H_BORDER`/`V_BORDER` accept tees as border continuation.
- Arrowheads: Unicode `→ ← ↑ ↓`, **filled triangles `► ◄ ▲ ▼` (and `▶ ◀`)** — all unambiguous, always arrows. `ASCII_ARROWS = {v ^ < >}` — these double as letters. `is_edge_glyph()` treats them as text when flanked by alphanumerics (the `v` in "e**v**ent"/"**v**alid"), as arrows otherwise. **Exception (horizontal only):** a `<`/`>` fused to its horizontal line on one side and text on the other (`──>Gateway`, `Gateway<──`) is a real arrowhead, not a letter — the same-axis line neighbor wins. (Scoped to `<`/`>`; the vertical `v`/`^` letter cases stay untouched.) The ASCII hyphen `-` gets the dual-use treatment too: between two alphanumerics it's punctuation inside a word (`top-k`, `read-only`), not a line. **This predicate is the single source of truth** for "does this cell participate in edge tracing" and is used everywhere (tracing, bridging, off-line labels, loose-mode node grounding, and `scripts/audit.py`).

**Node detection** — `find_rectangles` / `_close_rect`: closed rectangles from `┌`/`+` corners, with ±1 column-drift tolerance on walls/corners and per-row wall tracking for label extraction. `_close_rect` first tries `_close_by_walls`, then falls back to `_close_by_edges`. **`_close_by_walls` follows gradual drift**: it walks each side wall down via `_follow_wall`, allowing ±1 column from the *previous* row (cumulative), so a trapezoidal/ragged box whose width creeps line-by-line still closes (e.g. spotify's API GATEWAY: corners 66/64, walls 65) — the node bbox is the bounding box over every border glyph. The closing corners + a solid bottom run between them are still required, so it won't follow a stray line into a false close. **`_close_by_edges`** handles the other drift shape — a *sudden* ≥2-col jump on the middle row(s) where the top+bottom edges stay column-aligned (e.g. Kibana, 3-col jump) — accepting on two aligned full horizontal edges and locating the jumped walls with a wider ±3 search for label extraction.

**Nested boxes (containment)** — `find_rectangles` runs three passes: (1) close every rectangle, marking only its *border* consumed so inner corners stay reachable; (2) set each box's `parent` to the **smallest box that strictly contains it** (pure geometry, independent of scan order — `_smallest_container`); (3) mark interiors `node-interior` (so edges don't trace through a box) and extract labels. A **container** (box with children) differs from a leaf in pass 3: it skips its children's bboxes and leaves line/arrow glyphs in its interior *unconsumed*, so an edge *between two children* (`│ Web │──>│ API │` inside a cluster) is still traceable; its label drops nested-child regions and edge glyphs so it isn't polluted by what it contains. Edge-endpoint resolution (`_node_at`) is **border-aware**: a touchpoint must lie on a box's perimeter (within drift `tol`), so a line crossing a container's interior doesn't falsely register the container — and it prefers the smallest (innermost) box on a shared border. `emit_drawio` renders a container with `container=1;verticalAlign=top` and parents its children's mxCells to it with container-relative coords. **Known limitation:** an edge that crosses a container *wall* via a crossing glyph (`──┼──>` through the border) splits into two edges at the wall rather than passing through — a pass-through case left for the IR/reconciler.

**Borderless nodes (loose mode, `--loose`)** — `find_text_nodes`: promotes text-only nodes (no box outline — `Client`, `Database`, `Load Balancer`) into real nodes. Runs *after* `find_rectangles` (boxes + their labels already consumed) and *before* `find_edges`. Flood-fills unconsumed text into blocks (bridging single-space gaps so multi-word labels stay one node), then **grounds** each block by edge-adjacency: a real node sits at the *end* of a line, so only a line/arrow that `connects` *toward* the block counts. `_edge_into` hops a single-space gutter (`_GROUND_GAP=1`) between text and its line, because the natural form puts a space there (`Client ──> Gateway`) — without the hop the most common borderless diagram grounds nothing. This directional test is the discriminator — a label beside a `│` is rejected (the `│` connects up/down, not sideways toward it, even across the gutter), and a pure pass-through inline label (`──text──`, collinear lines on opposite sides, no terminating arrowhead) is left for edge gap-bridging. Marks the block's bbox consumed via `_mark_node`, so `find_edges` then attaches arrows to it geometrically. Emitted as a draw.io text shape (no stroke/fill). **Opt-in** — strict mode (closed-rectangle-only) stays the default, preserving Release-1 accuracy.

**Edge tracing** — `find_edges`:
- `_bfs_edge` flood-fills a connected line/arrow component and bridges label gaps (`── label ──`) via `_look_ahead_bridge` (which reverses text for left/up walks and reports label cell positions; dedups the gap bridged from both ends).
- `_build_edges` (note: **plural** — splits one component into multiple edges) collects every node touchpoint, classes each as a **sink** (arrowhead into it) or **source** (plain line leaves it), then pairs them: 1 source→N sinks = fan-out, N sources→1 sink = fan-in, M×N = nearest-pairing, arrows-both-ends = bidirectional, no-arrows = undirected. Endpoint resolution uses `_node_at(..., tol=1)` fallback to absorb ±1 column drift.
- A shared trunk label attaches to the **nearest branch only** (the edge whose node-center midpoint is closest to the label's grid position).
- `_offline_label` captures floating labels beside/above/below a line. **Directional reach**: 3 cols sideways from vertical lines (`_OFFLINE_SIDE_REACH`), 1 row up/down from horizontal lines (`_OFFLINE_VERT_REACH`). No column-overlap guard — a label beside a vertical line lies entirely off to one side, so a guard would reject all of them. (Empirically: this asymmetry recovers ~48 labels with 0 false positives; a uniform/wider reach steals neighbors' inline labels.)

**XML emit** — `emit_drawio`: mxGraph XML at pixel coords scaled by `CHAR_W=9`, `CHAR_H=18`.

**CLI** — `python3 ascii2drawio.py input.txt -o out.drawio`, with `--annotate` (colorized cell classification), `--report` (stderr node/edge summary), `--ir` (dump the deterministic IR + ambiguity flags as JSON), `--loose` (also detect borderless text-only nodes), `--llm` (orphan repair), `--llm-labels` (label correction).

**Deterministic IR + ambiguity flags** (`ir.py`, `build_ir`) — a grid-grounded structured view exposed on `ConvertResult.ir`: `nodes`, `edges`, unconsumed `text_runs`, and `flags`. Built with **no LLM**; useful standalone (`--ir`, audit harness) and the grounded input the reconciler consumes (so the model reads coordinates off the parser instead of inventing them). **Flags broaden the repair trigger** beyond orphan-edge cells (which only catch edge-tracing failures, missing "confident but wrong" cases): `orphan_cluster` (unresolved edge cells), `ungrounded_text` (free-floating leftover text → likely a missed node), `truncation_suspect` (leftover text 8-adjacent to an edge → likely a truncated/missed label). The `kind` is a *hint* for the reconciler, not a verdict. `report["flags"]` carries the count; `to_dict()` is JSON-serializable.

### LLM integration (implemented, Gemini via REST)

- **Auth/transport resolved**: direct **Gemini REST** (`gemini-2.5-flash`, `thinkingBudget: 0`) via `urllib` — no SDK dependency. Key from `GEMINI_API_KEY`/`GOOGLE_API_KEY`, loaded from a repo-root `.env` by `_load_dotenv`. (The earlier Anthropic-SDK/Bedrock/Vertex fork is moot.)
- **`llm_reconcile` (the default path `convert()` uses when `repair`/`labels` is set)** — **one holistic call** over the deterministic IR (full ASCII + nodes/edges/text-runs/flags) returning a **diff in grid coordinates** (`add_node` · `add_edge` · `relabel_node` · `relabel_edge` · `reparent_node`). Seeing the whole parse at once lets it make cross-cutting calls a keyhole per-cluster prompt can't (a leftover word between two nodes is a node *because* of what flanks it). **Fires only when ≥1 ambiguity flag was raised** (the broadened trigger — catches "confident but wrong", not just orphan cells). Ops applied in order (nodes → edges → relabels → reparents) so later ops can reference newly-added ids; each op gated by the validators below.
- **Legacy granular passes still exported** (not used by `convert()` anymore): `llm_repair` (per-cluster orphan patch, **one call per cluster**) and `llm_label_review` (one label-only call). Kept for back-compat/tests; the reconciler subsumes both.
- **Anti-hallucination guards (deterministic, grid-grounded) — reused by the reconciler:**
  - `add_node`: `_border_evidence` (≥50% of the claimed rectangle border must be real line glyphs) + `_label_evidence` (majority of label tokens must appear in the region). A real-but-missed box passes; an invented box/label is rejected. *(Borderless text nodes have no border, so the reconciler can't add those — they come from loose mode.)*
  - `add_edge`: src/dst must be real node ids (existing or added earlier in the diff), src ≠ dst.
  - relabels: `_label_grounded` (label text must literally appear in the grid). Stops the model inventing/paraphrasing.
  - `reparent_node`: parent must be a real node id that *geometrically contains* the child (`_node_contains`), or null.
- LLM passes are **blocking** (`urllib`); a web backend must run them in a threadpool (sync handler), not on the event loop. The network boundary is `_call_gemini` — tests stub it (no live calls).

## Self-test corpus and audit harness

`examples/` — `simple.txt`, `labeled.txt`, `ascii.txt` (hand-written), plus `spotify.txt`. `examples/sysdesign/` — 20 system-design architecture diagrams (URL shortener, rate limiter, news feed, web crawler, distributed cache, video streaming, ride sharing, payment, distributed storage, task queue, load balancer, CDN, online auction, stock trading, log aggregation, API gateway, saga microservices, etc.). `examples/borderless/` — text-only-node diagrams (loose mode: horizontal/vertical chains, branching, no-gutter). `examples/nested/` — boxes-in-boxes (one container; two levels with a sibling edge).

`scripts/audit.py` — runs the parser on every fixture: nodes, edges, orphan-edge cells, unclassified glyph cells, edges missing an endpoint, XML validity. (Uses `is_edge_glyph`, so it doesn't miscount letter-`v`s.)

`scripts/diagnose_orphans.py` — explains why each unclaimed top-left corner failed to close.

## Current accuracy (Release 1)

20/20 fixtures: **valid XML, 0 unclassified glyph cells, 0 edges missing endpoint, 0 splittable misses** (every orphan component now resolves ≤1 node). Orphan-edge cells: **762 → 190 → 46** (the latest drop from `_close_by_edges` recovering the drifted Settlement/Kibana/Quota boxes). Residual 46 spread thinly across `11`/`13`/`16`/`18`/`19`/`20`. Fan-out/fan-in edges, arrowhead directions, inline labels, and most floating labels are correct. Nested boxes (containment + sibling edges) and multi-arrow-from-one-box are supported; loose mode recovers borderless text nodes.

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
1. ~~**≥2-column drift boxes go undetected**~~ **Mostly fixed** — `_close_by_edges` recovers boxes whose middle-row walls drift beyond ±1 but whose top+bottom edges are fully formed and column-aligned (the canonical "middle row shifted N cols" malformed box: `16` Settlement, `18` Kibana UI 3-col, `19` Quota Store). Requiring two complete aligned horizontal edges + all four corners is strong enough evidence to fire only on real boxes; walls are then located with a wider ±3 search (`_nearest_wall`) purely for label extraction. Recovered all three with clean labels and attached inbound arrows; **orphans 190 → 46**. Residuals (`13`, `11`, `16`, `18`, `19`, `20`) are smaller, non-aligned-edge cases left as **LLM/IR-repair** candidates (their orphan regions are exactly what `llm_repair` clusters on).
2. **Multi-word floating labels truncate** to the word nearest the line (`hash key`→`key`, `on fail`→`on`). The **label-correction** pass (`--llm-labels`) is designed to fix these.
3. ~~**Strict mode only** — every node must be a closed rectangle.~~ **Done** — loose mode (`--loose`) recovers borderless text-only nodes; see *Borderless nodes* above. Opt-in; strict stays the default.

## Repo layout

```
ascii2drawio/
├── src/ascii2drawio/        # the package (pip-installable)
│   ├── __init__.py          # re-exports public API + back-compat names
│   ├── __main__.py          # `python -m ascii2drawio`
│   ├── glyphs.py            # glyph sets, connects/arrow_dir/is_edge_glyph
│   ├── grid.py              # Grid
│   ├── nodes.py             # Node, find_rectangles (nested), _close_rect, find_text_nodes (loose)
│   ├── edges.py             # Edge, find_edges, _build_edges, _offline_label, _orphan_clusters
│   ├── emit.py              # emit_drawio (CHAR_W/CHAR_H)
│   ├── ir.py                # IR + AmbiguityFlag + build_ir (deterministic flags)
│   ├── annotate.py          # debug colorization
│   ├── llm.py               # Gemini reconciler (default) + legacy repair/label passes, validators
│   ├── convert.py           # convert() + ConvertResult  ← shared entry point
│   └── cli.py               # argparse main(), .env loader
├── server/                  # FastAPI web backend
│   ├── app.py               # /api/convert, /api/examples, /api/health (+/healthz alias), static serving, logging
│   ├── limits.py            # per-IP rate limit + concurrency cap for the AI path
│   ├── index.html           # barebones page (fallback when web/dist absent)
│   └── requirements.txt     # fastapi / uvicorn / pydantic
├── web/                     # React + Vite SPA (editor + draw.io preview)
│   ├── src/{App,DrawioPreview,api,main}.tsx, index.css
│   ├── package.json · vite.config.ts · tsconfig.json   # `npm run build` → web/dist
├── Dockerfile · .dockerignore   # multi-stage build (node → python), single container
├── DEPLOY.md                # Cloud Run + Secret Manager deploy steps
├── pyproject.toml           # package metadata + `ascii2drawio` console script
├── tests/                   # test_convert.py (parser) + test_server.py (API/limits)
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
- ✅ **2** — React+Vite SPA in `web/`: monospace editor (debounced auto-convert), embedded editable draw.io preview (`DrawioPreview.tsx`, JSON postMessage protocol), `Load example` dropdown (served by `GET /api/examples`, a curated 5-item subset via `APP_EXAMPLES`; the full corpus stays on disk for tests/audit), Download `.drawio`. `vite build` → `web/dist/`, which FastAPI serves. *(Live embed render not yet browser-verified — protocol is standard.)*
- ✅ **3** — "✨ Enhance with AI" button (repair + labels) with loading/error states + stale-response guard; backend gates the AI path: 503 without a key, per-IP rate limit + concurrency cap (`server/limits.py`) → 429, deterministic path stays unmetered. Button auto-disables via the `/api/health` `llm` flag (health lives under `/api/` because a fronting layer was swallowing bare `/healthz` before it reached the container on Cloud Run). `tests/test_server.py` covers it (stubbed Gemini, no live calls).
- ✅ **loose-mode toggle** — a "Text-only nodes" checkbox in the toolbar maps to `ConvertRequest.loose`, re-running the debounced **deterministic/free** conversion (separate from the AI Enhance button). `convert(loose=…)` is threaded through both server paths; `test_server.py` pins it (strict→0 nodes, loose→3 on a borderless chain).
- ✅ **4** — Multi-stage `Dockerfile` (node build → python runtime serving `web/dist` + API), `.dockerignore`, structured JSON request logging (sizes/latency, never diagram text), `DEPLOY.md` with the Cloud Run + Secret Manager commands. Image (~192 MB) built and run-verified locally; deploy is the user's GCP project (not run here).

Run, two ways:
- **Prod-style (single origin):** `cd web && npm run build` then `.venv/bin/uvicorn server.app:app --port 8000` → `http://127.0.0.1:8000`.
- **Dev (HMR):** `.venv/bin/uvicorn server.app:app --reload --port 8000` and, separately, `cd web && npm run dev` → `http://127.0.0.1:5173` (Vite proxies `/api` to :8000).

Gotchas: LLM calls are blocking → sync handlers (threadpool); cap input size; don't log diagram text; disclose to users that diagrams hit the server (and Google only when Enhance is used).
