"""High-level conversion API used by both the CLI and the GUI."""

from __future__ import annotations

import shutil
import tempfile
import threading
from pathlib import Path

from morphto.core.detect import detect
from morphto.core.formats import Format, get_format
from morphto.core.options import Options
from morphto.core.registry import (
    CancelledError,
    Context,
    ConversionError,
    ProgressFn,
    Registry,
    Step,
    default_registry,
)

__all__ = [
    "CancelledError",
    "ConversionError",
    "EngineMissingError",
    "convert",
    "output_path",
    "plan",
    "required_engines",
]


class EngineMissingError(ConversionError):
    def __init__(self, engines: list[str]) -> None:
        self.engines = engines
        super().__init__(f"Required component is not installed: {', '.join(engines)}")


def plan(src_fmt: Format, dst_fmt: Format, registry: Registry | None = None) -> list[Step]:
    registry = registry or default_registry()
    path = registry.find_path(src_fmt.id, dst_fmt.id)
    # Changing category (image -> video, video -> image ...) only through a direct converter; otherwise
    # we'd produce nonsense like PNG -> one-frame GIF -> MP4.
    if path is None or (dst_fmt.category != src_fmt.category and len(path) > 1):
        raise ConversionError(f"Cannot convert {src_fmt.name} to {dst_fmt.name}")
    return path


def required_engines(steps: list[Step]) -> list[str]:
    from morphto import engines

    needed = {s.converter.engine for s in steps if s.converter.engine}
    return sorted(e for e in needed if not engines.get(e).available())


def output_path(source_stem: str, dst_fmt: Format, out_dir: Path) -> Path:
    """``out_dir/stem.ext``; never overwrites — adds `` (1)``, `` (2)`` ... instead."""
    candidate = out_dir / f"{source_stem}.{dst_fmt.ext}"
    n = 1
    while candidate.exists():
        candidate = out_dir / f"{source_stem} ({n}).{dst_fmt.ext}"
        n += 1
    return candidate


def convert(
    source: str | Path,
    target: str | Format,
    *,
    out_dir: str | Path | None = None,
    output: str | Path | None = None,
    options: Options | None = None,
    progress: ProgressFn | None = None,
    cancel: threading.Event | None = None,
    registry: Registry | None = None,
) -> Path:
    """Convert ``source`` to ``target`` format and return the path of the new file.

    The result goes to ``output`` if given, else into ``out_dir`` (default: next to the source).
    Existing files are never overwritten unless ``output`` names them explicitly.
    """
    source = Path(source)
    if not source.is_file():
        raise ConversionError(f"File not found: {source}")
    src_fmt = detect(source)
    if src_fmt is None:
        raise ConversionError(f"Unsupported or unknown file format: {source.name}")
    dst_fmt = target if isinstance(target, Format) else get_format(target)
    steps = plan(src_fmt, dst_fmt, registry)
    missing = required_engines(steps)
    if missing:
        raise EngineMissingError(missing)

    if output is not None:
        final = Path(output)
    else:
        directory = Path(out_dir) if out_dir is not None else source.parent
        directory.mkdir(parents=True, exist_ok=True)
        final = output_path(source.stem, dst_fmt, directory)

    options = options or Options()
    report = progress or (lambda _f: None)
    ctx = Context(cancel=cancel or threading.Event())
    with tempfile.TemporaryDirectory(prefix="morphto-") as tmp:
        current, current_fmt = source, src_fmt
        for i, step in enumerate(steps):
            ctx.check_cancelled()
            step_fmt = get_format(step.dst)
            step_out = Path(tmp) / f"step{i}.{step_fmt.ext}"
            ctx.progress = lambda f, i=i: report(min(1.0, (i + max(0.0, min(1.0, f))) / len(steps)))
            # Options such as quality/size only make sense for the final encode.
            step_options = options if i == len(steps) - 1 else Options()
            try:
                step.converter.convert(current, step_out, current_fmt, step_fmt, step_options, ctx)
            except (ConversionError, CancelledError):
                raise
            except Exception as exc:  # converters wrap third-party libraries; surface a clean message
                raise ConversionError(f"{step.converter.name}: {exc}") from exc
            if not step_out.is_file():
                raise ConversionError(f"{step.converter.name} produced no output")
            current, current_fmt = step_out, step_fmt
        final.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(current), final)
    report(1.0)
    return final
