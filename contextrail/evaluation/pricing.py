"""Versioned, user-supplied price sheets for reproducible API cost estimates."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path


@dataclass(frozen=True)
class PriceBook:
    version: str
    input_per_million: float
    output_per_million: float
    cached_input_per_million: float | None = None
    cache_write_per_million: float | None = None

    def __post_init__(self) -> None:
        if not self.version.strip() or self.input_per_million < 0 or self.output_per_million < 0:
            raise ValueError("Price sheet requires a version and nonnegative input/output prices.")
        if self.cached_input_per_million is not None and self.cached_input_per_million < 0:
            raise ValueError("Cached-input price must be nonnegative.")
        if self.cache_write_per_million is not None and self.cache_write_per_million < 0:
            raise ValueError("Cache-write price must be nonnegative.")

    @classmethod
    def from_path(cls, path: Path) -> "PriceBook":
        value = json.loads(path.read_text(encoding="utf-8"))
        return cls(value["version"], float(value["input_per_million"]), float(value["output_per_million"]),
                   None if value.get("cached_input_per_million") is None else float(value["cached_input_per_million"]),
                   None if value.get("cache_write_per_million") is None else float(value["cache_write_per_million"]))

    def estimate(self, *, input_tokens: int, output_tokens: int, cached_input_tokens: int | None = None,
                 cache_write_tokens: int | None = None) -> float:
        if min(input_tokens, output_tokens) < 0:
            raise ValueError("Invalid token accounting for price estimate.")
        if cached_input_tokens is None:
            if self.cached_input_per_million is not None and self.cached_input_per_million != self.input_per_million:
                raise ValueError("Cached-input usage is required by this price sheet.")
            cached_input_tokens = 0
        if cache_write_tokens is None:
            if self.cache_write_per_million is not None:
                raise ValueError("Cache-write usage is required by this price sheet.")
            cache_write_tokens = 0
        if min(cached_input_tokens, cache_write_tokens) < 0 or cached_input_tokens > input_tokens:
            raise ValueError("Invalid token accounting for price estimate.")
        cached_rate = self.input_per_million if self.cached_input_per_million is None else self.cached_input_per_million
        uncached = input_tokens - cached_input_tokens
        return (uncached * self.input_per_million + cached_input_tokens * cached_rate +
                output_tokens * self.output_per_million +
                cache_write_tokens * (self.cache_write_per_million or 0)) / 1_000_000
