"""Build the app with PyInstaller for the system it runs on.

- Windows: dist/MorphTo/ with MorphTo.exe (windowed) and morphto.com (console); the installer is made by
  packaging/installer.iss.
- Linux:   dist/MorphTo/ and, when appimagetool is available ($APPIMAGETOOL or on PATH),
  dist/MorphTo-<version>-x86_64.AppImage.
- macOS:   dist/MorphTo.app (a menu-bar app) and dist/MorphTo-<version>-macos-<arch>.dmg.

Usage:  python packaging/build.py            (needs `pip install pyinstaller`)

PyInstaller is used rather than Nuitka: with Python 3.14 + PySide6 6.12, the Nuitka-compiled GUI
corrupted memory (random access violations), while the same code is stable as regular bytecode.
Size is kept down by excluding what we never use and pruning Qt files after the build.
"""

from __future__ import annotations

import os
import platform
import plistlib
import shutil
import subprocess
import sys
from fnmatch import fnmatch
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
PRUNE_WINDOWS = [
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
# Linux and macOS keep Qt under PySide6/Qt/.
PRUNE_UNIX = [
    "PySide6/Qt/translations",
    "PySide6/Qt/plugins/imageformats",
    "PySide6/Qt/plugins/iconengines",
    "PySide6/Qt/plugins/generic",
    "PySide6/Qt/plugins/platforms/libqminimal.so",
    "PySide6/Qt/plugins/platforms/libqvnc.so",
    "PySide6/Qt/plugins/platforms/libqlinuxfb.so",
    "PySide6/Qt/plugins/platforms/libqvkkhrdisplay.so",
    "PySide6/Qt/plugins/platforms/libqminimal.dylib",
    "PySide6/Qt/plugins/platforms/libqoffscreen.dylib",
]
PRUNE = PRUNE_WINDOWS if sys.platform == "win32" else PRUNE_UNIX


def make_icon() -> Path:
    """MorphTo.ico on Windows, MorphTo.icns on macOS, MorphTo.png (256 px) on Linux."""
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
    BUILD.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        out = BUILD / "MorphTo.ico"
        images[0].save(out, format="ICO", sizes=[im.size for im in images], append_images=images[1:])
    elif sys.platform == "darwin":
        out = BUILD / "MorphTo.icns"
        big = render_png(1024)
        big.save(out, format="ICNS")
    else:
        out = BUILD / "MorphTo.png"
        images[0].save(out, format="PNG")
    return out


def render_png(size: int):
    from PIL import Image
    from PySide6.QtCore import QBuffer, QIODevice

    from morphto.gui.icon import render

    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    render(size).save(buf, "PNG")
    return Image.open(BytesIO(bytes(buf.data()))).convert("RGBA")


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


def pyinstaller(icon: Path, version: Path | None) -> Path:
    work = BUILD / "pyinstaller"
    extra = ["--version-file", str(version)] if version else []
    if sys.platform == "darwin":
        extra += ["--osx-bundle-identifier", "com.rearzty.morphto"]
    cmd = [
        sys.executable, "-m", "PyInstaller", str(ROOT / "packaging" / "entry.py"),
        "--name", APP_NAME, "--onedir", "--windowed", "--noconfirm", "--clean", "--log-level", "WARN",
        "--icon", str(icon), *extra,
        "--distpath", str(work / "dist"), "--workpath", str(work / "work"), "--specpath", str(work),
        "--paths", str(ROOT / "src"), "--collect-data", "morphto",
        # used by the JPEG XL plugin, which is downloaded later and imports it
        "--hidden-import", "packaging.version",
        *[arg for module in EXCLUDE_MODULES for arg in ("--exclude-module", module)],
    ]  # fmt: skip
    subprocess.run(cmd, check=True, cwd=ROOT)
    if sys.platform == "darwin":
        return work / "dist" / f"{APP_NAME}.app"
    return work / "dist" / APP_NAME


def finish_macos_bundle(app: Path) -> None:
    """A menu-bar app (no Dock icon), with our version and a usage text for automation prompts."""
    info_path = app / "Contents" / "Info.plist"
    with info_path.open("rb") as f:
        info = plistlib.load(f)
    info.update(
        LSUIElement=True,
        CFBundleName=APP_NAME,
        CFBundleDisplayName=APP_NAME,
        CFBundleShortVersionString=__version__,
        CFBundleVersion=__version__,
        NSHighResolutionCapable=True,
        LSMinimumSystemVersion="11.0",
        NSHumanReadableCopyright="Copyright (C) 2026 rearzty. GPL-3.0-or-later.",
    )
    with info_path.open("wb") as f:
        plistlib.dump(info, f)


def make_dmg(app: Path) -> Path:
    """A disk image with the app and a link to /Applications, to drag it there."""
    arch = "arm64" if platform.machine() == "arm64" else "x86_64"
    out = ROOT / "dist" / f"{APP_NAME}-{__version__}-macos-{arch}.dmg"
    stage = BUILD / "dmg"
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir(parents=True)
    subprocess.run(["ditto", str(app), str(stage / app.name)], check=True)
    (stage / "Applications").symlink_to("/Applications")
    out.unlink(missing_ok=True)
    subprocess.run(
        [
            "hdiutil",
            "create",
            "-volname",
            APP_NAME,
            "-srcfolder",
            str(stage),
            "-ov",
            "-format",
            "UDZO",
            str(out),
        ],
        check=True,
    )
    return out


def make_appimage(dist: Path, icon: Path) -> Path | None:
    """dist/MorphTo -> MorphTo-<version>-x86_64.AppImage (needs appimagetool)."""
    tool = os.environ.get("APPIMAGETOOL") or shutil.which("appimagetool")
    if not tool:
        print("! appimagetool not found, skipping the AppImage")
        return None
    appdir = BUILD / "MorphTo.AppDir"
    shutil.rmtree(appdir, ignore_errors=True)
    shutil.copytree(dist, appdir / "usr" / "lib" / "morphto", symlinks=True)
    (appdir / "AppRun").write_text(
        '#!/bin/sh\nHERE="$(dirname "$(readlink -f "$0")")"\nexec "$HERE/usr/lib/morphto/MorphTo" "$@"\n',
        encoding="utf-8",
    )
    (appdir / "AppRun").chmod(0o755)
    (appdir / "morphto.desktop").write_text(
        "[Desktop Entry]\nType=Application\nName=MorphTo\nComment=Fast offline file converter\n"
        "Exec=MorphTo %F\nIcon=morphto\nCategories=Utility;Graphics;AudioVideo;\nTerminal=false\n"
        "StartupWMClass=MorphTo\n",
        encoding="utf-8",
    )
    shutil.copy(icon, appdir / "morphto.png")
    arch = platform.machine() or "x86_64"
    out = ROOT / "dist" / f"{APP_NAME}-{__version__}-{arch}.AppImage"
    env = {**os.environ, "ARCH": arch}
    subprocess.run([tool, "--no-appstream", str(appdir), str(out)], check=True, env=env)
    return out


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
    ("pypdfium2", "BSD-3-Clause or Apache-2.0; PDFium is BSD-3-Clause, with the licenses of its libraries",
     ["BSD-3-Clause.txt", "*/BUILD_LICENSES/*"]),
    ("markdown-it-py", "MIT", ["LICENSE", "LICENSE.markdown-it"]),
    ("mdurl", "MIT", ["LICENSE"]),
    ("PyYAML", "MIT", ["LICENSE"]),
    ("packaging", "Apache-2.0 or BSD-2-Clause", ["LICENSE.BSD"]),
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
            # A plain name: the first file with that name. A pattern: every matching file.
            for f in dist.files or []:
                rel = str(f).replace("\\", "/")
                if f.name == wanted or ("/" in wanted and fnmatch(rel, wanted)):
                    title = f"{dist.metadata['Name']} {dist.version}: {f.name}"
                    texts.append(section(title, f.locate().read_text(encoding="utf-8", errors="replace")))
                    if "/" not in wanted:
                        break
    lines += [
        "",
        "Optional components downloaded on first use (not part of this package):",
        "  - FFmpeg (www.gyan.dev builds) — GPL-3.0",
        "  - pillow-heif, the HEIC encoder (from PyPI) — BSD-3-Clause; libheif/libde265 LGPL-3.0, x265 GPL-2.0",
        "  - LibreOffice (www.libreoffice.org), for office documents — MPL-2.0, unless already installed",
        "  - resvg (github.com/linebender/resvg), the SVG renderer — Apache-2.0 or MIT",
        "  - LibRaw (www.libraw.org), the camera RAW decoder — LGPL-2.1 or CDDL-1.0",
        "  - pillow-jxl-plugin (from PyPI), the JPEG XL codec — GPL-3.0-or-later",
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
    dist = pyinstaller(icon, make_version_file() if sys.platform == "win32" else None)
    if sys.platform == "darwin":
        internal = dist / "Contents" / "Frameworks"  # PyInstaller's layout inside a .app
        for rel in PRUNE:
            target = internal / rel
            if target.is_symlink():  # inside a bundle, data folders link into Contents/Resources
                real = target.resolve()
                target.unlink()
                if real.is_dir():
                    shutil.rmtree(real)
            elif target.is_dir():
                shutil.rmtree(target)
            elif target.exists():
                target.unlink()
        finish_macos_bundle(dist)
        # Editing the bundle broke PyInstaller's ad-hoc signature; Apple Silicon refuses to run unsigned code.
        subprocess.run(["codesign", "--force", "--deep", "--sign", "-", str(dist)], check=True)
        target = DIST.with_name(f"{APP_NAME}.app")
        shutil.rmtree(target, ignore_errors=True)
        shutil.move(str(dist), target)
        resources = target / "Contents" / "Resources"
        shutil.copy(ROOT / "LICENSE", resources / "LICENSE.txt")
        make_notices(resources / "THIRD-PARTY-NOTICES.txt")
        size_report(target)
        print(make_dmg(target))
        return
    prune(dist)
    DIST.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(dist), DIST)
    if sys.platform == "win32":
        build_stub(DIST)
    shutil.copy(ROOT / "LICENSE", DIST / "LICENSE.txt")
    make_notices(DIST / "THIRD-PARTY-NOTICES.txt")
    size_report(DIST)
    if sys.platform.startswith("linux"):
        print(make_appimage(DIST, icon))


if __name__ == "__main__":
    main()
