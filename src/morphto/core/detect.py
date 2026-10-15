"""Detect a file's real format from its signature, falling back to the extension."""

from __future__ import annotations

from pathlib import Path

from morphto.core.formats import Format, by_extension, find_format

_HEAD_SIZE = 512

_HEIF_BRANDS = {b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"mif1", b"msf1"}
_3GP_BRANDS = {b"3gp4", b"3gp5", b"3gp6", b"3g2a", b"3g2b", b"3g2c"}


def detect(path: str | Path) -> Format | None:
    """Return the format of ``path`` or ``None`` when it is not a supported file."""
    path = Path(path)
    try:
        with path.open("rb") as f:
            head = f.read(_HEAD_SIZE)
    except OSError:
        return None
    ext = path.suffix.lower().lstrip(".")
    fmt_id = _sniff_zip(path, ext) if head.startswith(b"PK\x03\x04") else sniff(head, ext)
    if fmt_id is not None:
        return find_format(fmt_id)
    fmt = by_extension(path.suffix)
    if fmt is not None and fmt.id == "ts":
        return None  # ".ts" without the MPEG-TS sync bytes is almost certainly TypeScript
    return fmt


_RAW_EXTS = {
    "dng", "cr2", "cr3", "nef", "nrw", "arw", "srf", "sr2", "orf", "rw2", "raf", "pef", "srw", "x3f",
    "3fr", "erf", "kdc", "mrw", "rwl", "iiq", "raw",
}  # fmt: skip
_TEXT_EXTS = {"md", "markdown", "txt", "csv", "json", "yaml", "yml"}


def sniff(head: bytes, ext: str = "") -> str | None:
    """Guess a format id from the first bytes of a file."""
    if ext in _RAW_EXTS and not head.startswith((b"\xff\xd8\xff", b"\x89PNG")):
        return "raw"  # camera RAW files are mostly TIFF inside, so the extension decides
    if head.startswith(b"\xff\x0a") or head.startswith(b"\x00\x00\x00\x0cJXL \r\n\x87\n"):
        return "jxl"
    if head.startswith(b"7z\xbc\xaf\x27\x1c"):
        return "7z"
    if head.startswith(b"Rar!\x1a\x07"):
        return "rar"
    if head[257:262] == b"ustar":
        return "tar"
    if head.startswith(b"\x1f\x8b") and ext in ("tgz", "gz"):
        return "tgz"
    if ext not in _TEXT_EXTS:
        markup = _sniff_markup(head, ext)
        if markup:
            return markup
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(b"{\\rtf"):
        return "rtf"
    if head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        # OLE2 container: Word/Excel/PowerPoint 97-2003 (and MSI, MSG...): trust the extension
        return ext if ext in ("doc", "xls", "ppt") else None
    if head.startswith(b"8BPS"):
        return "psd"
    if head.startswith(b"icns"):
        return "icns"
    if head.startswith(b"\x00\x00\x00\x0cjP  \r\n\x87\n") or head.startswith(b"\xff\x4f\xff\x51"):
        return "jp2"
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return "tiff"
    if head.startswith(b"\x00\x00\x01\x00") and ext in ("ico", ""):
        return "ico"
    if head.startswith(b"BM") and ext in ("bmp", "dib", ""):
        return "bmp"
    if head[:4] == b"RIFF":
        kind = head[8:12]
        return {b"WEBP": "webp", b"WAVE": "wav", b"AVI ": "avi"}.get(kind)
    if head[:4] == b"FORM" and head[8:12] in (b"AIFF", b"AIFC"):
        return "aiff"
    if head[4:8] == b"ftyp":
        return _sniff_isobmff(head, ext)
    if head.startswith(b"\x1a\x45\xdf\xa3"):
        return "webm" if b"webm" in head[:64] else "mkv"
    if head.startswith(b"OggS"):
        if b"OpusHead" in head:
            return "opus"
        if b"theora" in head:
            return "ogv"
        return "ogg"
    if head.startswith(b"fLaC"):
        return "flac"
    if head.startswith(b"FLV"):
        return "flv"
    if head.startswith(b"\x30\x26\xb2\x75\x8e\x66\xcf\x11"):
        return "wma" if ext == "wma" else "wmv"
    if head.startswith(b"\x00\x00\x01\xba"):
        return "mpeg"
    if len(head) > 188 and head[0] == 0x47 and head[188] == 0x47:
        return "ts"
    if head.startswith(b"ID3"):
        return "aac" if ext == "aac" else "mp3"
    if len(head) >= 2 and head[0] == 0xFF and head[1] & 0xF6 == 0xF0:
        return "aac"  # ADTS header
    if len(head) >= 2 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0:
        return "mp3"  # MPEG audio frame sync
    return None


def _sniff_markup(head: bytes, ext: str) -> str | None:
    """XML-based files: SVG, FictionBook, (X)HTML, other XML."""
    text = head.lstrip(b"\xef\xbb\xbf \t\r\n")
    if not text.startswith(b"<"):
        return None
    lower = text[:512].lower()
    if b"<svg" in lower:
        return "svg"
    if b"<fictionbook" in lower:
        return "fb2"
    if lower.startswith((b"<!doctype html", b"<html")) or b"<html" in lower:
        return "html"
    if text.startswith(b"<?xml"):
        return ext if ext in ("svg", "fb2", "html", "xhtml") else "xml"
    return None


_ODF_MIMETYPES = {
    b"application/vnd.oasis.opendocument.text": "odt",
    b"application/vnd.oasis.opendocument.spreadsheet": "ods",
    b"application/vnd.oasis.opendocument.presentation": "odp",
    b"application/epub+zip": "epub",
}
_OOXML_PARTS = {"word/document.xml": "docx", "xl/workbook.xml": "xlsx", "ppt/presentation.xml": "pptx"}


def _sniff_zip(path: Path, ext: str) -> str | None:
    """ZIP-based documents: Office Open XML (docx/xlsx/pptx) and OpenDocument (odt/ods/odp)."""
    import zipfile

    try:
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
            if "mimetype" in names:
                fmt = _ODF_MIMETYPES.get(zf.read("mimetype").strip())
                if fmt:
                    return fmt
            for part, fmt in _OOXML_PARTS.items():
                if part in names:
                    return fmt
    except (OSError, zipfile.BadZipFile, KeyError):
        return None
    return "zip"  # any other ZIP is an archive


def _sniff_isobmff(head: bytes, ext: str) -> str:
    size = int.from_bytes(head[:4], "big")
    major = head[8:12]
    brands = {major} | {head[i : i + 4] for i in range(16, min(size, len(head)) - 3, 4)}
    if b"avif" in brands or b"avis" in brands:
        return "avif"
    if brands & _HEIF_BRANDS:
        return "heic"
    if major in (b"M4A ", b"M4B "):
        return "m4a"
    if major == b"crx ":
        return "raw"  # Canon CR3
    if major == b"qt  ":
        return "mov"
    if major in _3GP_BRANDS:
        return "3gp"
    if ext == "m4a":
        return "m4a"
    return "mp4"
