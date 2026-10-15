"""PDF pages to images, and PDF text extraction, with PDFium (pypdfium2)."""

from __future__ import annotations

import threading
from collections.abc import Iterable
from pathlib import Path

from morphto.core.formats import Format
from morphto.core.options import Options
from morphto.core.registry import Context, ConversionError, Converter

IMAGES = ("png", "jpg", "webp", "bmp", "tiff")
DPI = 150  # sharp on screen and in chats, without making 50-page documents huge
_PIL_FORMAT = {"png": "PNG", "jpg": "JPEG", "webp": "WEBP", "bmp": "BMP", "tiff": "TIFF"}
_DEFAULT_QUALITY = {"jpg": 90, "webp": 90}

# PDFium is not thread-safe; conversions normally run one at a time anyway.
_lock = threading.Lock()


class PdfiumConverter(Converter):
    """``pdf -> png/jpg/...``: one image for a one-page PDF, otherwise a folder of pages
    (``page-01.png`` ...). TIFF keeps all pages in a single file. ``pdf -> txt`` extracts the text."""

    name = "PDFium"
    cost = 10

    def pairs(self) -> Iterable[tuple[str, str]]:
        return [("pdf", d) for d in (*IMAGES, "txt")]

    def convert(
        self, src: Path, dst: Path, src_fmt: Format, dst_fmt: Format, options: Options, ctx: Context
    ) -> None:
        import pypdfium2 as pdfium

        with _lock:
            try:
                pdf = pdfium.PdfDocument(src)
            except pdfium.PdfiumError as exc:
                raise ConversionError(f"Cannot open the PDF (damaged or password-protected): {exc}") from exc
            try:
                if dst_fmt.id == "txt":
                    self._text(pdf, dst, ctx)
                else:
                    self._images(pdf, dst, dst_fmt.id, options, ctx)
            finally:
                pdf.close()

    @staticmethod
    def _text(pdf, dst: Path, ctx: Context) -> None:
        parts = []
        for i in range(len(pdf)):
            ctx.check_cancelled()
            page = pdf[i]
            textpage = page.get_textpage()
            parts.append(textpage.get_text_range().replace("\r\n", "\n"))
            textpage.close()
            page.close()
            ctx.progress((i + 1) / len(pdf))
        dst.write_text("\n\f\n".join(parts), encoding="utf-8")

    @staticmethod
    def _images(pdf, dst: Path, target: str, options: Options, ctx: Context) -> None:
        count = len(pdf)
        if count == 0:
            raise ConversionError("The PDF has no pages")
        pages = []
        save_args: dict = {}
        if target in _DEFAULT_QUALITY:
            save_args["quality"] = options.quality or _DEFAULT_QUALITY[target]
        if target == "png":
            save_args["optimize"] = False  # fast; PDF renders compress well anyway
        folder = None
        if count > 1 and target != "tiff":
            folder = dst
            folder.mkdir()
        digits = max(2, len(str(count)))
        for i in range(count):
            ctx.check_cancelled()
            page = pdf[i]
            width, height = page.get_size()  # in points (1/72 inch)
            scale = DPI / 72
            if options.max_side:
                scale = min(scale, options.max_side / max(width, height, 1))
            image = page.render(scale=scale).to_pil()
            page.close()
            if target == "jpg" or target == "bmp":
                image = image.convert("RGB")
            if folder is not None:
                image.save(folder / f"page-{i + 1:0{digits}d}.{target}", _PIL_FORMAT[target], **save_args)
            elif target == "tiff":
                pages.append(image)
            else:
                image.save(dst, _PIL_FORMAT[target], **save_args)
            ctx.progress((i + 1) / count * (0.9 if pages else 1.0))
        if pages:
            pages[0].save(dst, "TIFF", save_all=True, append_images=pages[1:], compression="tiff_deflate")
