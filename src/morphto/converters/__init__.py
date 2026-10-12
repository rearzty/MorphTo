"""Built-in converters. Each module exposes one or more Converter subclasses."""

from __future__ import annotations

from morphto.core.registry import Registry


def register_builtin(registry: Registry) -> None:
    from morphto.converters.images import HeifWriter, PillowConverter
    from morphto.converters.media import FFmpegConverter

    registry.add(PillowConverter())
    registry.add(HeifWriter())
    registry.add(FFmpegConverter())
