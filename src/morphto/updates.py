"""New versions: ask GitHub for the latest release, download its installer and run it.

The check is one small request to the GitHub API (at start and once a day; it can be turned off in
Settings). Nothing about the user or their files is sent. The installer is verified against the release's
SHA256SUMS.txt before it runs.
"""

from __future__ import annotations

import json
import os
import platform
import re
import subprocess
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from morphto import __version__
from morphto.paths import data_dir

REPO = "rearzty/MorphTo"
LATEST_API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"


class UpdateError(Exception):
    pass


@dataclass(frozen=True)
class Release:
    version: str  # "0.3.0"
    page_url: str
    setup_url: str | None  # the package for this system: Windows installer or Linux AppImage
    setup_name: str | None
    sums_url: str | None  # SHA256SUMS.txt


def package_name(version: str) -> str:
    """Release asset this system updates from (macOS updates from the download page, by hand)."""
    if sys.platform == "win32":
        return f"MorphTo-{version}-setup.exe"
    if sys.platform == "darwin":
        return f"MorphTo-{version}-macos-{'arm64' if platform.machine() == 'arm64' else 'x86_64'}.dmg"
    return f"MorphTo-{version}-{platform.machine() or 'x86_64'}.AppImage"


def parse_version(text: str) -> tuple[int, ...]:
    """'v0.10.2' -> (0, 10, 2); anything after the numbers (e.g. '-beta') is ignored."""
    match = re.match(r"v?(\d+(?:\.\d+)*)", text.strip())
    return tuple(int(x) for x in match.group(1).split(".")) if match else ()


def is_newer(candidate: str, current: str = __version__) -> bool:
    return parse_version(candidate) > parse_version(current)


def parse_release(data: dict) -> Release:
    version = str(data.get("tag_name", "")).lstrip("v")
    assets = {a.get("name"): a.get("browser_download_url") for a in data.get("assets", [])}
    setup_name = package_name(version)
    return Release(
        version=version,
        page_url=data.get("html_url") or RELEASES_PAGE,
        setup_url=assets.get(setup_name),
        setup_name=setup_name if setup_name in assets else None,
        sums_url=assets.get("SHA256SUMS.txt"),
    )


def latest_release() -> Release:
    from morphto.engines import EngineInstallError, _fetch_text

    try:
        data = json.loads(_fetch_text(LATEST_API))
    except (EngineInstallError, ValueError) as exc:
        raise UpdateError(str(exc)) from exc
    if data.get("draft") or data.get("prerelease"):
        raise UpdateError("no stable release")
    return parse_release(data)


def check() -> Release | None:
    """The latest release if it is newer than this app, else ``None``. Raises UpdateError when offline."""
    release = latest_release()
    return release if is_newer(release.version) else None


def installed_app_dir() -> Path | None:
    """Folder of an installer-based install (Inno Setup leaves unins000.exe there); ``None`` for the
    portable zip and for running from source, which can only open the download page."""
    if not getattr(sys, "frozen", False):
        return None
    folder = Path(sys.executable).parent
    return folder if (folder / "unins000.exe").is_file() else None


def appimage_path() -> Path | None:
    """The running AppImage file, if we are one and may replace it."""
    path = os.environ.get("APPIMAGE")
    if not path or not getattr(sys, "frozen", False):
        return None
    file = Path(path)
    return file if file.is_file() and os.access(file.parent, os.W_OK) else None


def can_self_update() -> bool:
    if sys.platform == "win32":
        return installed_app_dir() is not None
    return sys.platform.startswith("linux") and appimage_path() is not None


def download_installer(release: Release, progress: Callable[[float], None], cancel: threading.Event) -> Path:
    from morphto.engines import EngineInstallError, _download, _fetch_text

    if not (release.setup_url and release.setup_name and release.sums_url):
        raise UpdateError("the release has no installer")
    try:
        sums = _fetch_text(release.sums_url)
    except EngineInstallError as exc:
        raise UpdateError(str(exc)) from exc
    expected = next(
        (line.split()[0].lower() for line in sums.splitlines() if line.strip().endswith(release.setup_name)),
        None,
    )
    if not expected:
        raise UpdateError("the installer is not listed in SHA256SUMS.txt")
    folder = data_dir() / "updates"
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.iterdir():  # earlier updates
        if old.is_file():
            old.unlink(missing_ok=True)
    appimage = appimage_path()
    # An AppImage is downloaded next to itself, so the final rename stays on one file system.
    target = (appimage.parent / f".{release.setup_name}.part") if appimage else folder / release.setup_name
    try:
        digest = _download(release.setup_url, target, progress, cancel)
    except EngineInstallError as exc:
        target.unlink(missing_ok=True)
        raise UpdateError(str(exc)) from exc
    if digest != expected:
        target.unlink(missing_ok=True)
        raise UpdateError("checksum mismatch, the download is corrupted")
    return target


def run_installer(installer: Path) -> None:
    """Install the update; the caller quits right after.

    Windows: the installer runs with /SILENT (only a progress window, the user's choices from the first
    install are kept) and /update=1 (it starts MorphTo again when done).
    Linux: the new AppImage replaces the running one (which stays mounted until we exit) and is started.
    """
    appimage = appimage_path()
    if appimage is not None:
        installer.chmod(0o755)
        os.replace(installer, appimage)
        subprocess.Popen([str(appimage), "--background"], start_new_session=True, close_fds=True)
        return
    folder = installed_app_dir()
    args = [str(installer), "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/update=1"]
    if folder is not None:
        args.append(f"/DIR={folder}")
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen(args, creationflags=flags, close_fds=True, cwd=os.path.dirname(installer))
