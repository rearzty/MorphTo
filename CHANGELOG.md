# Changelog

## [0.4.0] - 2026-10-16

MorphTo for macOS and Linux.

### Added
- macOS (11 and later, Apple Silicon and Intel): a menu-bar app in a `.dmg`. The hotkey is Control+Option+C,
  registered without asking for accessibility permissions; "Open at login".
- Linux: one AppImage for any distribution. The hotkey (Super+Alt+C) works on X11 and, under Wayland, as a GNOME
  keyboard shortcut; "Convert with MorphTo" in Nautilus, Nemo and Dolphin; start at login; an application
  menu entry. The AppImage updates itself like the Windows version.
- PDF from Markdown, HTML and e-books through any Chromium-based browser (Edge, Chrome, Chromium, Brave).
- Releases ship Windows, macOS and Linux builds with one SHA256SUMS.txt.

### Notes
- On macOS and Linux, FFmpeg, LibreOffice and the other optional components come from Homebrew or the system
  package manager; MorphTo says which command installs a missing one.
- The macOS build is not notarized by Apple yet: open it with right-click → Open the first time.

## [0.3.0] - 2026-10-15

E-books, Markdown, data, archives, SVG, camera RAW and JPEG XL.

### Added
- Markdown, HTML, FB2, EPUB and TXT convert into each other and to DOCX and PDF (printed by the Microsoft
  Edge that comes with Windows). Word documents also become EPUB, FB2 and Markdown.
- Data: CSV, XLSX, JSON, YAML and XML convert into each other, without LibreOffice.
- Archives: ZIP, 7Z, TAR, TAR.GZ and RAR unpack into a folder or repack into another format.
- SVG, camera RAW and JPEG XL, with small components downloaded on first use (2–5 MB each).
- iPhone ringtone (M4R, the first 40 seconds).
- The installer says when it updates an existing version and skips the license page then.

### Fixed
- Pasting a clipboard image that MorphTo announced but could not read: more image formats are read, and a
  message explains when it still fails.
- Ctrl+V and Ctrl+O in the popup work on any keyboard layout (e.g. Russian).

## [0.2.0] - 2026-10-15

Documents, spreadsheets, presentations and update notifications.

### Added
- PDF to PNG, JPEG, WebP, BMP or TIFF, built in (PDFium). Several pages give a folder of images
  (`page-01.png`…) or one multi-page TIFF. PDF to TXT extracts the text.
- Word (DOCX, DOC), OpenDocument (ODT, ODS, ODP), RTF and TXT, Excel (XLSX, XLS, CSV) and PowerPoint (PPTX, PPT):
  to PDF, to each other within the same kind, and to images page by page.
- These office conversions use an installed LibreOffice, or download it on first use (~360 MB, unpacked into
  MorphTo's folder, nothing installed into Windows). With Microsoft Office installed, Word / Excel / PowerPoint to
  PDF goes through Office and needs no download.
- Office files are recognised by their content, not only by the extension.
- Update notifications: MorphTo checks GitHub for a new version at start and once a day (can be turned off in
  Settings). An “Update” button in the popup and the tray menu downloads the installer, verifies it and
  updates in place; the portable version opens the download page.

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
- Explorer context menu, start with Windows (on by default in the installer), batch conversion.
- Light and dark themes, acrylic background on Windows 11, 13 languages.
- Command line: `morphto convert`, `formats`, `presets`, `engines`.
- Fully offline; no telemetry.
