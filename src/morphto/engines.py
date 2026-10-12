"""Optional engines, downloaded only when first needed: programs such as FFmpeg, and Python packages
such as the HEIC encoder (pillow-heif)."""

from __future__ import annotations

import contextlib
import hashlib
import importlib
import importlib.util
import json
import os
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
_SYSTEM_TAR = Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32" / "tar.exe"


class EngineInstallError(Exception):
    pass


@dataclass(frozen=True)
class Archive:
    url: str
    sha256_url: str
    size_mb: int
    kind: str  # "7z" (needs the tar.exe that ships with Windows 11) or "zip"


@dataclass
class Engine:
    id: str
    name: str
    archives: tuple[Archive, ...]  # tried in order; the first is the smallest download
    tools: tuple[str, ...]  # executables to extract from the archive (without .exe)
    env_var: str
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
        keep = {f"{t}.exe" for t in self.tools}
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
            raise EngineInstallError("; ".join(errors) or f"{self.name}: no suitable download")

    def _fetch(self, archive: Archive, tmp: Path, progress: ProgressFn, cancel: threading.Event) -> Path:
        file = tmp / f"download.{archive.kind}"
        expected = _fetch_text(archive.sha256_url).split()[0].lower()
        digest = _download(archive.url, file, progress, cancel)
        if digest != expected:
            raise EngineInstallError(f"{self.name}: checksum mismatch, download is corrupted")
        staging = tmp / "out"
        staging.mkdir()
        if archive.kind == "7z":
            _extract_7z(file, staging, self.tools)
        else:
            _extract_zip(file, staging, self.tools)
        return staging

    def remove(self) -> None:
        with self._lock:
            if self.install_dir.exists():
                shutil.rmtree(self.install_dir)


# Downloads go through the curl.exe that ships with Windows 10 1803+ / 11. That way the app does not
# need to bundle OpenSSL (~6 MB) just to fetch FFmpeg once. urllib is the fallback (dev setups).
_SYSTEM_CURL = _SYSTEM_TAR.with_name("curl.exe")
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


def _extract_zip(archive: Path, dest: Path, tools: tuple[str, ...]) -> None:
    wanted = {f"{t}.exe" for t in tools}
    found: set[str] = set()
    with zipfile.ZipFile(archive) as zf:
        for info in zf.infolist():
            name = info.filename.rsplit("/", 1)[-1]
            if name in wanted and "/bin/" in f"/{info.filename}":
                with zf.open(info) as src, (dest / name).open("wb") as out:
                    shutil.copyfileobj(src, out, 1 << 20)
                found.add(name)
    missing = wanted - found
    if missing:
        raise EngineInstallError(f"Archive does not contain {', '.join(sorted(missing))}")


_GYAN = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials"

FFMPEG = Engine(
    id="ffmpeg",
    name="FFmpeg",
    # Release "essentials" build by Gyan Doshi (GPL, includes x264/x265/vpx/opus/lame), linked from ffmpeg.org.
    archives=(
        Archive(f"{_GYAN}.7z", f"{_GYAN}.7z.sha256", 34, "7z"),
        Archive(f"{_GYAN}.zip", f"{_GYAN}.zip.sha256", 110, "zip"),
    ),
    tools=("ffmpeg",),
    env_var="MORPHTO_FFMPEG",
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
        """(url, sha256, size) of the wheel for this Python from a PyPI release JSON."""
        tag = self.wheel_tag()
        for f in release.get("urls", []):
            if f.get("packagetype") == "bdist_wheel" and f["filename"].endswith(f"{tag}.whl"):
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

_ENGINES = {e.id: e for e in (FFMPEG, HEIF_ENCODER)}


def get(engine_id: str) -> Engine | WheelEngine:
    return _ENGINES[engine_id]


def all_engines() -> list[Engine | WheelEngine]:
    return list(_ENGINES.values())
