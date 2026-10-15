"""Optional engines, downloaded only when first needed: programs such as FFmpeg and LibreOffice, and
Python packages such as the HEIC encoder (pillow-heif)."""

from __future__ import annotations

import contextlib
import hashlib
import importlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from morphto import __version__
from morphto.paths import engines_dir

ProgressFn = Callable[[float], None]

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
if sys.platform == "win32":
    _SYSTEM_TAR = Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32" / "tar.exe"
else:  # bsdtar (libarchive) unpacks .7z: /usr/bin/tar on a Mac, the libarchive-tools package on Linux
    _SYSTEM_TAR = Path(
        "/usr/bin/tar" if sys.platform == "darwin" else shutil.which("bsdtar") or "/nonexistent"
    )


class EngineInstallError(Exception):
    pass


@dataclass(frozen=True)
class Archive:
    url: str
    sha256_url: str  # where the publisher lists the checksum ("" when it is pinned in ``sha256``)
    size_mb: int
    kind: str  # "7z" (needs the tar.exe that ships with Windows 11) or "zip"
    sha256: str = ""  # pinned checksum, for downloads whose publisher lists none
    folder: str = "/bin/"  # where the programs sit inside the archive ("" = anywhere)


@dataclass
class Engine:
    id: str
    name: str
    archives: tuple[Archive, ...]  # tried in order; the first is the smallest download
    tools: tuple[str, ...]  # executables to extract from the archive (without .exe)
    env_var: str
    extra: tuple[str, ...] = ()  # other files the programs need (DLLs), taken from the same folder
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def install_dir(self) -> Path:
        return engines_dir() / self.id

    @property
    def size_mb(self) -> int:
        """Download size shown to the user before installing."""
        return next((a.size_mb for a in self.archives if a.kind != "7z" or _SYSTEM_TAR.is_file()), 0)

    def tool(self, name: str) -> Path | None:
        """Path to one of the engine's executables, searching managed, env override, then PATH."""
        exe = f"{name}.exe" if os.name == "nt" else name
        managed = self.install_dir / exe
        if managed.is_file():
            return managed
        override = os.environ.get(self.env_var)
        if override:
            candidate = Path(override).parent / exe
            if candidate.is_file():
                return candidate
        found = shutil.which(name)
        return Path(found) if found else None

    def available(self) -> bool:
        return all(self.tool(t) is not None for t in self.tools)

    def source(self) -> str | None:
        """'managed' (downloaded by MorphTo), 'system' (found elsewhere) or None."""
        if not self.available():
            return None
        return "managed" if all((self.install_dir / f"{t}.exe").is_file() for t in self.tools) else "system"

    def prune(self) -> None:
        """Delete files left over by older versions (e.g. ffprobe.exe, ~100 MB, no longer needed)."""
        if not self.install_dir.is_dir():
            return
        keep = {f"{t}.exe" for t in self.tools} | set(self.extra)
        for item in self.install_dir.iterdir():
            if item.name not in keep:
                with contextlib.suppress(OSError):
                    shutil.rmtree(item) if item.is_dir() else item.unlink()

    def install(self, progress: ProgressFn | None = None, cancel: threading.Event | None = None) -> None:
        progress = progress or (lambda _f: None)
        cancel = cancel or threading.Event()
        with self._lock:
            target = self.install_dir
            target.parent.mkdir(parents=True, exist_ok=True)
            errors: list[str] = []
            for archive in self.archives:
                if archive.kind == "7z" and not _SYSTEM_TAR.is_file():
                    continue
                with tempfile.TemporaryDirectory(prefix=f"morphto-{self.id}-", dir=target.parent) as tmp:
                    try:
                        staging = self._fetch(archive, Path(tmp), progress, cancel)
                    except EngineInstallError as exc:
                        if cancel.is_set():
                            raise
                        errors.append(str(exc))
                        continue
                    if target.exists():
                        shutil.rmtree(target)
                    staging.rename(target)
                progress(1.0)
                return
            if not self.archives:
                raise EngineInstallError(_system_package_hint(self.name, self.tools[0]))
            raise EngineInstallError("; ".join(errors) or f"{self.name}: no suitable download")

    def _fetch(self, archive: Archive, tmp: Path, progress: ProgressFn, cancel: threading.Event) -> Path:
        file = tmp / f"download.{archive.kind}"
        expected = archive.sha256 or _fetch_text(archive.sha256_url).split()[0].lower()
        digest = _download(archive.url, file, progress, cancel)
        if digest != expected:
            raise EngineInstallError(f"{self.name}: checksum mismatch, download is corrupted")
        staging = tmp / "out"
        staging.mkdir()
        if archive.kind == "7z":
            _extract_7z(file, staging, self.tools)
        else:
            _extract_zip(file, staging, self.tools, self.extra, archive.folder)
        return staging

    def remove(self) -> None:
        with self._lock:
            if self.install_dir.exists():
                shutil.rmtree(self.install_dir)


