"""Archives: unpack into a folder, or repack into another archive format.

ZIP, TAR and TAR.GZ are handled by Python itself. 7Z and RAR go through the tar.exe that ships with
Windows (libarchive); it reads both and writes 7Z. Nothing is ever written outside the target folder:
names with ".." or absolute paths are refused by both.
"""

from __future__ import annotations

import gzip
import os
import shutil
import subprocess
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
_TAR = Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32" / "tar.exe"


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
        listing = folder.parent / f"{folder.name}-files.txt"
        listing.write_text("\n".join(p.name for p in entries) + "\n", encoding="utf-8")
        try:
            _run_tar(["-cf", str(dst), "--format", "7zip", "-C", str(folder), "-T", str(listing)], ctx, fmt)
        finally:
            listing.unlink(missing_ok=True)


def _run_tar(args: list[str], ctx: Context, fmt: str) -> None:
    if not _TAR.is_file():
        raise ConversionError(
            f"{fmt.upper()} archives need tar.exe, which comes with Windows 10 1803 and later"
        )
    from morphto.converters.office import _run

    result = _run([str(_TAR), *args], ctx)
    if result.returncode != 0:
        detail = (result.stderr or b"").decode("utf-8", "replace").strip()
        if "passphrase" in detail.lower() or "encrypt" in detail.lower():
            raise ConversionError("The archive is password-protected")
        raise ConversionError(f"Cannot process the {fmt.upper()} archive: {detail[-300:]}")
