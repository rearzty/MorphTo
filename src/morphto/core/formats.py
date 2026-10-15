"""Registry of file formats MorphTo knows about."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Category(StrEnum):
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"
    DOCUMENT = "document"
    SPREADSHEET = "spreadsheet"
    PRESENTATION = "presentation"


@dataclass(frozen=True, slots=True)
class Format:
    id: str
    name: str
    category: Category
    extensions: tuple[str, ...]  # lowercase, without dot; the first one is used for output files

    @property
    def ext(self) -> str:
        return self.extensions[0]


_FORMATS: dict[str, Format] = {}
_BY_EXT: dict[str, Format] = {}


def register_format(fmt: Format) -> Format:
    _FORMATS[fmt.id] = fmt
    for ext in fmt.extensions:
        _BY_EXT.setdefault(ext, fmt)
    return fmt


def get_format(format_id: str) -> Format:
    try:
        return _FORMATS[format_id.lower().lstrip(".")]
    except KeyError:
        fmt = by_extension(format_id)
        if fmt is None:
            raise KeyError(f"Unknown format: {format_id}") from None
        return fmt


def find_format(format_id: str) -> Format | None:
    try:
        return get_format(format_id)
    except KeyError:
        return None


def by_extension(ext: str) -> Format | None:
    return _BY_EXT.get(ext.lower().lstrip("."))


def all_formats() -> list[Format]:
    return list(_FORMATS.values())


_I, _A, _V, _D = Category.IMAGE, Category.AUDIO, Category.VIDEO, Category.DOCUMENT
_S, _P = Category.SPREADSHEET, Category.PRESENTATION

for _f in [
    # Images
    Format("png", "PNG", _I, ("png", "apng")),
    Format("jpg", "JPEG", _I, ("jpg", "jpeg", "jpe", "jfif")),
    Format("webp", "WebP", _I, ("webp",)),
    Format("avif", "AVIF", _I, ("avif",)),
    Format("heic", "HEIC / HEIF", _I, ("heic", "heif", "hif")),
    Format("gif", "GIF", _I, ("gif",)),
    Format("bmp", "Bitmap", _I, ("bmp", "dib")),
    Format("tiff", "TIFF", _I, ("tiff", "tif")),
    Format("ico", "Windows icon", _I, ("ico",)),
    Format("icns", "macOS icon", _I, ("icns",)),
    Format("tga", "Targa", _I, ("tga",)),
    Format("jp2", "JPEG 2000", _I, ("jp2", "j2k", "jpf")),
    Format("psd", "Photoshop", _I, ("psd",)),
    # Documents
    Format("pdf", "PDF", _D, ("pdf",)),
    Format("docx", "Word document", _D, ("docx",)),
    Format("doc", "Word 97–2003", _D, ("doc",)),
    Format("odt", "OpenDocument text", _D, ("odt",)),
    Format("rtf", "Rich Text", _D, ("rtf",)),
    Format("txt", "Plain text", _D, ("txt",)),
    # Spreadsheets
    Format("xlsx", "Excel workbook", _S, ("xlsx",)),
    Format("xls", "Excel 97–2003", _S, ("xls",)),
    Format("ods", "OpenDocument spreadsheet", _S, ("ods",)),
    Format("csv", "CSV", _S, ("csv",)),
    # Presentations
    Format("pptx", "PowerPoint", _P, ("pptx",)),
    Format("ppt", "PowerPoint 97–2003", _P, ("ppt",)),
    Format("odp", "OpenDocument presentation", _P, ("odp",)),
    # Audio
    Format("mp3", "MP3", _A, ("mp3",)),
    Format("wav", "WAV", _A, ("wav",)),
    Format("flac", "FLAC", _A, ("flac",)),
    Format("ogg", "Ogg Vorbis", _A, ("ogg", "oga")),
    Format("opus", "Opus", _A, ("opus",)),
    Format("m4a", "M4A (AAC)", _A, ("m4a",)),
    Format("aac", "AAC", _A, ("aac",)),
    Format("wma", "Windows Media Audio", _A, ("wma",)),
    Format("aiff", "AIFF", _A, ("aiff", "aif")),
    # Video
    Format("mp4", "MP4 (H.264)", _V, ("mp4", "m4v")),
    Format("mkv", "Matroska", _V, ("mkv",)),
    Format("webm", "WebM (VP9)", _V, ("webm",)),
    Format("mov", "QuickTime", _V, ("mov", "qt")),
    Format("avi", "AVI", _V, ("avi",)),
    Format("wmv", "Windows Media Video", _V, ("wmv", "asf")),
    Format("flv", "Flash Video", _V, ("flv",)),
    Format("mpeg", "MPEG", _V, ("mpg", "mpeg", "vob")),
    Format("ts", "MPEG-TS", _V, ("ts", "mts", "m2ts")),
    Format("3gp", "3GP", _V, ("3gp", "3g2")),
    Format("ogv", "Ogg Video", _V, ("ogv",)),
]:
    register_format(_f)
