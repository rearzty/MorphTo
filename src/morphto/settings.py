"""Persistent user settings (JSON in %APPDATA%\\MorphTo\\settings.json)."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from morphto.paths import config_dir, default_output_dir
from morphto.platform.hotkeys import DEFAULT_HOTKEY

OUTPUT_MODES = ("next", "folder", "ask")


@dataclass
class Settings:
    language: str = ""  # "" = follow Windows
    hotkey: str = DEFAULT_HOTKEY
    output_mode: str = "next"  # next | folder | ask
    output_dir: str = field(default_factory=lambda: str(default_output_dir()))
    copy_to_clipboard: bool = True
    autostart: bool = False
    context_menu: bool = False
    popup_pos: list[int] | None = None  # where the user dragged the popup; None = centered
    effects: bool = True  # Windows 11 acrylic backdrop for the popup
    check_updates: bool = True  # ask GitHub for a newer release at start and once a day
    update_notified: str = ""  # last version announced with a notification (announce each one once)
    # source format id -> {target id -> times used}; drives smart suggestions
    history: dict[str, dict[str, int]] = field(default_factory=dict)

    @staticmethod
    def path() -> Path:
        return config_dir() / "settings.json"

    @classmethod
    def load(cls) -> Settings:
        try:
            raw = json.loads(cls.path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        known = {f.name for f in fields(cls)}
        settings = cls(**{k: v for k, v in raw.items() if k in known})
        if settings.output_mode not in OUTPUT_MODES:
            settings.output_mode = "next"
        return settings

    def save(self) -> None:
        path = self.path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

    def record_use(self, source_format: str, target_id: str) -> None:
        counts = self.history.setdefault(source_format, {})
        counts[target_id] = counts.get(target_id, 0) + 1
