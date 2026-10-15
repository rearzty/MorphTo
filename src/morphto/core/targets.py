"""What a file can be turned into: plain formats plus task-oriented presets, ranked smartly."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from morphto.core.engine import plan
from morphto.core.formats import Category, Format, get_format
from morphto.core.options import Options
from morphto.core.registry import ConversionError, Registry, default_registry


@dataclass(frozen=True, slots=True)
class Target:
    id: str  # "png" for a plain format, "preset:web" for a preset
    format: Format
    options: Options = field(default_factory=Options)
    preset: str | None = None  # preset key; its label lives in the translations as "preset.<key>"

    @property
    def is_preset(self) -> bool:
        return self.preset is not None


@dataclass(frozen=True, slots=True)
class Preset:
    key: str
    format_id: str
    options: Options
    categories: frozenset[Category]

    def target(self) -> Target:
        return Target(f"preset:{self.key}", get_format(self.format_id), self.options, self.key)


_IMG, _VID, _AUD = frozenset({Category.IMAGE}), frozenset({Category.VIDEO}), frozenset({Category.AUDIO})

PRESETS: list[Preset] = [
    Preset("web", "webp", Options(quality=80, max_side=1920), _IMG),
    Preset("small_jpg", "jpg", Options(quality=75, max_side=2560), _IMG),
    Preset("icon", "ico", Options(), _IMG),
    Preset("discord", "mp4", Options(target_size_mb=9.5), _VID),
    Preset("small_mp3", "mp3", Options(audio_bitrate_k=96), _AUD | _VID),
]

_DEFAULT_ORDER: dict[Category, list[str]] = {
    Category.IMAGE: ["png", "jpg", "webp", "pdf", "preset:web", "ico", "avif", "gif", "preset:small_jpg"],
    Category.VIDEO: ["mp4", "gif", "mp3", "preset:discord", "webm", "mkv", "mov", "m4a", "wav"],
    Category.AUDIO: ["mp3", "wav", "flac", "m4a", "ogg", "opus", "preset:small_mp3"],
    Category.DOCUMENT: ["pdf", "docx", "png", "jpg", "odt", "txt"],
    Category.SPREADSHEET: ["pdf", "xlsx", "csv", "png", "ods"],
    Category.PRESENTATION: ["pdf", "png", "jpg", "pptx", "odp"],
    Category.DATA: ["json", "csv", "xlsx", "yaml", "xml"],
    Category.BOOK: ["epub", "fb2", "pdf", "docx"],
    Category.ARCHIVE: ["folder", "zip", "7z"],
}
# What people usually want from a specific source format (goes before the category defaults).
_FORMAT_ORDER: dict[str, list[str]] = {
    "pdf": ["png", "jpg", "txt"],
    "md": ["pdf", "html", "docx"],
    "html": ["pdf", "md", "docx"],
    "epub": ["fb2", "pdf"],
    "fb2": ["epub", "pdf"],
    "json": ["yaml", "csv", "xlsx"],
    "yaml": ["json"],
    "xml": ["json", "csv"],
    "raw": ["jpg", "png", "tiff"],
    "svg": ["png", "ico", "jpg"],
    "jxl": ["jpg", "png"],
    "m4a": ["mp3", "m4r"],
    "txt": ["pdf", "docx"],
    "csv": ["xlsx"],
    "heic": ["jpg", "png"],
    "avif": ["png", "jpg"],
    "webp": ["png", "jpg", "gif"],
    "png": ["jpg", "webp", "ico"],
    "jpg": ["png", "webp", "pdf"],
    "psd": ["png", "jpg"],
    "gif": ["mp4", "webp", "png"],
    "mov": ["mp4"],
    "mkv": ["mp4"],
    "webm": ["mp4", "gif"],
    "avi": ["mp4"],
    "wav": ["mp3", "flac"],
    "flac": ["mp3", "m4a"],
    "opus": ["mp3"],
    "ogg": ["mp3"],
}


def available_targets(sources: Sequence[Format], registry: Registry | None = None) -> list[Target]:
    """Targets valid for *all* given source formats (unranked)."""
    if not sources:
        return []
    registry = registry or default_registry()
    common: set[str] | None = None
    for src in sources:
        allowed = {fid for fid in registry.targets(src.id) if fid != src.id and _sensible(registry, src, fid)}
        common = allowed if common is None else common & allowed
    result = [Target(fid, get_format(fid)) for fid in sorted(common or ())]
    for preset in PRESETS:
        if all(
            src.category in preset.categories and _sensible(registry, src, preset.format_id)
            for src in sources
        ):
            result.append(preset.target())
    return result


def _sensible(registry: Registry, src: Format, dst_id: str) -> bool:
    try:
        plan(src, get_format(dst_id), registry)
    except ConversionError:
        return False
    return True


def rank(targets: Sequence[Target], source: Format, history: Mapping[str, int] | None = None) -> list[Target]:
    """Order targets: most used for this source format first, then curated defaults, then A-Z."""
    history = history or {}
    order: list[str] = []
    for tid in [*_FORMAT_ORDER.get(source.id, []), *_DEFAULT_ORDER.get(source.category, [])]:
        if tid not in order:
            order.append(tid)
    position = {tid: i for i, tid in enumerate(order)}

    def key(t: Target) -> tuple[int, int, int, str]:
        return (-history.get(t.id, 0), position.get(t.id, len(order)), int(t.is_preset), t.id)

    return sorted(targets, key=key)


def find_target(target_id: str) -> Target:
    """Resolve "png", ".png" or "preset:web" / "web" into a Target."""
    tid = target_id.strip().lower()
    key = tid.removeprefix("preset:")
    for preset in PRESETS:
        if preset.key == key:
            return preset.target()
    fmt = get_format(tid)
    return Target(fmt.id, fmt)
