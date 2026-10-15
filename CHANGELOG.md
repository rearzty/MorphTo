# Changelog

## [0.2.0] - 2026-10-15

Documents, spreadsheets and presentations.

### Added
- PDF to PNG, JPEG, WebP, BMP or TIFF, built in (PDFium). Several pages give a folder of images
  (`page-01.png`…) or one multi-page TIFF. PDF to TXT extracts the text.
- Word (DOCX, DOC), OpenDocument (ODT, ODS, ODP), RTF and TXT, Excel (XLSX, XLS, CSV) and PowerPoint (PPTX, PPT):
  to PDF, to each other within the same kind, and to images page by page.
- These office conversions use an installed LibreOffice, or download it on first use (~360 MB, unpacked into
  MorphTo's folder, nothing installed into Windows). With Microsoft Office installed, Word / Excel / PowerPoint to
  PDF goes through Office and needs no download.
- Office files are recognised by their content, not only by the extension.

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
