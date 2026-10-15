"""Window effects: Windows 11 acrylic through DWM; other systems draw the popup's own background."""

import sys

if sys.platform == "win32":
    from morphto.gui.win_effects import (
        apply_acrylic,
        backdrop_supported,
        handle_native_event,
        prepare_captionless,
    )
else:

    def backdrop_supported() -> bool:
        return False

    def prepare_captionless(hwnd: int) -> None:
        pass

    def apply_acrylic(hwnd: int, dark: bool) -> bool:
        return False

    def handle_native_event(message: int):
        return None


__all__ = ["apply_acrylic", "backdrop_supported", "handle_native_event", "prepare_captionless"]