# Downloads go through the curl.exe that ships with Windows 10 1803+ / 11. That way the app does not
# need to bundle OpenSSL (~6 MB) just to fetch FFmpeg once. urllib is the fallback (dev setups).
if sys.platform == "win32":
    _SYSTEM_CURL = _SYSTEM_TAR.with_name("curl.exe")
else:
    _SYSTEM_CURL = Path(shutil.which("curl") or "/usr/bin/curl")
_USER_AGENT = f"MorphTo/{__version__}"


def _curl(*args: str) -> list[str]:
    return [str(_SYSTEM_CURL), "--silent", "--show-error", "--fail", "--location", "-A", _USER_AGENT, *args]


def _fetch_text(url: str) -> str:
    if _SYSTEM_CURL.is_file():
        result = subprocess.run(
            _curl("--max-time", "30", url), capture_output=True, creationflags=_NO_WINDOW, check=False
        )
        if result.returncode != 0:
            raise EngineInstallError(
                f"Cannot reach download server: {result.stderr.decode(errors='replace')}"
            )
        return result.stdout.decode("ascii", "replace")
    import urllib.request

    try:
        request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(request, timeout=30) as resp:
            return resp.read().decode("ascii", "replace")
    except OSError as exc:
        raise EngineInstallError(f"Cannot reach download server: {exc}") from exc


def _content_length(url: str) -> int:
    result = subprocess.run(
        _curl("--head", "--max-time", "30", url), capture_output=True, creationflags=_NO_WINDOW, check=False
    )
    lengths = [
        line.split(":", 1)[1].strip()
        for line in result.stdout.decode("latin-1").splitlines()
        if line.lower().startswith("content-length:")
    ]
    return int(lengths[-1]) if lengths and lengths[-1].isdigit() else 0


def _download(url: str, dest: Path, progress: ProgressFn, cancel: threading.Event) -> str:
    if not _SYSTEM_CURL.is_file():
        return _download_urllib(url, dest, progress, cancel)
    total = _content_length(url)
    proc = subprocess.Popen(
        _curl("--output", str(dest), url), stderr=subprocess.PIPE, creationflags=_NO_WINDOW
    )
    try:
        while proc.poll() is None:
            if cancel.wait(0.2):
                proc.kill()
                proc.wait()
                raise EngineInstallError("Cancelled")
            if total and dest.exists():
                progress(min(0.97, dest.stat().st_size / total))
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    if proc.returncode != 0:
        error = proc.stderr.read().decode(errors="replace").strip() if proc.stderr else ""
        raise EngineInstallError(f"Download failed: {error or f'curl exit code {proc.returncode}'}")
    sha = hashlib.sha256()
    with dest.open("rb") as f:
        while chunk := f.read(1 << 20):
            sha.update(chunk)
    return sha.hexdigest()


def _download_urllib(url: str, dest: Path, progress: ProgressFn, cancel: threading.Event) -> str:
    import urllib.request

    sha = hashlib.sha256()
    try:
        request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(request, timeout=30) as resp, dest.open("wb") as out:
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            while chunk := resp.read(1 << 20):
                if cancel.is_set():
                    raise EngineInstallError("Cancelled")
                out.write(chunk)
                sha.update(chunk)
                done += len(chunk)
                if total:
                    progress(min(0.97, done / total))
    except OSError as exc:
        raise EngineInstallError(f"Download failed: {exc}") from exc
    return sha.hexdigest()


