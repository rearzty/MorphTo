"""OS integration. ``system`` is the module for the running OS (windows, macos or linux); all three offer
the same functions: HotkeyListener, acquire_single_instance, set_autostart, set_context_menu, reveal..."""

import sys

if sys.platform == "win32":
    from morphto.platform import windows as system
elif sys.platform == "darwin":
    from morphto.platform import macos as system
else:
    from morphto.platform import linux as system

__all__ = ["system"]
