"""Background jobs so the UI never blocks."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Signal

from morphto import engines, updates
from morphto.core import ConversionError, convert
from morphto.core.registry import CancelledError
from morphto.core.targets import Target
from morphto.platform import clipboard


@dataclass(frozen=True)
class Job:
    source: Path
    out_dir: Path | None = None  # used when output is None
    output: Path | None = None


class ConvertSignals(QObject):
    progress = Signal(int, int, float)  # index, total, fraction of current file
    finished = Signal(list, list, bool, bool)  # results: list[Path], errors: list[str], cancelled, copied


class ConvertTask(QRunnable):
    def __init__(self, jobs: list[Job], target: Target, copy_to_clipboard: bool = False) -> None:
        super().__init__()
        self.jobs = jobs
        self.target = target
        self.copy_to_clipboard = copy_to_clipboard
        self.cancel = threading.Event()
        self.signals = ConvertSignals()

    def run(self) -> None:
        results: list[Path] = []
        errors: list[str] = []
        total = len(self.jobs)
        for i, job in enumerate(self.jobs):
            if self.cancel.is_set():
                break
            try:
                result = convert(
                    job.source, self.target.format, out_dir=job.out_dir, output=job.output,
                    options=self.target.options, cancel=self.cancel,
                    progress=lambda f, i=i: self.signals.progress.emit(i, total, f),
                )  # fmt: skip
                results.append(result)
            except CancelledError:
                break
            except ConversionError as exc:
                errors.append(f"{job.source.name}: {exc}")
            except Exception as exc:  # never let a worker die silently
                errors.append(f"{job.source.name}: {exc}")
        cancelled = self.cancel.is_set()
        copied = bool(results) and not cancelled and self.copy_to_clipboard and clipboard.write_files(results)
        self.signals.finished.emit(results, errors, cancelled, copied)


class InstallSignals(QObject):
    progress = Signal(float)
    finished = Signal(str)  # empty string = success, else error message


class InstallTask(QRunnable):
    def __init__(self, engine_ids: list[str]) -> None:
        super().__init__()
        self.engine_ids = engine_ids
        self.cancel = threading.Event()
        self.signals = InstallSignals()

    def run(self) -> None:
        n = len(self.engine_ids)
        try:
            for i, engine_id in enumerate(self.engine_ids):
                engines.get(engine_id).install(
                    lambda f, i=i: self.signals.progress.emit((i + f) / n), self.cancel
                )
        except Exception as exc:
            self.signals.finished.emit(str(exc) or exc.__class__.__name__)
            return
        self.signals.finished.emit("")


class UpdateCheckSignals(QObject):
    finished = Signal(object, str)  # updates.Release or None, error message ("" = checked fine)


class UpdateCheckTask(QRunnable):
    def __init__(self) -> None:
        super().__init__()
        self.signals = UpdateCheckSignals()

    def run(self) -> None:
        try:
            release = updates.check()
        except Exception as exc:  # offline, rate-limited, GitHub down: try again next time
            self.signals.finished.emit(None, str(exc) or exc.__class__.__name__)
            return
        self.signals.finished.emit(release, "")


class UpdateDownloadSignals(QObject):
    progress = Signal(float)
    finished = Signal(str, str)  # installer path, error message ("" = success)


class UpdateDownloadTask(QRunnable):
    def __init__(self, release: updates.Release) -> None:
        super().__init__()
        self.release = release
        self.cancel = threading.Event()
        self.signals = UpdateDownloadSignals()

    def run(self) -> None:
        try:
            path = updates.download_installer(self.release, self.signals.progress.emit, self.cancel)
        except Exception as exc:
            self.signals.finished.emit("", str(exc) or exc.__class__.__name__)
            return
        self.signals.finished.emit(str(path), "")
