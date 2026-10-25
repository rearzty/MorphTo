"""Archives: unpack into a folder, or repack into another archive format.

ZIP, TAR and TAR.GZ are handled by Python itself. 7Z and RAR go through the tar.exe that ships with
Windows (libarchive); it reads both and writes 7Z. Nothing is ever written outside the target folder:
names with ".." or absolute paths are refused by both.
"""

from __future__ import annotations

import gzip
import lzma
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from collections.abc import Iterable
from pathlib import Path

from morphto.core.formats import Format
from morphto.core.options import Options
from morphto.core.registry import Context, ConversionError, Converter

READ = ("zip", "7z", "tar", "tgz", "rar")
WRITE = ("zip", "7z", "tar", "tgz")

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
if sys.platform == "win32":
    _TAR = Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32" / "tar.exe"
elif sys.platform == "darwin":
    _TAR = Path("/usr/bin/tar")  # bsdtar
else:
    _TAR = Path(shutil.which("bsdtar") or "/usr/bin/bsdtar")  # the libarchive-tools package


class ArchiveConverter(Converter):
    name = "MorphTo archives"
    cost = 10

    def pairs(self) -> Iterable[tuple[str, str]]:
        return [(s, "folder") for s in READ] + [(s, d) for s in READ for d in WRITE if s != d]

    def convert(
        self, src: Path, dst: Path, src_fmt: Format, dst_fmt: Format, options: Options, ctx: Context
    ) -> None:
        if dst_fmt.id == "folder":
            dst.mkdir()
            extract(src, src_fmt.id, dst, ctx)
            return
        with tempfile.TemporaryDirectory(prefix="morphto-archive-") as tmp:
            folder = Path(tmp)
            extract(src, src_fmt.id, folder, ctx)
            ctx.check_cancelled()
            ctx.progress(0.6)
            pack(folder, dst, dst_fmt.id, ctx)


def extract(src: Path, fmt: str, dest: Path, ctx: Context) -> None:
    try:
        if fmt == "zip":
            with zipfile.ZipFile(src) as zf:
                if any(info.flag_bits & 0x1 for info in zf.infolist()):
                    raise ConversionError("The archive is password-protected")
                zf.extractall(dest)  # zipfile drops ".." and drive letters from member names
        elif fmt in ("tar", "tgz"):
            if fmt == "tgz" and not _is_tar(src):
                # a single gzip-compressed file (log.gz), not a .tar.gz
                name = src.stem if src.suffix.lower() == ".gz" and not src.stem.startswith("step") else "file"
                with gzip.open(src) as f_in, (dest / name).open("wb") as f_out:
                    shutil.copyfileobj(f_in, f_out, 1 << 20)
            else:
                with tarfile.open(src) as tf:
                    tf.extractall(dest, filter="data")
        else:
            if fmt == "7z" and _read_7z_ourselves(src, dest, ctx):
                return
            try:
                _run_tar(["-xf", str(src), "-C", str(dest)], ctx, fmt)
            except ConversionError as exc:
                # Links from Linux archives cannot be created on Windows; keep everything else.
                if "Can't create" not in str(exc) or not any(dest.iterdir()):
                    raise
    except (zipfile.BadZipFile, tarfile.TarError, OSError, EOFError) as exc:
        raise ConversionError(f"Cannot unpack the archive: {exc}") from exc
    if not any(dest.iterdir()):
        raise ConversionError("The archive is empty")


def _read_7z_ourselves(src: Path, dest: Path, ctx: Context) -> bool:
    """True when our reader unpacked it; False to let tar try (PPMd, BCJ2...)."""
    from morphto.converters.sevenzip_read import PasswordProtected, Unsupported, read_7z

    try:
        read_7z(src, dest, ctx.check_cancelled)
    except PasswordProtected:
        raise ConversionError("The archive is password-protected") from None
    except (Unsupported, lzma.LZMAError):
        for item in dest.iterdir():  # start clean for tar
            shutil.rmtree(item) if item.is_dir() else item.unlink()
        return False
    if not any(dest.iterdir()):
        raise ConversionError("The archive is empty")
    return True


def _is_tar(path: Path) -> bool:
    try:
        with tarfile.open(path):
            return True
    except (tarfile.TarError, OSError, EOFError):
        return False


def pack(folder: Path, dst: Path, fmt: str, ctx: Context) -> None:
    entries = sorted(folder.iterdir())
    if fmt == "zip":
        with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for path in sorted(folder.rglob("*")):
                ctx.check_cancelled()
                zf.write(path, path.relative_to(folder).as_posix())
    elif fmt in ("tar", "tgz"):
        with tarfile.open(dst, "w:gz" if fmt == "tgz" else "w", format=tarfile.PAX_FORMAT) as tf:
            for path in entries:
                tf.add(path, path.name)
    else:  # 7z
        from morphto.converters.sevenzip import write_7z

        write_7z(folder, dst, ctx.check_cancelled)


def _run_tar(args: list[str], ctx: Context, fmt: str) -> None:
    if not _TAR.is_file():
        if sys.platform == "win32":
            raise ConversionError(
                f"{fmt.upper()} archives need tar.exe, which comes with Windows 10 1803 and later"
            )
        raise ConversionError(f"{fmt.upper()} archives need bsdtar: sudo apt install libarchive-tools")
    from morphto.converters.office import _run

    result = _run([str(_TAR), *args], ctx)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or b"").decode("utf-8", "replace").strip()
        detail = detail or f"tar exit code {result.returncode}"
        if "passphrase" in detail.lower() or "encrypt" in detail.lower():
            raise ConversionError("The archive is password-protected")
        raise ConversionError(f"Cannot process the {fmt.upper()} archive: {detail[-300:]}")
