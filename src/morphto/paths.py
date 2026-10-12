"""Where MorphTo keeps its settings and downloaded components."""

from __future__ import annotations

import os
from pathlib import Path

from morphto import APP_NAME


def _base(env: str, fallback: Path) -> Path:
    override = os.environ.get("MORPHTO_HOME")
    if override:
        return Path(override)
    value = os.environ.get(env)
    return (Path(value) if value else fallback) / APP_NAME


def config_dir() -> Path:
    """Roaming settings: %APPDATA%\\MorphTo."""
    return _base("APPDATA", Path.home() / "AppData" / "Roaming")


def data_dir() -> Path:
    """Machine-local data such as downloaded engines: %LOCALAPPDATA%\\MorphTo."""
    return _base("LOCALAPPDATA", Path.home() / "AppData" / "Local")


def engines_dir() -> Path:
    override = os.environ.get("MORPHTO_ENGINES")
    return Path(override) if override else data_dir() / "engines"


def default_output_dir() -> Path:
    return Path.home() / "Downloads" / APP_NAME
