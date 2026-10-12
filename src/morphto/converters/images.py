"""Image conversion with Pillow (+ pillow-heif for HEIC)."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from PIL import Image, ImageOps, ImageSequence

from morphto.core.formats import Format
from morphto.core.options import Options
from morphto.core.registry import Context, Converter

# HEIC reading ships with the app via the light, decoder-only "pi-heif". Writing HEIC needs the x265
# encoder (pillow-heif, ~20 MB unpacked), so it is an engine downloaded on first use: see HeifWriter.
try:
    import pi_heif as _heif_reader
except ImportError:  # pragma: no cover - dev setups that only have pillow-heif
    import pillow_heif as _heif_reader
_heif_reader.register_heif_opener()

READ = {"png", "jpg", "webp", "avif", "gif", "bmp", "tiff", "ico", "icns", "tga", "jp2", "psd", "heic"}
WRITE = {"png", "jpg", "webp", "avif", "gif", "bmp", "tiff", "ico", "icns", "tga", "jp2", "pdf"}

# Pillow's names for our format ids.
_PIL_FORMAT = {
    "png": "PNG", "jpg": "JPEG", "webp": "WEBP", "avif": "AVIF", "heic": "HEIF", "gif": "GIF",
    "bmp": "BMP", "tiff": "TIFF", "ico": "ICO", "icns": "ICNS", "tga": "TGA", "jp2": "JPEG2000",
    "pdf": "PDF",
}  # fmt: skip
_NO_ALPHA = {"jpg", "bmp", "pdf", "jp2"}
_ANIMATED = {"gif", "webp", "png", "avif"}
_MULTIPAGE = {"pdf", "tiff"}
_KEEP_META = {"jpg", "webp", "png", "tiff", "avif", "heic"}
_DEFAULT_QUALITY = {"jpg": 90, "webp": 90, "avif": 75, "heic": 85, "jp2": 90}
_ICO_SIZES = [16, 24, 32, 48, 64, 128, 256]


class PillowConverter(Converter):
    name = "Pillow"
    cost = 10

    def pairs(self) -> Iterable[tuple[str, str]]:
        return [(s, d) for s in READ for d in WRITE]

    def convert(
        self, src: Path, dst: Path, src_fmt: Format, dst_fmt: Format, options: Options, ctx: Context
    ) -> None:
        target = dst_fmt.id
        with Image.open(src) as im:
            n_frames = getattr(im, "n_frames", 1)
            multi = n_frames > 1 and (target in _ANIMATED or target in _MULTIPAGE)
            frames = [f.copy() for f in ImageSequence.Iterator(im)] if multi else [im.copy()]
            info = dict(im.info)
            exif = im.getexif() if target in _KEEP_META else None
            ctx.progress(0.3)

            prepared = []
            for i, frame in enumerate(frames):
                ctx.check_cancelled()
                frame = ImageOps.exif_transpose(frame) if i == 0 and not multi else frame
                if options.max_side:
                    frame.thumbnail((options.max_side, options.max_side), Image.Resampling.LANCZOS)
                prepared.append(_fit_mode(frame, target))
            ctx.progress(0.6)

        save = _save_params(target, options, info, exif, transposed=not multi)
        first, rest = prepared[0], prepared[1:]
        if target == "ico":
            first = _square(first)
            save["sizes"] = [(s, s) for s in _ICO_SIZES if s <= max(first.size)] or [first.size]
        if target == "icns":
            first = _square(first)
        if rest:
            save.update(save_all=True, append_images=rest)
            if target in _ANIMATED:
                save.setdefault("duration", info.get("duration", 100))
                save.setdefault("loop", info.get("loop", 0))
        first.save(dst, format=_PIL_FORMAT[target], **save)
        ctx.progress(1.0)


def _fit_mode(im: Image.Image, target: str) -> Image.Image:
    has_alpha = im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info)
    if target in _NO_ALPHA:
        if has_alpha:
            rgba = im.convert("RGBA")
            background = Image.new("RGB", rgba.size, (255, 255, 255))
            background.paste(rgba, mask=rgba.getchannel("A"))
            return background
        return im if im.mode in ("RGB", "L") else im.convert("RGB")
    if target == "gif":
        return im if im.mode in ("P", "L") else im.convert("RGBA" if has_alpha else "RGB")
    if im.mode in ("RGB", "RGBA", "L", "LA") or (target in ("png", "tiff", "bmp", "tga") and im.mode == "P"):
        return im
    return im.convert("RGBA" if has_alpha else "RGB")


def _square(im: Image.Image) -> Image.Image:
    w, h = im.size
    if w == h:
        return im
    side = max(w, h)
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(im.convert("RGBA"), ((side - w) // 2, (side - h) // 2))
    return canvas


def _save_params(
    target: str, options: Options, info: dict, exif: Image.Exif | None, *, transposed: bool
) -> dict:
    params: dict = {}
    if target in _DEFAULT_QUALITY:
        quality = options.quality or _DEFAULT_QUALITY[target]
        if target == "jp2":
            params.update(quality_mode="dB", quality_layers=[20 + quality * 0.3])
        else:
            params["quality"] = quality
    if target == "jpg":
        params.update(optimize=True, progressive=True)
    if target == "png":
        params["optimize"] = False  # optimize=True is very slow on big images for ~5% gain
    if target == "webp":
        params["method"] = 4
    if target in _KEEP_META:
        icc = info.get("icc_profile")
        if icc:
            params["icc_profile"] = icc
        if exif:
            if transposed:
                exif.pop(0x0112, None)  # orientation was already applied to the pixels
            params["exif"] = exif.tobytes()
    if target == "tiff":
        params["compression"] = "tiff_lzw"
    return params


class HeifWriter(PillowConverter):
    """Saving to HEIC: same pipeline as PillowConverter, with the on-demand pillow-heif encoder."""

    name = "HEIC encoder"
    engine = "heif"

    def pairs(self) -> Iterable[tuple[str, str]]:
        return [(s, "heic") for s in READ]

    def convert(
        self, src: Path, dst: Path, src_fmt: Format, dst_fmt: Format, options: Options, ctx: Context
    ) -> None:
        from morphto import engines

        engines.get("heif").load().register_heif_opener()  # adds HEIF saving to Pillow
        super().convert(src, dst, src_fmt, dst_fmt, options, ctx)
