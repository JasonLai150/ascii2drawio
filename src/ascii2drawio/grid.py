"""The character grid: input text padded to a rectangle with safe access."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Grid:
    rows: list[list[str]]

    @classmethod
    def from_text(cls, text: str) -> "Grid":
        lines = text.split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        if not lines:
            return cls([])
        w = max(len(line) for line in lines)
        return cls([list(line.ljust(w)) for line in lines])

    @property
    def h(self) -> int:
        return len(self.rows)

    @property
    def w(self) -> int:
        return len(self.rows[0]) if self.rows else 0

    def at(self, r: int, c: int) -> str:
        if 0 <= r < self.h and 0 <= c < self.w:
            return self.rows[r][c]
        return " "

    def in_bounds(self, r: int, c: int) -> bool:
        return 0 <= r < self.h and 0 <= c < self.w
