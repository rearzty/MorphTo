"""Tiny translation layer: one flat JSON file per language in ``locales/``.

To add a language, copy ``locales/en.json``, translate the values (keep ``{placeholders}``) and add it
to ``LANGUAGES`` below.
"""

from __future__ import annotations

import json
import locale
import os
import sys
from functools import cache
from pathlib import Path

LOCALES_DIR = Path(__file__).with_name("locales")

# code -> native name, in the order shown in Settings
LANGUAGES: dict[str, str] = {
    "en": "English",
    "ru": "Русский",
    "uk": "Українська",
    "de": "Deutsch",
    "fr": "Français",
    "es": "Español",
    "pt": "Português",
    "it": "Italiano",
    "pl": "Polski",
    "tr": "Türkçe",
    "zh": "简体中文",
    "ja": "日本語",
    "ko": "한국어",
}

_current = "en"


@cache
def _catalog(code: str) -> dict[str, str]:
    return json.loads((LOCALES_DIR / f"{code}.json").read_text(encoding="utf-8"))


def system_language() -> str:
    code = _windows_language() if sys.platform == "win32" else _unix_language()
    return code if code in LANGUAGES else "en"


def _windows_language() -> str:
    try:
        import ctypes

        lang_id = ctypes.windll.kernel32.GetUserDefaultUILanguage()
        name = locale.windows_locale.get(lang_id, "")
    except (AttributeError, OSError):
        name = locale.getlocale()[0] or ""
    return name.split("_")[0].lower()


def _unix_language() -> str:
    """$LANGUAGE / $LC_ALL / $LC_MESSAGES / $LANG ("ru_RU.UTF-8"); on a Mac the system setting, since
    apps started from the Finder get no $LANG."""
    for var in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(var, "")
        if value and value not in ("C", "POSIX", "C.UTF-8"):
            return value.split(":")[0].split("_")[0].split(".")[0].lower()
    if sys.platform == "darwin":
        import subprocess

        try:
            out = subprocess.run(
                ["defaults", "read", "-g", "AppleLocale"], capture_output=True, text=True, timeout=2
            )
            return out.stdout.strip().split("_")[0].lower()
        except (OSError, subprocess.SubprocessError):
            pass
    return (locale.getlocale()[0] or "").split("_")[0].lower()


def set_language(code: str) -> str:
    """Activate ``code`` ("" = system language); returns the code actually used."""
    global _current
    _current = code if code in LANGUAGES else system_language()
    return _current


def current_language() -> str:
    return _current


def tr(key: str, **kwargs: object) -> str:
    text = _catalog(_current).get(key) or _catalog("en").get(key) or key
    return text.format(**kwargs) if kwargs else text


def engine_name(engine) -> str:
    """Localized name of an optional component (falls back to its own name, e.g. "FFmpeg")."""
    key = f"engine.name.{engine.id}"
    text = _catalog(_current).get(key) or _catalog("en").get(key)
    return text or engine.name
