"""User-tunable conversion options (kept intentionally tiny)."""

from __future__ import annotations

from dataclasses import dataclass, fields, replace


@dataclass(frozen=True, slots=True)
class Options:
    quality: int | None = None  # 1..100 for lossy formats; None = sensible default
    max_side: int | None = None  # downscale so the longest side is at most this many pixels
    target_size_mb: float | None = None  # aim for this output size (video)
    audio_bitrate_k: int | None = None  # e.g. 128 for 128 kbit/s

    def merged(self, other: Options) -> Options:
        """Return a copy where every option set in ``other`` overrides this one."""
        changes = {
            f.name: getattr(other, f.name) for f in fields(other) if getattr(other, f.name) is not None
        }
        return replace(self, **changes)
