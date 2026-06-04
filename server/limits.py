"""Lightweight, dependency-free guards for the (paid, slow) AI-enhance path.

Per-instance and in-memory — good enough to blunt abuse on a scale-to-zero
Cloud Run service. Both are thread-safe because FastAPI runs the sync handler
in a worker threadpool. Raise HTTPException so FastAPI turns them into 429s.
"""
from __future__ import annotations

import threading
import time

from fastapi import HTTPException


class FixedWindowLimiter:
    """At most ``max_requests`` per ``window_seconds`` per key (e.g. client IP)."""

    def __init__(self, max_requests: int, window_seconds: float):
        self.max = max_requests
        self.window = window_seconds
        self._lock = threading.Lock()
        self._hits: dict[str, list[float]] = {}

    def check(self, key: str) -> None:
        now = time.monotonic()
        cutoff = now - self.window
        with self._lock:
            q = self._hits.setdefault(key, [])
            i = 0
            while i < len(q) and q[i] < cutoff:
                i += 1
            del q[:i]
            if len(q) >= self.max:
                retry = int(self.window - (now - q[0])) + 1
                raise HTTPException(
                    status_code=429,
                    detail=f"Rate limit reached. Try again in {retry}s.",
                )
            q.append(now)


class ConcurrencyLimiter:
    """Cap concurrent in-flight calls; reject (don't queue) when full.

    Each LLM request holds a worker thread for seconds, so we keep a small pool
    free for the (fast, free) deterministic path.
    """

    def __init__(self, max_concurrent: int):
        self._sem = threading.BoundedSemaphore(max_concurrent)

    def __enter__(self) -> "ConcurrencyLimiter":
        if not self._sem.acquire(blocking=False):
            raise HTTPException(
                status_code=429,
                detail="Server is busy with AI requests. Try again shortly.",
            )
        return self

    def __exit__(self, *exc) -> None:
        self._sem.release()
