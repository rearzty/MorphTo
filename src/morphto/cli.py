"""Command-line interface: ``morphto convert photo.heic -t png`` and friends.

Plain argparse on purpose: no third-party CLI libraries, so the app stays small and starts fast.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import sys
import time
from pathlib import Path

from morphto import __version__, engines

_COLOR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def _c(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _COLOR else text


def green(t: str) -> str:
    return _c(t, "32")


def red(t: str) -> str:
    return _c(t, "31")


def cyan(t: str) -> str:
    return _c(t, "36")


def magenta(t: str) -> str:
    return _c(t, "35")


def dim(t: str) -> str:
    return _c(t, "2")


class _ProgressLine:
    """A single self-updating line: ``name  [#####-----]  42%``."""

    def __init__(self, label: str) -> None:
        self.label = label
        self.enabled = sys.stderr.isatty()
        self._last = -1.0

    def __call__(self, fraction: float) -> None:
        if not self.enabled or (fraction - self._last < 0.01 and fraction < 1):
            return
        self._last = fraction
        width = 24
        filled = int(width * fraction)
        sys.stderr.write(f"\r{self.label[:40]:<40} [{'#' * filled}{'-' * (width - filled)}] {fraction:4.0%}")
        sys.stderr.flush()

    def clear(self) -> None:
        if self.enabled:
            sys.stderr.write("\r" + " " * 76 + "\r")
            sys.stderr.flush()


def cmd_convert(args: argparse.Namespace) -> int:
    from morphto.core import ConversionError, EngineMissingError, Options, convert
    from morphto.core.targets import find_target

    try:
        target = find_target(args.to)
    except KeyError:
        print(red(f"Unknown format or preset: {args.to}") + ". See `morphto formats`.", file=sys.stderr)
        return 2
    options = target.options.merged(
        Options(quality=args.quality, max_side=args.max_side, target_size_mb=args.size_mb)
    )
    failed = 0
    for src in args.files:
        if not src.is_file():
            print(f"{red('✗')} {src}: file not found", file=sys.stderr)
            failed += 1
            continue
        while True:
            bar = _ProgressLine(src.name)
            started = time.perf_counter()
            try:
                result = convert(src, target.format, out_dir=args.out, options=options, progress=bar)
            except EngineMissingError as exc:
                bar.clear()
                if not _install_engines(exc.engines, assume_yes=args.yes):
                    return 1
                continue
            except ConversionError as exc:
                bar.clear()
                print(f"{red('✗')} {src.name}: {exc}", file=sys.stderr)
                failed += 1
            else:
                bar.clear()
                print(f"{green('✓')} {src.name} → {result} {dim(f'({time.perf_counter() - started:.1f}s)')}")
            break
    return 1 if failed else 0


def _install_engines(ids: list[str], assume_yes: bool) -> bool:
    for engine_id in ids:
        engine = engines.get(engine_id)
        if not assume_yes:
            if not sys.stdin.isatty():
                print(
                    red(f"{engine.name} is required.")
                    + f" Run with --yes or `morphto engines install {engine.id}`.",
                    file=sys.stderr,
                )
                return False
            answer = input(
                f"{engine.name} is required (~{engine.size_mb} MB, one-time download). Install? [Y/n] "
            )
            if answer.strip().lower() not in ("", "y", "yes", "д", "да"):
                return False
        if _install(engine) != 0:
            return False
    return True


def _install(engine: engines.Engine) -> int:
    bar = _ProgressLine(f"Downloading {engine.name}")
    try:
        engine.install(bar)
    except engines.EngineInstallError as exc:
        bar.clear()
        print(red(f"Could not install {engine.name}: {exc}"), file=sys.stderr)
        return 1
    bar.clear()
    print(f"{green('✓')} {engine.name} installed to {engine.install_dir}")
    return 0


def cmd_formats(args: argparse.Namespace) -> int:
    from morphto.core import all_formats
    from morphto.core.detect import detect
    from morphto.core.registry import default_registry
    from morphto.core.targets import available_targets, rank

    registry = default_registry()
    if args.file is not None:
        fmt = detect(args.file)
        if fmt is None:
            print(red(f"Unsupported file: {args.file.name}"), file=sys.stderr)
            return 1
        print(f"{args.file.name} is {cyan(fmt.name)}. Can be converted to:")
        for t in rank(available_targets([fmt]), fmt):
            print(f"  {magenta(t.preset)} (preset → {t.format.ext})" if t.preset else f"  {cyan(t.id)}")
        return 0

    readable = registry.sources()
    writable = {d for s in readable for d in registry.targets(s, 1)}
    needs: dict[str, str] = {}
    for conv in registry.converters:
        if conv.engine:
            for s, d in conv.pairs():
                needs.setdefault(s, conv.engine)
                needs.setdefault(d, conv.engine)
    print(f"{'FORMAT':<7} {'NAME':<22} {'READ':<5} {'WRITE':<6} {'NEEDS':<7} EXTENSIONS")
    category = None
    for fmt in sorted(all_formats(), key=lambda f: list(type(f.category)).index(f.category)):
        if fmt.category != category:
            category = fmt.category
            print(dim(f"-- {category.value} " + "-" * 50))
        print(
            f"{cyan(f'{fmt.id:<7}')} {fmt.name:<22} {'yes' if fmt.id in readable else '':<5} "
            f"{'yes' if fmt.id in writable else '':<6} {needs.get(fmt.id, ''):<7} {', '.join(fmt.extensions)}"
        )
    return 0


def cmd_presets(_args: argparse.Namespace) -> int:
    from morphto.core.targets import PRESETS
    from morphto.i18n import tr

    for p in PRESETS:
        print(f"{magenta(f'{p.key:<10}')} → {p.format_id:<5} {tr('preset.' + p.key)}")
    return 0


def cmd_engines(args: argparse.Namespace) -> int:
    if args.action in ("install", "remove"):
        if not args.name:
            print(red(f"Specify a component, e.g. `morphto engines {args.action} ffmpeg`."), file=sys.stderr)
            return 2
        try:
            engine = engines.get(args.name)
        except KeyError:
            print(red(f"Unknown component: {args.name}"), file=sys.stderr)
            return 2
        if args.action == "install":
            return _install(engine)
        engine.remove()
        print(f"Removed {engine.name}.")
        return 0
    for engine in engines.all_engines():
        engine.prune()
        source = engine.source()
        status = {"managed": green("installed"), "system": green("found on PATH")}.get(
            source or "", f"not installed (~{engine.size_mb} MB download)"
        )
        print(f"{engine.id:<8} {engine.name:<10} {status}")
    return 0


def cmd_gui(args: argparse.Namespace) -> int:
    from morphto.gui import launch

    return launch([str(f.resolve()) for f in args.files], background=args.background)


def _enable_ansi() -> None:
    """Turn on ANSI colors in the classic Windows console."""
    import ctypes

    kernel32 = ctypes.windll.kernel32
    handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
    mode = ctypes.c_uint32()
    if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
        kernel32.SetConsoleMode(handle, mode.value | 0x0004)  # ENABLE_VIRTUAL_TERMINAL_PROCESSING


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="morphto", description="MorphTo — fast, offline file converter.")
    parser.add_argument("-V", "--version", action="version", version=f"MorphTo {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    p = sub.add_parser("convert", help="convert one or more files")
    p.add_argument("files", nargs="+", type=Path, help="files to convert")
    p.add_argument(
        "-t", "--to", required=True, help="target format (png, mp3 ...) or preset (web, discord ...)"
    )
    p.add_argument("-o", "--out", type=Path, help="output folder (default: next to each file)")
    p.add_argument("-q", "--quality", type=int, choices=range(1, 101), metavar="1-100", help="quality")
    p.add_argument("-s", "--max-side", type=int, help="downscale so the longest side fits (pixels)")
    p.add_argument("--size-mb", type=float, help="target size for video, MB")
    p.add_argument("-y", "--yes", action="store_true", help="install required components without asking")
    p.set_defaults(func=cmd_convert)

    p = sub.add_parser("formats", help="list formats, or what a file can be converted to")
    p.add_argument("file", nargs="?", type=Path)
    p.set_defaults(func=cmd_formats)

    p = sub.add_parser("presets", help="list task presets (use them with --to)")
    p.set_defaults(func=cmd_presets)

    p = sub.add_parser("engines", help="show, install or remove optional components (FFmpeg)")
    p.add_argument("action", nargs="?", choices=["list", "install", "remove"], default="list")
    p.add_argument("name", nargs="?")
    p.set_defaults(func=cmd_engines)

    p = sub.add_parser("gui", help="start the tray app (or show the popup if it is running)")
    p.add_argument("files", nargs="*", type=Path)
    p.add_argument("--background", action="store_true", help="start in the tray without the popup")
    p.set_defaults(func=cmd_gui)
    return parser


def main(argv: list[str] | None = None) -> None:
    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr):
            with contextlib.suppress(AttributeError, ValueError):
                stream.reconfigure(encoding="utf-8")  # ✓ / → on old code pages
        _enable_ansi()
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        sys.exit(0)
    sys.exit(args.func(args))
