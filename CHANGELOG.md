# Changelog

## [0.1.0] - 2026-10-12

First public beta.

**Download:** `MorphTo-0.1.0-setup.exe` (installer, no admin rights needed) or `MorphTo-0.1.0-portable.zip`.
Windows 10 (1809+) and 11, 64-bit. The app is not code-signed yet: if SmartScreen warns, click
*More info → Run anyway*. Checksums are in `SHA256SUMS.txt`.

### Features
- Tray app with a popup on <kbd>Win</kbd>+<kbd>Alt</kbd>+<kbd>C</kbd>: paste (<kbd>Ctrl</kbd>+<kbd>V</kbd>), drop or browse,
  type a format, press <kbd>Enter</kbd>.
- Images: PNG, JPEG, WebP, AVIF, HEIC/HEIF, GIF (animated), BMP, TIFF, ICO, ICNS, TGA, JPEG 2000, PSD (read), PDF (write).
- Audio and video through FFmpeg, downloaded on first use (~34 MB); fast stream copy when only the container changes.
- Saving to HEIC with an encoder downloaded on first use (~7 MB).
- Presets: Discord (video under 10 MB), WebP for the web, Windows icon, smaller photo, small MP3.
- Result copied to the clipboard and draggable out of the popup; size before → after.
- Smart suggestions that learn which formats you use.
- Progress on the taskbar button and the tray icon while working.
- Explorer context menu, start with Windows, batch conversion.
- Light and dark themes, acrylic background on Windows 11, 13 languages.
- Command line: `morphto convert`, `formats`, `presets`, `engines`.
- Fully offline; no telemetry.
