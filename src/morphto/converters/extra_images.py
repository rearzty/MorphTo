"""Images that need a component downloaded on first use: SVG (resvg), camera RAW (LibRaw), JPEG XL."""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Iterable
from pathlib import Path
from xml.etree import ElementTree as ET

from morphto import engines
from morphto.converters.images import READ, WRITE, PillowConverter
from morphto.core.formats import Format
from morphto.core.options import Options
from morphto.core.registry import Context, ConversionError, Converter

SVG_MIN_SIDE = 1024  # icons are often 24 px in the file; render them big enough to be useful


class SvgConverter(Converter):
    """SVG -> PNG with resvg; Pillow takes it from there (ICO, JPG, WebP...)."""

    name = "resvg"
    engine = "resvg"
    cost = 10

    def pairs(self) -> Iterable[tuple[str, str]]:
        return [("svg", "png")]

    def convert(
        self, src: Path, dst: Path, src_fmt: Format, dst_fmt: Format, options: Options, ctx: Context
    ) -> None:
        resvg = engines.get("resvg").tool("resvg")
        if resvg is None:
            raise ConversionError("The SVG renderer is not installed")
        side = svg_size(src)
        target = options.max_side or (max(SVG_MIN_SIDE, max(side)) if side else SVG_MIN_SIDE)
        cmd = [str(resvg), str(src), str(dst)]
        if side and max(side) > 0:
            cmd += ["--zoom", f"{target / max(side):.4f}"]
        else:
            cmd += ["--width", str(target)]
        from morphto.converters.office import _run

        result = _run(cmd, ctx)
        if result.returncode != 0 or not dst.is_file():
            detail = (result.stderr or b"").decode("utf-8", "replace").strip()
            raise ConversionError(f"Cannot render the SVG: {detail[-300:] or 'unknown error'}")


_LENGTH = re.compile(r"\s*([0-9.]+)\s*(px|pt|mm|cm|in|em)?\s*$")
_UNIT = {"px": 1, None: 1, "pt": 4 / 3, "mm": 96 / 25.4, "cm": 96 / 2.54, "in": 96, "em": 16}


def svg_size(path: Path) -> tuple[float, float] | None:
    """Intrinsic size in CSS pixels from width/height (or the viewBox); None if unknown or relative."""
    try:
        for _event, el in ET.iterparse(path, events=("start",)):
            attrs = el.attrib
            size = []
            for name in ("width", "height"):
                match = _LENGTH.match(attrs.get(name, ""))
                size.append(float(match[1]) * _UNIT[match[2]] if match else None)
            if None in size:
                box = re.split(r"[\s,]+", attrs.get("viewBox", "").strip())
                if len(box) == 4:
                    w, h = float(box[2]), float(box[3])
                    size = [
                        size[0] or (size[1] * w / h if size[1] and h else w),
                        size[1] or (size[0] * h / w if size[0] and w else h),
                    ]
            if all(size) and all(s > 0 for s in size):
                return size[0], size[1]
            return None
    except (ET.ParseError, ValueError, OSError):
        return None
    return None


class RawConverter(Converter):
    """Camera RAW -> TIFF with LibRaw (camera white balance, sRGB, best interpolation, auto-rotated)."""

    name = "LibRaw"
    engine = "libraw"
    cost = 10

    def pairs(self) -> Iterable[tuple[str, str]]:
        return [("raw", "tiff")]

    def convert(
        self, src: Path, dst: Path, src_fmt: Format, dst_fmt: Format, options: Options, ctx: Context
    ) -> None:
        decoder = engines.get("libraw").tool("dcraw_emu")
        if decoder is None:
            raise ConversionError("The RAW decoder is not installed")
        from morphto.converters.office import _run

        cmd = [str(decoder), "-w", "-o", "1", "-q", "3", "-T", "-Z", str(dst), str(src)]
        result = _run(cmd, ctx, env=_runtime_env())
        if result.returncode != 0 or not dst.is_file():
            detail = (result.stderr or result.stdout or b"").decode("utf-8", "replace").strip()
            raise ConversionError(f"Cannot decode the RAW file: {detail[-300:] or 'unsupported camera?'}")


def _runtime_env() -> dict[str, str]:
    """LibRaw is built with Visual C++; the app already ships its runtime (next to Qt), so point there."""
    env = dict(os.environ)
    base = getattr(sys, "_MEIPASS", None)
    if base:
        dirs = [base, os.path.join(base, "PySide6")]
        env["PATH"] = os.pathsep.join([*dirs, env.get("PATH", "")])
    return env


class JxlConverter(PillowConverter):
    """JPEG XL in both directions, through Pillow with the on-demand pillow-jxl plugin."""

    name = "JPEG XL codec"
    engine = "jxl"

    def pairs(self) -> Iterable[tuple[str, str]]:
        return [(s, "jxl") for s in READ] + [("jxl", d) for d in WRITE]

    def convert(
        self, src: Path, dst: Path, src_fmt: Format, dst_fmt: Format, options: Options, ctx: Context
    ) -> None:
        engines.get("jxl").load()  # importing the plugin registers JPEG XL with Pillow
        super().convert(src, dst, src_fmt, dst_fmt, options, ctx)
