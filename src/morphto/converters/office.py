"""Word / Excel / PowerPoint / OpenDocument files: LibreOffice (headless), or Microsoft Office when
it is installed (for export to PDF, where its layout is exact)."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import threading
from collections.abc import Iterable
from pathlib import Path

from morphto import engines
from morphto.core.formats import Format
from morphto.core.options import Options
from morphto.core.registry import Context, ConversionError, Converter

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
TIMEOUT_S = 300

# Format families: LibreOffice converts within a family, and every family exports to PDF.
WRITER = ("docx", "doc", "odt", "rtf", "txt")
CALC = ("xlsx", "xls", "ods", "csv")
IMPRESS = ("pptx", "ppt", "odp")

# --convert-to arguments: "<extension>:<export filter>[:<options>]".
_FILTERS = {
    "writer": {
        "pdf": "pdf:writer_pdf_Export", "docx": "docx:MS Word 2007 XML", "doc": "doc:MS Word 97",
        "odt": "odt:writer8", "rtf": "rtf:Rich Text Format", "txt": "txt:Text (encoded):UTF8",
    },
    "calc": {
        "pdf": "pdf:calc_pdf_Export", "xlsx": "xlsx:Calc Office Open XML", "xls": "xls:MS Excel 97",
        "ods": "ods:calc8", "csv": "csv:Text - txt - csv (StarCalc):44,34,76,1,,0,false,true,true",  # comma-separated UTF-8, quotes only where needed
    },
    "impress": {
        "pdf": "pdf:impress_pdf_Export", "pptx": "pptx:Impress Office Open XML",
        "ppt": "ppt:MS PowerPoint 97", "odp": "odp:impress8",
    },
}  # fmt: skip
_FAMILIES = {"writer": WRITER, "calc": CALC, "impress": IMPRESS}
_FAMILY = {fmt: family for family, fmts in _FAMILIES.items() for fmt in fmts}

# One headless LibreOffice at a time: two instances cannot share a profile.
_lo_lock = threading.Lock()


def _run(cmd: list[str], ctx: Context, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    """Run a converter process, killing it on cancel or after TIMEOUT_S."""
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, creationflags=_NO_WINDOW
    )
    waited = 0.0
    try:
        while True:
            try:
                out, err = proc.communicate(timeout=0.25)
                break
            except subprocess.TimeoutExpired:
                waited += 0.25
                if waited > TIMEOUT_S:
                    raise ConversionError(f"Timed out after {TIMEOUT_S} s") from None
                ctx.check_cancelled()
                ctx.progress(min(0.9, waited / 20))
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    return subprocess.CompletedProcess(cmd, proc.returncode, out, err)


class LibreOfficeConverter(Converter):
    name = "LibreOffice"
    engine = "libreoffice"
    cost = 20

    def pairs(self) -> Iterable[tuple[str, str]]:
        return [
            (s, d)
            for family, filters in _FILTERS.items()
            for s in _FAMILIES[family]
            for d in filters
            if s != d
        ]

    def convert(
        self, src: Path, dst: Path, src_fmt: Format, dst_fmt: Format, options: Options, ctx: Context
    ) -> None:
        engine = engines.get("libreoffice")
        soffice = engine.tool("soffice")
        if soffice is None:
            raise ConversionError("LibreOffice is not installed")
        export = _FILTERS[_FAMILY[src_fmt.id]][dst_fmt.id]
        with tempfile.TemporaryDirectory(prefix="morphto-lo-") as tmp:
            # LibreOffice names the output after the input; give it a plain name with the right extension.
            work_src = Path(tmp) / f"source.{src_fmt.ext}"
            shutil.copyfile(src, work_src)
            out_dir = Path(tmp) / "out"
            cmd = [
                str(soffice), "--headless", "--invisible", "--norestore", "--nolockcheck", "--nodefault",
                f"-env:UserInstallation={engine.profile_dir.as_uri()}",
                "--convert-to", export, "--outdir", str(out_dir), str(work_src),
            ]  # fmt: skip
            with _lo_lock:
                # The very first run builds the profile, and some versions exit (or crash) right after
                # that without converting; the second run then works.
                for _attempt in range(2):
                    result = _run(cmd, ctx)
                    produced = [p for p in out_dir.glob("*") if p.is_file()] if out_dir.is_dir() else []
                    if produced:
                        break
            if not produced:
                detail = (result.stderr or result.stdout or b"").decode("utf-8", "replace").strip()
                raise ConversionError(f"LibreOffice could not convert the file. {detail[-300:]}".strip())
            shutil.move(str(produced[0]), dst)


# ---- Microsoft Office ----

_OFFICE_APPS = {
    "writer": "Word.Application",
    "calc": "Excel.Application",
    "impress": "PowerPoint.Application",
}
_POWERSHELL = (
    Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
)

# PowerShell + COM automation. Paths come in through environment variables, so no quoting issues.
# Quit() only if nothing else is open: PowerPoint is single-instance and may hold the user's own files.
_OFFICE_SCRIPTS = {
    "writer": r"""
