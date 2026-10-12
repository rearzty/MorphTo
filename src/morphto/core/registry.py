"""Converters and the format graph they form.

Every converter declares the (source, target) format pairs it can handle. Together they form a
directed graph; a conversion is the cheapest path through it, so e.g. PSD -> ICO or MKV -> OPUS work
even when no single converter handles that pair directly.
"""

from __future__ import annotations

import heapq
import threading
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from morphto.core.formats import Format
from morphto.core.options import Options

ProgressFn = Callable[[float], None]


class ConversionError(Exception):
    """A conversion failed; the message is meant to be shown to the user."""


class CancelledError(ConversionError):
    def __init__(self) -> None:
        super().__init__("Cancelled")


@dataclass
class Context:
    progress: ProgressFn = lambda _fraction: None
    cancel: threading.Event = field(default_factory=threading.Event)

    def check_cancelled(self) -> None:
        if self.cancel.is_set():
            raise CancelledError


class Converter(ABC):
    name: str = ""
    engine: str | None = None  # id of an external engine this converter needs (see morphto.engines)
    cost: int = 10  # relative cost of one hop; the planner minimises the sum

    @abstractmethod
    def pairs(self) -> Iterable[tuple[str, str]]:
        """Format id pairs (source, target) this converter supports."""

    @abstractmethod
    def convert(
        self, src: Path, dst: Path, src_fmt: Format, dst_fmt: Format, options: Options, ctx: Context
    ) -> None:
        """Convert ``src`` into ``dst`` (``dst`` does not exist yet)."""


@dataclass(frozen=True, slots=True)
class Step:
    src: str
    dst: str
    converter: Converter


class Registry:
    def __init__(self) -> None:
        self._converters: list[Converter] = []
        self._edges: dict[str, list[Step]] = {}

    def add(self, converter: Converter) -> None:
        self._converters.append(converter)
        for src, dst in converter.pairs():
            self._edges.setdefault(src, []).append(Step(src, dst, converter))

    @property
    def converters(self) -> list[Converter]:
        return list(self._converters)

    def find_path(self, src: str, dst: str) -> list[Step] | None:
        """Cheapest chain of steps converting ``src`` to ``dst``; ``None`` if impossible."""
        if src == dst:
            same = [s for s in self._edges.get(src, []) if s.dst == dst]
            return [min(same, key=lambda s: s.converter.cost)] if same else None
        # Dijkstra; the counter keeps heap entries comparable.
        best: dict[str, int] = {src: 0}
        heap: list[tuple[int, int, str, list[Step]]] = [(0, 0, src, [])]
        counter = 1
        while heap:
            cost, _, node, path = heapq.heappop(heap)
            if node == dst:
                return path
            if cost > best.get(node, cost):
                continue
            for step in self._edges.get(node, []):
                if step.dst == node:
                    continue
                new_cost = cost + step.converter.cost
                if new_cost < best.get(step.dst, new_cost + 1):
                    best[step.dst] = new_cost
                    heapq.heappush(heap, (new_cost, counter, step.dst, [*path, step]))
                    counter += 1
        return None

    def targets(self, src: str, max_hops: int = 3) -> set[str]:
        """Every format reachable from ``src`` (``src`` itself only if it can be re-encoded)."""
        reachable: set[str] = set()
        frontier = {src}
        for _ in range(max_hops):
            nxt: set[str] = set()
            for node in frontier:
                for step in self._edges.get(node, []):
                    if step.dst not in reachable:
                        reachable.add(step.dst)
                        nxt.add(step.dst)
            frontier = nxt - {src}
        if not any(s.dst == src for s in self._edges.get(src, [])):
            reachable.discard(src)
        return reachable

    def sources(self) -> set[str]:
        return {src for src, steps in self._edges.items() if steps}


_default: Registry | None = None
_lock = threading.Lock()


def default_registry() -> Registry:
    global _default
    with _lock:
        if _default is None:
            from morphto.converters import register_builtin

            registry = Registry()
            register_builtin(registry)
            _default = registry
    return _default
