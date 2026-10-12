"""Build the Windows app: dist/MorphTo/ with MorphTo.exe (windowed) and morphto.com (console).

Usage:  python packaging/build.py            (needs `pip install pyinstaller`)

PyInstaller is used rather than Nuitka: with Python 3.14 + PySide6 6.12, the Nuitka-compiled GUI
corrupted memory (random access violations), while the same code is stable as regular bytecode.
Size is kept down by excluding what we never use and pruning Qt files after the build.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from io import BytesIO
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
DIST = ROOT / "dist" / "MorphTo"
sys.path.insert(0, str(ROOT / "src"))

from morphto import APP_NAME, __version__  # noqa: E402

# Python modules the app never imports at run time (or that only matter for HTTPS / zstd / HEIC writing).
EXCLUDE_MODULES = [
    "tkinter", "unittest", "pydoc", "doctest", "pytest", "_pytest", "setuptools", "pip", "nuitka",
    "ssl", "_ssl", "_hashlib", "compression.zstd", "_zstd", "pillow_heif", "PySide6.QtNetwork",
]  # fmt: skip

# Files removed from the bundle after the build (paths relative to the _internal folder).
PRUNE = [
    "PySide6/opengl32sw.dll",  # software OpenGL, 20 MB
    "PySide6/Qt6Network.dll",  # IPC uses a plain named pipe
    "PySide6/Qt6Svg.dll",
    "PySide6/translations",  # UI strings are ours; file dialogs are native and localized by Windows
    "PySide6/plugins/imageformats",  # images are decoded by Pillow
    "PySide6/plugins/iconengines",
    "PySide6/plugins/generic",
    "PySide6/plugins/platforms/qdirect2d.dll",
    "PySide6/plugins/platforms/qminimal.dll",
    "PySide6/plugins/platforms/qoffscreen.dll",
]


def make_icon() -> Path:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PIL import Image
    from PySide6.QtCore import QBuffer, QIODevice
    from PySide6.QtGui import QGuiApplication

    from morphto.gui.icon import render

    _app = QGuiApplication.instance() or QGuiApplication([])
    images = []
    for size in (256, 128, 64, 48, 40, 32, 24, 20, 16):
        buf = QBuffer()
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        render(size).save(buf, "PNG")
        images.append(Image.open(BytesIO(bytes(buf.data()))).convert("RGBA"))
    out = BUILD / "MorphTo.ico"
    out.parent.mkdir(parents=True, exist_ok=True)
    images[0].save(out, format="ICO", sizes=[im.size for im in images], append_images=images[1:])
    return out


def make_version_file() -> Path:
    v = tuple(int(x) for x in [*__version__.split("."), "0", "0", "0"][:4])
    text = f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={v}, prodvers={v}, mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'rearzty'),
      StringStruct('FileDescription', '{APP_NAME}'),
      StringStruct('FileVersion', '{__version__}'),
      StringStruct('InternalName', '{APP_NAME}'),
      StringStruct('LegalCopyright', 'Copyright (C) 2026 rearzty. GPL-3.0-or-later.'),
      StringStruct('OriginalFilename', '{APP_NAME}.exe'),
      StringStruct('ProductName', '{APP_NAME}'),
      StringStruct('ProductVersion', '{__version__}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""
    out = BUILD / "version.txt"
    out.write_text(text, encoding="utf-8")
    return out


def pyinstaller(icon: Path, version: Path) -> Path:
    work = BUILD / "pyinstaller"
    cmd = [
        sys.executable, "-m", "PyInstaller", str(ROOT / "packaging" / "entry.py"),
        "--name", APP_NAME, "--onedir", "--windowed", "--noconfirm", "--clean", "--log-level", "WARN",
        "--icon", str(icon), "--version-file", str(version),
        "--distpath", str(work / "dist"), "--workpath", str(work / "work"), "--specpath", str(work),
        "--paths", str(ROOT / "src"), "--collect-data", "morphto",
        *[arg for module in EXCLUDE_MODULES for arg in ("--exclude-module", module)],
    ]  # fmt: skip
    subprocess.run(cmd, check=True, cwd=ROOT)
    return work / "dist" / APP_NAME


def prune(dist: Path) -> None:
    internal = dist / "_internal"
    for rel in PRUNE:
        target = internal / rel
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()


def find_c_compiler() -> list[str] | None:
    """Any C compiler for the tiny console stub: zig (e.g. from Nuitka's cache), gcc or clang."""
    for name, args in (("zig", ["cc"]), ("gcc", []), ("clang", [])):
        if shutil.which(name):
            return [name, *args]
    cache = Path(os.environ.get("LOCALAPPDATA", "")) / "Nuitka" / "Nuitka" / "Cache" / "downloads"
    if cache.is_dir():
        for name, args in (("zig.exe", ["cc"]), ("gcc.exe", [])):
            found = next(iter(sorted(cache.rglob(name))), None)
            if found:
                return [str(found), *args]
    return None


def build_stub(out_dir: Path) -> None:
    compiler = find_c_compiler()
    if compiler is None:
        print("! no C compiler found (install zig: `pip install ziglang`), skipping morphto.com")
        return
    args = [
        *compiler,
        "-O2",
        "-s",
        "-municode",
        "-o",
        str(out_dir / "morphto.com"),
        str(ROOT / "packaging" / "cli_stub.c"),
    ]
    if compiler[-1] == "cc":  # zig
        args[2:2] = ["-target", "x86_64-windows-gnu"]
    subprocess.run(args, check=True)


# Components shipped inside the app: (distribution, short license, license files to include in full).
BUNDLED = [
    ("PySide6-Essentials", "LGPL-3.0 (Qt for Python and Qt 6)", []),
    ("shiboken6", "LGPL-3.0", []),
    ("pillow", "MIT-CMU, with the licenses of its bundled image libraries", ["LICENSE"]),
    ("pi-heif", "BSD-3-Clause; libheif and libde265 are LGPL-3.0", ["LICENSE.txt", "LICENSES_bundled.txt"]),
    ("pyinstaller", "GPL-2.0-or-later with the bootloader exception (only its bootloader is shipped)", ["COPYING.txt"]),
]  # fmt: skip


def make_notices(dest: Path) -> None:
    """THIRD-PARTY-NOTICES.txt: what is bundled, under which license, plus the full license texts."""
    from importlib.metadata import distribution

    def section(title: str, body: str) -> str:
        return f"\n\n{'=' * 78}\n{title}\n{'=' * 78}\n\n{body.strip()}\n"

    import platform

    lines = [
        f"{APP_NAME} {__version__} is licensed under the GNU General Public License v3.0 or later (LICENSE.txt).",
        "It bundles the following third-party software:",
        "",
        f"  - Python {platform.python_version()} — Python Software Foundation License",
    ]
    texts: list[str] = []
    for name, license_name, files in BUNDLED:
        dist = distribution(name)
        lines.append(f"  - {dist.metadata['Name']} {dist.version} — {license_name}")
        for wanted in files:
            for f in dist.files or []:
                if f.name == wanted:
                    texts.append(
                        section(
                            f"{dist.metadata['Name']} {dist.version}: {wanted}", f.read_text(encoding="utf-8")
                        )
                    )
                    break
    lines += [
        "",
        "Optional components downloaded on first use (not part of this package):",
        "  - FFmpeg (www.gyan.dev builds) — GPL-3.0",
        "  - pillow-heif, the HEIC encoder (from PyPI) — BSD-3-Clause; libheif/libde265 LGPL-3.0, x265 GPL-2.0",
        "",
        "Qt and Qt for Python are used as unmodified dynamically linked libraries, so they can be replaced by",
        "the user, as the LGPL requires. Their source code is available from https://download.qt.io/.",
    ]
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if python_license.exists():
        texts.insert(0, section("Python", python_license.read_text(encoding="utf-8", errors="replace")))
    texts.insert(0, section("GNU Lesser General Public License v3.0 (Qt, PySide6, libheif, libde265)",
                            (ROOT / "packaging" / "licenses" / "LGPL-3.0.txt").read_text(encoding="utf-8")))  # fmt: skip
    dest.write_text("\n".join(lines) + "".join(texts), encoding="utf-8")


def size_report(folder: Path) -> None:
    files = [p for p in folder.rglob("*") if p.is_file()]
    total = sum(p.stat().st_size for p in files)
    print(f"\n{folder}: {len(files)} files, {total / 1024 / 1024:.1f} MB")
    for p in sorted(files, key=lambda p: p.stat().st_size, reverse=True)[:12]:
        print(f"  {p.stat().st_size / 1024 / 1024:6.1f} MB  {p.relative_to(folder)}")


def release_old_dist() -> None:
    """Move the previous build out of the way first. If MorphTo from dist/ is running, Windows refuses the
    rename and we stop here, instead of half-deleting the folder under the running app."""
    if not DIST.exists():
        return
    for leftover in DIST.parent.glob(f"{DIST.name}.old*"):  # earlier builds that could not be deleted
        shutil.rmtree(leftover, ignore_errors=True)
    old = DIST.with_name(f"{DIST.name}.old-{os.getpid()}")
    try:
        DIST.rename(old)
    except PermissionError:
        sys.exit(f"{DIST} is in use: quit MorphTo (tray icon → Quit) and run the build again.")
    shutil.rmtree(old, ignore_errors=True)


def main() -> None:
    release_old_dist()
    icon = make_icon()
    dist = pyinstaller(icon, make_version_file())
    prune(dist)
    DIST.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(dist), DIST)
    build_stub(DIST)
    shutil.copy(ROOT / "LICENSE", DIST / "LICENSE.txt")
    make_notices(DIST / "THIRD-PARTY-NOTICES.txt")
    size_report(DIST)


if __name__ == "__main__":
    main()