$app = New-Object -ComObject Word.Application
$app.Visible = $false; $app.DisplayAlerts = 0
try {
  $doc = $app.Documents.Open($env:MORPHTO_SRC, $false, $true, $false)
  $doc.ExportAsFixedFormat($env:MORPHTO_DST, 17)
  $doc.Close(0)
} finally { if ($app.Documents.Count -eq 0) { $app.Quit() } }
""",
    "calc": r"""
$app = New-Object -ComObject Excel.Application
$app.Visible = $false; $app.DisplayAlerts = $false
try {
  $book = $app.Workbooks.Open($env:MORPHTO_SRC, 0, $true)
  $book.ExportAsFixedFormat(0, $env:MORPHTO_DST)
  $book.Close($false)
} finally { if ($app.Workbooks.Count -eq 0) { $app.Quit() } }
""",
    "impress": r"""
$app = New-Object -ComObject PowerPoint.Application
try {
  $pres = $app.Presentations.Open($env:MORPHTO_SRC, -1, 0, 0)
  $pres.SaveAs($env:MORPHTO_DST, 32)
  $pres.Close()
} finally { if ($app.Presentations.Count -eq 0) { $app.Quit() } }
""",
}


def office_families() -> list[str]:
    """Families whose Microsoft Office application is registered on this machine."""
    if os.name != "nt":
        return []
    import winreg

    found = []
    for family, prog_id in _OFFICE_APPS.items():
        try:
            winreg.CloseKey(winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, rf"{prog_id}\CLSID"))
        except OSError:
            continue
        found.append(family)
    return found


class MsOfficeConverter(Converter):
    """Export to PDF through the installed Word / Excel / PowerPoint. Preferred over LibreOffice (lower
    cost) and needs no download; if Office fails, LibreOffice is used when it is available."""

    name = "Microsoft Office"
    cost = 15

    def __init__(self, families: list[str]) -> None:
        self.families = families

    def pairs(self) -> Iterable[tuple[str, str]]:
        return [(s, "pdf") for family in self.families for s in _FAMILIES[family]]

    def convert(
        self, src: Path, dst: Path, src_fmt: Format, dst_fmt: Format, options: Options, ctx: Context
    ) -> None:
        family = _FAMILY[src_fmt.id]
        with tempfile.TemporaryDirectory(prefix="morphto-office-") as tmp:
            work_src = Path(tmp) / f"source.{src_fmt.ext}"  # Office picks the reader by extension
            shutil.copyfile(src, work_src)
            env = {**os.environ, "MORPHTO_SRC": str(work_src), "MORPHTO_DST": str(dst)}
            cmd = [str(_POWERSHELL), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                   "-Command", _OFFICE_SCRIPTS[family]]  # fmt: skip
            result = _run(cmd, ctx, env)
        if dst.is_file():
            return
        if engines.get("libreoffice").available():
            dst.unlink(missing_ok=True)
            LibreOfficeConverter().convert(src, dst, src_fmt, dst_fmt, options, ctx)
            return
        detail = (result.stderr or b"").decode("utf-8", "replace").strip()
        raise ConversionError(f"Microsoft Office could not convert the file. {detail[-300:]}".strip())