def _extract_7z(archive: Path, dest: Path, tools: tuple[str, ...]) -> None:
    """Use the bsdtar shipped with Windows 11 (libarchive with LZMA) to unpack a .7z."""
    unpacked = dest / "_unpacked"
    unpacked.mkdir()
    cmd = [str(_SYSTEM_TAR), "-xf", str(archive), "-C", str(unpacked)]
    for tool in tools:
        cmd += ["--include", f"*/bin/{tool}.exe"]
    result = subprocess.run(cmd, capture_output=True, creationflags=_NO_WINDOW, check=False)
    found = {p.name: p for p in unpacked.rglob("*.exe")}
    missing = [t for t in tools if f"{t}.exe" not in found]
    if result.returncode != 0 or missing:
        raise EngineInstallError(
            f"Cannot unpack {archive.name}: {result.stderr.decode('utf-8', 'replace')[:200]}"
        )
    for tool in tools:
        found[f"{tool}.exe"].rename(dest / f"{tool}.exe")
    shutil.rmtree(unpacked)


def _extract_zip(
    archive: Path, dest: Path, tools: tuple[str, ...], extra: tuple[str, ...] = (), folder: str = "/bin/"
) -> None:
    wanted = {f"{t}.exe" for t in tools} | set(extra)
    found: set[str] = set()
    with zipfile.ZipFile(archive) as zf:
        for info in zf.infolist():
            name = info.filename.rsplit("/", 1)[-1]
            if name in wanted and folder in f"/{info.filename}":
                with zf.open(info) as src, (dest / name).open("wb") as out:
                    shutil.copyfileobj(src, out, 1 << 20)
                found.add(name)
    missing = wanted - found
    if missing:
        raise EngineInstallError(f"Archive does not contain {', '.join(sorted(missing))}")


def _windows_only(*archives: Archive) -> tuple[Archive, ...]:
    """These downloads are Windows builds. Elsewhere the program comes from the system (Homebrew, apt...)."""
    return archives if sys.platform == "win32" else ()


_GYAN = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials"

FFMPEG = Engine(
    id="ffmpeg",
    name="FFmpeg",
    # Release "essentials" build by Gyan Doshi (GPL, includes x264/x265/vpx/opus/lame), linked from ffmpeg.org.
    archives=_windows_only(
        Archive(f"{_GYAN}.7z", f"{_GYAN}.7z.sha256", 34, "7z"),
        Archive(f"{_GYAN}.zip", f"{_GYAN}.zip.sha256", 110, "zip"),
    ),
    tools=("ffmpeg",),
    env_var="MORPHTO_FFMPEG",
)


# SVG rendering: resvg by the Linebender project (Apache-2.0 / MIT). The last release with a Windows build.
RESVG = Engine(
    id="resvg",
    name="SVG renderer",
    archives=_windows_only(
        Archive(
            "https://github.com/linebender/resvg/releases/download/v0.47.0/resvg-win64.zip",
            "",
            2,
            "zip",
            sha256="5684e59ceaa53ce720b49efb441b0918ae99d04e8ce3f6f753664524592d67f1",
            folder="",
        ),
    ),
    tools=("resvg",),
    env_var="MORPHTO_RESVG",
)

# Camera RAW: LibRaw's own Windows build (LGPL-2.1 / CDDL-1.0); dcraw_emu.exe is its command-line decoder.
LIBRAW = Engine(
    id="libraw",
    name="RAW decoder",
    archives=_windows_only(
        Archive(
            "https://www.libraw.org/data/LibRaw-0.22.2-Win64.zip",
            "",
            4,
            "zip",
            sha256="ac64fa12bb00a7581332d4c6ab918c0533fb3f119d6b668d47a6875410dca948",
        ),
    ),
    tools=("dcraw_emu",),
    extra=("libraw.dll",),
    env_var="MORPHTO_LIBRAW",
)


