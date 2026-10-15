"""Where MorphTo keeps its settings and downloaded components."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from morphto import APP_NAME


def _base(env: str, fallback: Path) -> Path:
    override = os.environ.get("MORPHTO_HOME")
    if override:
        return Path(override)
    value = os.environ.get(env)
    return (Path(value) if value else fallback) / APP_NAME


def config_dir() -> Path:
    r"""Settings: %APPDATA%\MorphTo, ~/Library/Application Support/MorphTo, ~/.config/MorphTo."""
    if sys.platform == "win32":
        return _base("APPDATA", Path.home() / "AppData" / "Roaming")
    if sys.platform == "darwin":
        return _base("", Path.home() / "Library" / "Application Support")
    return _base("XDG_CONFIG_HOME", Path.home() / ".config")


def data_dir() -> Path:
    r"""Downloaded engines, logs: %LOCALAPPDATA%\MorphTo, ~/Library/Application Support/MorphTo,
    ~/.local/share/MorphTo."""
    if sys.platform == "win32":
        return _base("LOCALAPPDATA", Path.home() / "AppData" / "Local")
    if sys.platform == "darwin":
        return _base("", Path.home() / "Library" / "Application Support")
    return _base("XDG_DATA_HOME", Path.home() / ".local" / "share")


def engines_dir() -> Path:
    override = os.environ.get("MORPHTO_ENGINES")
    return Path(override) if override else data_dir() / "engines"


def default_output_dir() -> Path:
    return Path.home() / "Downloads" / APP_NAME
