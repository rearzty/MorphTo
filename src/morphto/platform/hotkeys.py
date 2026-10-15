"""Hotkey text shared by every platform: "Win+Alt+C" style, stored the same way everywhere.

"Win" is the logo key: Windows key, Super on Linux, Command on a Mac. Only how it is shown differs.
"""

from __future__ import annotations

import sys
from collections.abc import Iterable

MOD_ORDER = ["Ctrl", "Alt", "Shift", "Win"]
HOTKEY_KEYS = (
    [chr(c) for c in range(ord("A"), ord("Z") + 1)]
    + [str(d) for d in range(10)]
    + [f"F{n}" for n in range(1, 13)]
    + ["Space", "Insert", "Pause", "`"]
)

if sys.platform == "darwin":
    # Cmd+Option+C is Finder's "Copy as Pathname", so the Mac default is Control+Option+C.
    DEFAULT_HOTKEY = "Ctrl+Alt+C"
    MOD_LABELS = {"Ctrl": "⌃ Control", "Alt": "⌥ Option", "Shift": "⇧ Shift", "Win": "⌘ Command"}
    _SHORT = {"Ctrl": "⌃", "Alt": "⌥", "Shift": "⇧", "Win": "⌘"}
elif sys.platform == "win32":
    DEFAULT_HOTKEY = "Win+Alt+C"
    MOD_LABELS = {m: m for m in MOD_ORDER}
    _SHORT = {}
else:
    DEFAULT_HOTKEY = "Win+Alt+C"
    MOD_LABELS = {"Ctrl": "Ctrl", "Alt": "Alt", "Shift": "Shift", "Win": "Super"}
    _SHORT = {}


def format_hotkey(modifiers: Iterable[str], key: str) -> str:
    mods = [m for m in MOD_ORDER if m in set(modifiers)]
    return "+".join([*mods, key])


def split_hotkey(text: str) -> tuple[list[str], str]:
    parts = [p.strip() for p in text.split("+")]
    mods = [m for m in MOD_ORDER if m.lower() in {p.lower() for p in parts[:-1]}]
    return mods, parts[-1]


def display_hotkey(text: str) -> str:
    """How the user sees it: "Win+Alt+C" on Windows, "Super+Alt+C" on Linux, "⌃⌥C" on a Mac."""
    if _SHORT:  # Apple's order: Control, Option, Shift, Command
        mods, key = split_hotkey(text)
        return "".join(_SHORT[m] for m in mods) + key
    labels = {m.lower(): label for m, label in MOD_LABELS.items()}
    return "+".join(
        labels.get(p.strip().lower(), p.strip()) for p in text.split("+")
    )  # keep the user's order
