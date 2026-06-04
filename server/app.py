"""FastAPI backend for ascii2drawio.

One JSON endpoint (`POST /api/convert`) over the shared ``convert()`` library,
plus a health check and static serving of the built frontend (or a barebones
page until the Vite build exists).

Handlers are deliberately sync ``def`` — the optional LLM passes use blocking
``urllib``, so FastAPI runs them in a worker threadpool instead of blocking the
event loop. The Gemini key lives only on the server (env), never in a request.
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .limits import ConcurrencyLimiter, FixedWindowLimiter

# Prefer the installed package; fall back to the repo's src/ for `python server/app.py`.
try:
    from ascii2drawio import convert
except ModuleNotFoundError:  # pragma: no cover
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from ascii2drawio import convert

ROOT = Path(__file__).resolve().parents[1]
DIST_DIR = ROOT / "web" / "dist"               # Vite build (Phase 2) lands here
FALLBACK_INDEX = Path(__file__).resolve().parent / "index.html"  # Phase 1 page
EXAMPLES_DIR = ROOT / "examples"

# Local dev convenience: pull the key from the repo .env if present. No-op in
# Cloud Run (no file there) where the key is injected as a real env var, and it
# never overrides an already-set env var.
from ascii2drawio.cli import _load_dotenv  # noqa: E402
_load_dotenv(str(ROOT / ".env"))

# Bound parser + token cost; the editor enforces the same client-side.
MAX_INPUT_CHARS = 20_000

# Guards for the AI-enhance path only (the deterministic path stays unmetered).
_enhance_rate = FixedWindowLimiter(max_requests=10, window_seconds=60.0)
_enhance_slots = ConcurrencyLimiter(max_concurrent=4)

app = FastAPI(title="ascii2drawio", version="1.0.0")


def _client_key(request: Request) -> str:
    """Best-effort client identity for rate limiting (honors Cloud Run's XFF)."""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "anon"


class Enhance(BaseModel):
    repair: bool = False
    labels: bool = False


class ConvertRequest(BaseModel):
    text: str = Field(..., description="ASCII/Unicode box-drawing diagram")
    enhance: Enhance = Field(default_factory=Enhance)


class ConvertResponse(BaseModel):
    xml: str
    report: dict


def _api_key() -> str | None:
    return os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "llm": bool(_api_key())}


@app.post("/api/convert", response_model=ConvertResponse)
def api_convert(req: ConvertRequest, request: Request) -> ConvertResponse:
    if len(req.text) > MAX_INPUT_CHARS:
        raise HTTPException(status_code=413,
                            detail=f"Input exceeds {MAX_INPUT_CHARS} characters.")

    use_llm = req.enhance.repair or req.enhance.labels
    if not use_llm:
        # Fast, free, unmetered path.
        result = convert(req.text)
        return ConvertResponse(xml=result.xml, report=result.report)

    # AI-enhance path: needs a key, and is rate- and concurrency-limited.
    api_key = _api_key()
    if not api_key:
        raise HTTPException(status_code=503,
                            detail="AI enhance is unavailable: server has no API key.")
    _enhance_rate.check(_client_key(request))
    with _enhance_slots:
        result = convert(
            req.text,
            repair=req.enhance.repair,
            labels=req.enhance.labels,
            api_key=api_key,
        )
    return ConvertResponse(xml=result.xml, report=result.report)


@app.get("/api/examples")
def api_examples() -> list[dict]:
    """Bundled sample diagrams for the editor's 'Load example' dropdown."""
    paths = sorted(EXAMPLES_DIR.glob("*.txt"))
    paths += sorted((EXAMPLES_DIR / "sysdesign").glob("*.txt"))
    out: list[dict] = []
    for p in paths:
        try:
            out.append({"name": p.stem, "text": p.read_text(encoding="utf-8")})
        except OSError:
            continue
    return out


# Static frontend: serve the Vite build if present, else the barebones page.
# Registered last so /api/* and /healthz take precedence.
if DIST_DIR.exists():
    app.mount("/", StaticFiles(directory=str(DIST_DIR), html=True), name="static")
else:
    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(FALLBACK_INDEX)