@dataclass
class WheelEngine:
    """A Python package (binary wheel from PyPI) unpacked into its own folder and imported on demand.

    Pinned to one version; the wheel matching the running Python is picked from PyPI's JSON API, which
    also provides the sha256 we verify against.
    """

    id: str
    name: str
    package: str  # PyPI project name
    module: str  # import name
    version: str
    size_mb: int  # approximate download, shown before installing
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def install_dir(self) -> Path:
        return engines_dir() / self.id

    def tool(self, name: str) -> Path | None:
        return None

    def _managed(self) -> bool:
        return (self.install_dir / self.module / "__init__.py").is_file()

    def available(self) -> bool:
        if self._managed():
            return True
        return importlib.util.find_spec(self.module) is not None

    def source(self) -> str | None:
        if self._managed():
            return "managed"
        return "system" if importlib.util.find_spec(self.module) is not None else None

    def load(self):
        """Import the package (from our folder if it was downloaded) and return the module."""
        if self._managed() and str(self.install_dir) not in sys.path:
            sys.path.insert(0, str(self.install_dir))
            with contextlib.suppress(OSError):
                self._dll_dir = os.add_dll_directory(str(self.install_dir))  # bundled DLLs live here
            importlib.invalidate_caches()
        return importlib.import_module(self.module)

    def prune(self) -> None:
        pass

    def wheel_tag(self) -> str:
        return f"cp{sys.version_info.major}{sys.version_info.minor}-cp{sys.version_info.major}{sys.version_info.minor}-win_amd64"

    def pick_wheel(self, release: dict) -> tuple[str, str, int]:
        """(url, sha256, size) of the best wheel for this Python and system from a PyPI release JSON."""
        wheels = [f for f in release.get("urls", []) if f.get("packagetype") == "bdist_wheel"]
        try:
            from packaging.tags import sys_tags
            from packaging.utils import InvalidWheelFilename, parse_wheel_filename
        except ImportError:
            sys_tags = None
        if sys_tags is not None:
            # sys_tags() lists what this interpreter accepts, most specific first (manylinux, macOS...)
            rank = {str(t): i for i, t in enumerate(sys_tags())}
            best: tuple[int, dict] | None = None
            for f in wheels:
                try:
                    tags = parse_wheel_filename(f["filename"])[3]
                except InvalidWheelFilename:
                    continue
                score = min((rank[str(t)] for t in tags if str(t) in rank), default=None)
                if score is not None and (best is None or score < best[0]):
                    best = (score, f)
            if best is not None:
                f = best[1]
                return f["url"], f["digests"]["sha256"], int(f.get("size") or 0)
        tag = self.wheel_tag()
        for f in wheels:
            if f["filename"].endswith(f"{tag}.whl"):
                return f["url"], f["digests"]["sha256"], int(f.get("size") or 0)
        raise EngineInstallError(f"{self.name}: no download for this system ({tag})")

    def install(self, progress: ProgressFn | None = None, cancel: threading.Event | None = None) -> None:
        progress = progress or (lambda _f: None)
        cancel = cancel or threading.Event()
        with self._lock:
            release = json.loads(_fetch_text(f"https://pypi.org/pypi/{self.package}/{self.version}/json"))
            url, sha, _size = self.pick_wheel(release)
            target = self.install_dir
            target.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix=f"morphto-{self.id}-", dir=target.parent) as tmp:
                wheel = Path(tmp) / "package.whl"
                if _download(url, wheel, progress, cancel) != sha:
                    raise EngineInstallError(f"{self.name}: checksum mismatch, download is corrupted")
                staging = Path(tmp) / "out"
                with zipfile.ZipFile(wheel) as zf:
                    zf.extractall(staging)
                _install_wheel_data(staging)
                if target.exists():
                    shutil.rmtree(target)
                staging.rename(target)
        progress(1.0)

    def remove(self) -> None:
        with self._lock:
            if self.install_dir.exists():
                shutil.rmtree(self.install_dir)


