#!/usr/bin/env python3
"""Backend tests for the AI-enhance gating + limiters.

Calls the handler directly with a hand-built Request and stubs the Gemini call,
so there are no live API calls and no httpx/pytest dependency. Run directly:
`python3 tests/test_server.py`.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from fastapi import HTTPException  # noqa: E402
from starlette.requests import Request  # noqa: E402

import server.app as appmod  # noqa: E402
from server.limits import ConcurrencyLimiter, FixedWindowLimiter  # noqa: E402

BOX = "┌──┐\n│A │\n└──┘"


def _request(client="1.2.3.4", headers=None) -> Request:
    hdrs = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request({
        "type": "http", "method": "POST", "path": "/api/convert",
        "headers": hdrs, "client": (client, 0), "query_string": b"",
        "scheme": "http", "server": ("test", 80),
    })


def _status(fn) -> int:
    try:
        fn()
        return 200
    except HTTPException as e:
        return e.status_code


def test_fixed_window_limiter():
    lim = FixedWindowLimiter(max_requests=2, window_seconds=60)
    lim.check("k"); lim.check("k")
    assert _status(lambda: lim.check("k")) == 429
    lim.check("other")  # separate key unaffected


def test_concurrency_limiter():
    lim = ConcurrencyLimiter(max_concurrent=1)
    with lim:
        assert _status(lambda: lim.__enter__()) == 429
    with lim:  # released, usable again
        pass


def test_deterministic_needs_no_key():
    req = appmod.ConvertRequest(text=BOX)
    resp = appmod.api_convert(req, _request())
    assert resp.report["nodes"] == 1


def test_oversized_413():
    req = appmod.ConvertRequest(text="x" * (appmod.MAX_INPUT_CHARS + 1))
    assert _status(lambda: appmod.api_convert(req, _request())) == 413


def test_enhance_503_without_key(restore):
    restore("_api_key", lambda: None)
    req = appmod.ConvertRequest(text=BOX, enhance=appmod.Enhance(repair=True))
    assert _status(lambda: appmod.api_convert(req, _request())) == 503


def test_enhance_runs_and_rate_limits(restore, stub_gemini):
    stub_gemini(lambda *a, **k: {"repairs": []})   # no real network
    restore("_api_key", lambda: "fake")
    restore("_enhance_rate", FixedWindowLimiter(max_requests=2, window_seconds=60))
    req = appmod.ConvertRequest(text=BOX, enhance=appmod.Enhance(repair=True, labels=True))
    assert _status(lambda: appmod.api_convert(req, _request())) == 200
    assert _status(lambda: appmod.api_convert(req, _request())) == 200
    assert _status(lambda: appmod.api_convert(req, _request())) == 429  # 3rd over the limit


def _run():
    from ascii2drawio import llm

    saved: dict = {}

    def restore(attr, value):
        if attr not in saved:
            saved[attr] = getattr(appmod, attr)
        setattr(appmod, attr, value)

    def stub_gemini(fn):
        saved["_call_gemini"] = llm._call_gemini
        llm._call_gemini = fn

    tests = [
        (test_fixed_window_limiter, ()),
        (test_concurrency_limiter, ()),
        (test_deterministic_needs_no_key, ()),
        (test_oversized_413, ()),
        (test_enhance_503_without_key, (restore,)),
        (test_enhance_runs_and_rate_limits, (restore, stub_gemini)),
    ]
    passed = 0
    try:
        for fn, args in tests:
            fn(*args)
            print(f"  ok  {fn.__name__}")
            passed += 1
    finally:
        for attr, val in saved.items():
            if attr == "_call_gemini":
                llm._call_gemini = val
            else:
                setattr(appmod, attr, val)
    print(f"\n{passed} passed")


if __name__ == "__main__":
    _run()
