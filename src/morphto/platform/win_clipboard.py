"""Direct Win32 clipboard access.

Qt's clipboard goes through OLE and retries for seconds when another program holds the clipboard,
which would freeze the popup. Here every call either succeeds quickly or gives up.
"""

from __future__ import annotations

import ctypes
import io
import time
from contextlib import contextmanager
from ctypes import wintypes
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

CF_BITMAP, CF_DIB, CF_UNICODETEXT, CF_HDROP, CF_DIBV5 = 2, 8, 13, 15, 17
GMEM_MOVEABLE = 0x0002
DROPEFFECT_COPY = 1

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_shell32 = ctypes.WinDLL("shell32", use_last_error=True)

_user32.OpenClipboard.argtypes = [wintypes.HWND]
_user32.OpenClipboard.restype = wintypes.BOOL
_user32.CloseClipboard.restype = wintypes.BOOL
_user32.EmptyClipboard.restype = wintypes.BOOL
_user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
_user32.IsClipboardFormatAvailable.restype = wintypes.BOOL
_user32.GetClipboardData.argtypes = [wintypes.UINT]
_user32.GetClipboardData.restype = ctypes.c_void_p
_user32.SetClipboardData.argtypes = [wintypes.UINT, ctypes.c_void_p]
_user32.SetClipboardData.restype = ctypes.c_void_p
_user32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
_user32.RegisterClipboardFormatW.restype = wintypes.UINT
_user32.GetClipboardSequenceNumber.restype = wintypes.DWORD
_kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
_kernel32.GlobalAlloc.restype = ctypes.c_void_p
_kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
_kernel32.GlobalLock.restype = ctypes.c_void_p
_kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
_kernel32.GlobalSize.argtypes = [ctypes.c_void_p]
_kernel32.GlobalSize.restype = ctypes.c_size_t
_kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
_shell32.DragQueryFileW.argtypes = [ctypes.c_void_p, wintypes.UINT, wintypes.LPWSTR, wintypes.UINT]
_shell32.DragQueryFileW.restype = wintypes.UINT

COPY_IN_GUI_THREAD = False  # Win32 clipboard calls work from the conversion worker
CF_PNG = _user32.RegisterClipboardFormatW("PNG")
CF_DROPEFFECT = _user32.RegisterClipboardFormatW("Preferred DropEffect")
_IMAGE_FORMATS = (CF_PNG, CF_DIBV5, CF_DIB, CF_BITMAP)


@dataclass(frozen=True)
class ClipInfo:
    kind: str  # "files" | "image"
    files: list[Path] = field(default_factory=list)
    image_size: tuple[int, int] | None = None


class ClipboardBusy(Exception):
    pass


@contextmanager
def _opened(attempts: int = 5, delay: float = 0.02):
    for i in range(attempts):
        if _user32.OpenClipboard(None):
            break
        if i < attempts - 1:
            time.sleep(delay)
    else:
        raise ClipboardBusy
    try:
        yield
    finally:
        _user32.CloseClipboard()


def _available(fmt: int) -> bool:
    return bool(_user32.IsClipboardFormatAvailable(fmt))


def sequence_number() -> int:
    return int(_user32.GetClipboardSequenceNumber())


def has_content() -> bool:
    """Instant check (does not open the clipboard): files, an image or text that might be a path."""
    return any(_available(f) for f in (CF_HDROP, CF_UNICODETEXT, *_IMAGE_FORMATS))


def _global_bytes(handle: int | None) -> bytes | None:
    if not handle:
        return None
    ptr = _kernel32.GlobalLock(handle)
    if not ptr:
        return None
    try:
        return ctypes.string_at(ptr, _kernel32.GlobalSize(handle))
    finally:
        _kernel32.GlobalUnlock(handle)


def _files_unlocked() -> list[Path]:
    handle = _user32.GetClipboardData(CF_HDROP)
    if not handle:
        return []
    count = _shell32.DragQueryFileW(handle, 0xFFFFFFFF, None, 0)
    files = []
    for i in range(count):
        length = _shell32.DragQueryFileW(handle, i, None, 0)
        buf = ctypes.create_unicode_buffer(length + 1)
        _shell32.DragQueryFileW(handle, i, buf, length + 1)
        files.append(Path(buf.value))
    return files


def _text_path_unlocked() -> Path | None:
    data = _global_bytes(_user32.GetClipboardData(CF_UNICODETEXT))
    if not data or len(data) > 4096:
        return None
    text = data.decode("utf-16-le", "ignore").split("\0", 1)[0].strip().strip('"')
    if not text or "\n" in text:
        return None
    path = Path(text)
    try:
        return path if path.is_file() else None
    except OSError:
        return None


def _image_size_unlocked() -> tuple[int, int] | None:
    if _available(CF_PNG):
        data = _global_bytes(_user32.GetClipboardData(CF_PNG))
        if data and len(data) >= 24 and data.startswith(b"\x89PNG"):
            return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    for fmt in (CF_DIBV5, CF_DIB):
        if _available(fmt):
            data = _global_bytes(_user32.GetClipboardData(fmt))
            if data and len(data) >= 12:
                width = int.from_bytes(data[4:8], "little", signed=True)
                height = int.from_bytes(data[8:12], "little", signed=True)
                return abs(width), abs(height)
    return None