def _system_package_hint(name: str, package: str) -> str:
    if sys.platform == "darwin":
        return f"{name} is not installed. Install it with Homebrew: brew install {package}"
    return f"{name} is not installed. Install it with your package manager, e.g.: sudo apt install {package}"


def _install_wheel_data(root: Path) -> None:
    """Do what pip does with ``<name>.data/platlib|purelib``: move their files next to the package."""
    for data in root.glob("*.data"):
        for scheme in ("platlib", "purelib"):
            folder = data / scheme
            if folder.is_dir():
                for item in folder.iterdir():
                    item.rename(root / item.name)
        shutil.rmtree(data)


HEIF_ENCODER = WheelEngine(
    id="heif",
    name="HEIC encoder",
    package="pillow-heif",
    module="pillow_heif",
    version="1.8.1",
    size_mb=7,
)


_LO_STABLE = "https://download.documentfoundation.org/libreoffice/stable/"

# Parts of LibreOffice that converting files never touches: spell-check dictionaries, translations of the
# UI, help, galleries, templates, icon themes, the bundled Python. Removing them cuts ~1.5 GB to ~0.5 GB.
_LO_PRUNE = (
    "help", "readmes", "System", "System64", "CREDITS.fodt",
    "program/resource", "program/wizards", "program/shlxthdl", "program/python-core-*",
    "share/extensions", "share/gallery", "share/template", "share/autotext", "share/wizards", "share/basic",
    "share/registry/res", "share/config/images_*",
)  # fmt: skip


@dataclass
class OfficeEngine:
    """LibreOffice, used headless for Word / Excel / PowerPoint / OpenDocument files.

    An installed LibreOffice is used as is. Otherwise the newest stable MSI is downloaded from
    documentfoundation.org, checked against its published sha256, unpacked with an *administrative*
    install (``msiexec /a``: just extracts files, no admin rights, nothing registered in Windows) and pruned.
    """

    id: str = "libreoffice"
    name: str = "LibreOffice"
    size_mb: int = 360
    env_var: str = "MORPHTO_LIBREOFFICE"
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def install_dir(self) -> Path:
        return engines_dir() / self.id

    @property
    def profile_dir(self) -> Path:
        """LibreOffice's user profile for our headless runs, kept apart from the user's own."""
        return engines_dir() / f"{self.id}-profile"

    def _system_candidates(self) -> list[Path]:
        candidates = []
        override = os.environ.get(self.env_var)
        if override:
            candidates.append(Path(override))
        if sys.platform == "win32":
            for env in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
                base = os.environ.get(env)
                if base:
                    candidates.append(Path(base) / "LibreOffice" / "program" / "soffice.com")
            found = shutil.which("soffice")
            if found:
                candidates.append(Path(found).with_suffix(".com"))
            return candidates
        candidates += [
            Path("/Applications/LibreOffice.app/Contents/MacOS/soffice"),
            Path.home() / "Applications/LibreOffice.app/Contents/MacOS/soffice",
        ]
        candidates += [Path(p) for p in (shutil.which("soffice"), shutil.which("libreoffice")) if p]
        candidates += [Path("/usr/lib/libreoffice/program/soffice"), Path("/opt/libreoffice/program/soffice")]
        return candidates

    def tool(self, name: str = "soffice") -> Path | None:
        managed = self.install_dir / "program" / "soffice.com"
        if managed.is_file():
            return managed
        return next((p for p in self._system_candidates() if p.is_file()), None)

    def available(self) -> bool:
        return self.tool() is not None

    def source(self) -> str | None:
        if (self.install_dir / "program" / "soffice.com").is_file():
            return "managed"
        return "system" if self.available() else None

    def prune(self) -> None:
        pass

    def latest_msi(self) -> str:
        listing = _fetch_text(_LO_STABLE)
        versions = {tuple(int(x) for x in m) for m in re.findall(r'href="(\d+)\.(\d+)\.(\d+)/"', listing)}
        if not versions:
            raise EngineInstallError(f"{self.name}: cannot find a stable version at {_LO_STABLE}")
        v = ".".join(map(str, max(versions)))
        return f"{_LO_STABLE}{v}/win/x86_64/LibreOffice_{v}_Win_x86-64.msi"

    def install(self, progress: ProgressFn | None = None, cancel: threading.Event | None = None) -> None:
        progress = progress or (lambda _f: None)
        cancel = cancel or threading.Event()
        if sys.platform != "win32":
            raise EngineInstallError(_system_package_hint(self.name, "libreoffice"))
        with self._lock:
            url = self.latest_msi()
            expected = _fetch_text(f"{url}.sha256").split()[0].lower()
            target = self.install_dir
            target.parent.mkdir(parents=True, exist_ok=True)
            # Not tempfile: since Python 3.13 its folders are private to the user, and msiexec unpacks
            # through the Windows Installer service, which then fails with error 1603.
            # Short names: LibreOffice's deepest paths are ~110 characters, and MAX_PATH still applies.
            tmp = target.parent / f"lo-{os.getpid()}"
            shutil.rmtree(tmp, ignore_errors=True)
            tmp.mkdir()
            try:
                msi = tmp / "libreoffice.msi"
                digest = _download(url, msi, lambda f: progress(f * 0.8), cancel)
                if digest != expected:
                    raise EngineInstallError(f"{self.name}: checksum mismatch, download is corrupted")
                staging = tmp / "x"
                self._extract(msi, staging, progress, cancel)
                _prune_tree(staging, _LO_PRUNE)
                if not (staging / "program" / "soffice.com").is_file():
                    raise EngineInstallError(f"{self.name}: the package has no program/soffice.com")
                if target.exists():
                    shutil.rmtree(target)
                staging.rename(target)
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
        progress(1.0)

    @staticmethod
    def _extract(msi: Path, dest: Path, progress: ProgressFn, cancel: threading.Event) -> None:
        log = msi.with_suffix(".log")
        cmd = ["msiexec.exe", "/a", str(msi), "/qn", f"TARGETDIR={dest}", "/L*", str(log)]
        proc = subprocess.Popen(cmd, creationflags=_NO_WINDOW)
        fraction = 0.8
        try:
            while proc.poll() is None:
                if cancel.wait(0.5):
                    proc.kill()
                    proc.wait()
                    raise EngineInstallError("Cancelled")
                fraction = min(0.98, fraction + 0.002)  # msiexec reports nothing; ~1 min to unpack
                progress(fraction)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
        if proc.returncode != 0:
            raise EngineInstallError(
                f"Cannot unpack {msi.name}: msiexec exit code {proc.returncode}. {_msi_error(log)}"
            )
        (dest / msi.name).unlink(missing_ok=True)  # the administrative image keeps a copy of the MSI

    def remove(self) -> None:
        with self._lock:
            for folder in (self.install_dir, self.profile_dir):
                if folder.exists():
                    shutil.rmtree(folder)


def _msi_error(log: Path) -> str:
    """The first error message from an msiexec log (UTF-16), if any."""
    try:
        text = log.read_bytes().decode("utf-16", "replace")
    except OSError:
        return ""
    found = re.search(r"^\S+ 1\d{3}\..*$", text, re.MULTILINE)  # "Error 1304. ..." in the system language
    return found.group(0).strip() if found else ""


def _prune_tree(root: Path, patterns: tuple[str, ...]) -> None:
    for pattern in patterns:
        for item in root.glob(pattern):
            with contextlib.suppress(OSError):
                shutil.rmtree(item) if item.is_dir() else item.unlink()


LIBREOFFICE = OfficeEngine()

JXL_CODEC = WheelEngine(
    id="jxl",
    name="JPEG XL codec",
    package="pillow-jxl-plugin",
    module="pillow_jxl",
    version="1.3.8",
    size_mb=5,
)

_ENGINES = {e.id: e for e in (FFMPEG, HEIF_ENCODER, JXL_CODEC, RESVG, LIBRAW, LIBREOFFICE)}


def get(engine_id: str) -> Engine | WheelEngine | OfficeEngine:
    return _ENGINES[engine_id]


def all_engines() -> list[Engine | WheelEngine | OfficeEngine]:
    return list(_ENGINES.values())