def peek() -> ClipInfo | None:
    """What a paste would give us, for the hint line. Returns quickly; None if nothing usable."""
    if not has_content():
        return None
    try:
        with _opened(attempts=1):
            if _available(CF_HDROP):
                files = [f for f in _files_unlocked() if f.is_file()]
                if files:
                    return ClipInfo("files", files)
            if any(_available(f) for f in _IMAGE_FORMATS):
                return ClipInfo("image", image_size=_image_size_unlocked())
            if _available(CF_UNICODETEXT):
                path = _text_path_unlocked()
                if path:
                    return ClipInfo("files", [path])
    except ClipboardBusy:
        if any(_available(f) for f in _IMAGE_FORMATS):
            return ClipInfo("image")
    return None


def read() -> list[Path] | Image.Image | None:
    """Files (list of paths) or an image from the clipboard; None if there is nothing usable."""
    with _opened():
        if _available(CF_HDROP):
            files = [f for f in _files_unlocked() if f.is_file()]
            if files:
                return files
        if _available(CF_PNG):
            data = _global_bytes(_user32.GetClipboardData(CF_PNG))
            if data:
                try:
                    image = Image.open(io.BytesIO(data))
                    image.load()
                    return image
                except OSError:
                    pass
        for fmt in (CF_DIBV5, CF_DIB):
            if _available(fmt):
                data = _global_bytes(_user32.GetClipboardData(fmt))
                if data:
                    image = _dib_to_image(data)
                    if image is not None:
                        return image
        if _available(CF_UNICODETEXT):
            path = _text_path_unlocked()
            if path:
                return [path]
    return None


def _dib_to_image(dib: bytes) -> Image.Image | None:
    """Wrap a packed DIB in a BMP file header so Pillow can decode it."""
    header_size = int.from_bytes(dib[0:4], "little")
    bit_count = int.from_bytes(dib[14:16], "little")
    compression = int.from_bytes(dib[16:20], "little")
    if compression in (4, 5):  # BI_JPEG / BI_PNG: a whole JPEG or PNG file follows the header
        try:
            image = Image.open(io.BytesIO(dib[header_size:]))
            image.load()
            return image
        except OSError:
            return None
    colors_used = int.from_bytes(dib[32:36], "little")
    masks = 12 if compression == 3 and header_size == 40 else 0  # BI_BITFIELDS after a v1 header
    palette = (colors_used or (1 << bit_count if bit_count <= 8 else 0)) * 4
    offset = 14 + header_size + masks + palette
    bmp = b"BM" + (14 + len(dib)).to_bytes(4, "little") + b"\0\0\0\0" + offset.to_bytes(4, "little") + dib
    try:
        image = Image.open(io.BytesIO(bmp))
        image.load()
    except OSError:
        return None
    if image.mode == "RGBA" and image.getextrema()[3] == (0, 0):
        image = image.convert("RGB")  # many apps leave alpha at 0 in 32-bit DIBs
    return image


def _set(fmt: int, data: bytes) -> None:
    handle = _kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
    if not handle:
        raise MemoryError
    ptr = _kernel32.GlobalLock(handle)
    ctypes.memmove(ptr, data, len(data))
    _kernel32.GlobalUnlock(handle)
    if not _user32.SetClipboardData(fmt, handle):
        _kernel32.GlobalFree(handle)
        raise OSError(ctypes.get_last_error(), "SetClipboardData failed")


def write_files(paths: list[Path], with_image: bool = True) -> bool:
    """Put files on the clipboard (paste in Explorer, chats...). A single image also goes as pixels."""
    files = "".join(str(p.resolve()) + "\0" for p in paths) + "\0"
    dropfiles = (20).to_bytes(4, "little") + b"\0" * 12 + (1).to_bytes(4, "little")  # pFiles, pt, fNC, fWide
    image_png = image_dib = None
    if with_image and len(paths) == 1:
        image_png, image_dib = _encode_image(paths[0])
    try:
        with _opened(attempts=10, delay=0.03):
            _user32.EmptyClipboard()
            _set(CF_HDROP, dropfiles + files.encode("utf-16-le"))
            _set(CF_DROPEFFECT, DROPEFFECT_COPY.to_bytes(4, "little"))
            if image_png:
                _set(CF_PNG, image_png)
            if image_dib:
                _set(CF_DIB, image_dib)
    except (ClipboardBusy, OSError, MemoryError):
        return False
    return True


def _encode_image(path: Path) -> tuple[bytes | None, bytes | None]:
    try:
        with Image.open(path) as im:
            if im.width * im.height > 40_000_000:
                return None, None
            im.seek(0)
            rgba = im.convert("RGBA")
    except Exception:
        return None, None
    png = io.BytesIO()
    rgba.save(png, "PNG", compress_level=1)
    bmp = io.BytesIO()
    rgba.convert("RGB").save(bmp, "BMP")
    return png.getvalue(), bmp.getvalue()[14:]
