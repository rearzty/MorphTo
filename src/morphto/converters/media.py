"""Audio and video conversion through FFmpeg (downloaded on first use)."""

from __future__ import annotations

import re
import subprocess
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from morphto import engines
from morphto.core.formats import Format
from morphto.core.options import Options
from morphto.core.registry import CancelledError, Context, ConversionError, Converter

AUDIO = {"mp3", "wav", "flac", "ogg", "opus", "m4a", "aac", "wma", "aiff"}
VIDEO_IN = {"mp4", "mkv", "webm", "mov", "avi", "wmv", "flv", "mpeg", "ts", "3gp", "ogv", "gif"}
VIDEO_OUT = {"mp4", "mkv", "webm", "mov", "avi", "wmv", "flv", "gif"}

_AUDIO_CODEC = {
    "mp3": ["-c:a", "libmp3lame", "-q:a", "2"],
    "wav": ["-c:a", "pcm_s16le"],
    "flac": ["-c:a", "flac"],
    "ogg": ["-c:a", "libvorbis", "-q:a", "5"],
    "opus": ["-c:a", "libopus", "-b:a", "128k"],
    "m4a": ["-c:a", "aac", "-b:a", "192k"],
    "aac": ["-c:a", "aac", "-b:a", "192k"],
    "wma": ["-c:a", "wmav2", "-b:a", "192k"],
    "aiff": ["-c:a", "pcm_s16be"],
}
_LOSSLESS_AUDIO = {"wav", "flac", "aiff"}
# Audio codecs that can be stream-copied into the given audio container without re-encoding.
_AUDIO_COPY = {
    "m4a": {"aac", "alac"},
    "aac": {"aac"},
    "mp3": {"mp3"},
    "opus": {"opus"},
    "ogg": {"vorbis"},
    "flac": {"flac"},
}

_X264 = ["-c:v", "libx264", "-preset", "fast", "-crf", "23", "-pix_fmt", "yuv420p"]
_VIDEO_CODEC = {
    "mp4": [*_X264, "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"],
    "mov": [*_X264, "-c:a", "aac", "-b:a", "192k"],
    "mkv": [*_X264, "-c:a", "aac", "-b:a", "192k"],
    "flv": [*_X264, "-c:a", "aac", "-b:a", "160k"],
    "webm": [
        "-c:v", "libvpx-vp9", "-crf", "33", "-b:v", "0", "-deadline", "good", "-cpu-used", "4",
        "-row-mt", "1", "-pix_fmt", "yuv420p", "-c:a", "libopus", "-b:a", "128k",
    ],
    "avi": ["-c:v", "mpeg4", "-q:v", "3", "-c:a", "libmp3lame", "-b:a", "192k"],
    "wmv": ["-c:v", "wmv2", "-q:v", "3", "-c:a", "wmav2", "-b:a", "192k"],
}  # fmt: skip
_EVEN_DIMS = {"mp4", "mov", "mkv", "flv", "webm"}
# Containers we can switch between by copying streams when codecs allow (fast, lossless).
_REMUX_VIDEO = {
    "mp4": {"h264", "hevc", "av1", "mpeg4"},
    "mov": {"h264", "hevc", "mpeg4", "prores"},
    "mkv": None,
}
_REMUX_AUDIO = {
    "mp4": {"aac", "mp3", "ac3", "eac3", "opus", "alac"},
    "mov": {"aac", "mp3", "ac3", "alac"},
    "mkv": None,
}

_GIF_FPS = 12
_GIF_WIDTH = 480

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@dataclass(frozen=True, slots=True)
class Probe:
    duration: float | None
    video_codec: str | None
    audio_codec: str | None
    width: int | None
    height: int | None
    bitrate_k: float | None = None  # overall bitrate of the source file


class FFmpegConverter(Converter):
    name = "FFmpeg"
    engine = "ffmpeg"
    cost = 10

    def pairs(self) -> Iterable[tuple[str, str]]:
        pairs = [(s, d) for s in VIDEO_IN for d in VIDEO_OUT if not (s == "gif" and d == "gif")]
        pairs += [(s, d) for s in VIDEO_IN - {"gif"} for d in AUDIO]
        pairs += [(s, d) for s in AUDIO for d in AUDIO]
        return pairs

    def convert(
        self, src: Path, dst: Path, src_fmt: Format, dst_fmt: Format, options: Options, ctx: Context
    ) -> None:
        engine = engines.get("ffmpeg")
        ffmpeg = engine.tool("ffmpeg")
        if ffmpeg is None:
            raise ConversionError("FFmpeg is not installed")
        info = probe(ffmpeg, src)
        attempts = build_attempts(dst_fmt.id, options, info)
        last_error = ""
        for args in attempts:
            try:
                run_ffmpeg(ffmpeg, src, dst, args, info.duration, ctx)
                return
            except CancelledError:
                raise
            except ConversionError as exc:  # e.g. stream copy refused; try the next (re-encoding) variant
                last_error = str(exc)
                dst.unlink(missing_ok=True)
        raise ConversionError(last_error or "FFmpeg failed")


def probe(ffmpeg: Path, src: Path) -> Probe:
    """Read duration, codecs and size from ``ffmpeg -i`` (so we don't need ffprobe, ~100 MB)."""
    cmd = [str(ffmpeg), "-hide_banner", "-nostdin", "-i", str(src)]
    result = subprocess.run(cmd, capture_output=True, creationflags=_NO_WINDOW, check=False)
    text = result.stderr.decode("utf-8", "replace")
    if "Input #0" not in text:
        raise ConversionError(_last_lines(text) or "Cannot read media file")
    return parse_probe(text)


_DURATION = re.compile(r"Duration: (\d+):(\d\d):(\d\d(?:\.\d+)?)")
_BITRATE = re.compile(r"Duration: .*?bitrate: (\d+) kb/s")
_STREAM = re.compile(r"^\s*Stream #\d+:\d+.*?: (Video|Audio): (\w+)(.*)$", re.MULTILINE)
_RESOLUTION = re.compile(r", (\d{2,5})x(\d{2,5})")


def parse_probe(text: str) -> Probe:
    duration = None
    if m := _DURATION.search(text):
        duration = int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3]) or None
    bitrate = float(m[1]) if (m := _BITRATE.search(text)) else None
    video = audio = None
    width = height = None
    for kind, codec, rest in _STREAM.findall(text):
        if kind == "Video" and video is None and "(attached pic)" not in rest:
            video = codec
            if r := _RESOLUTION.search(rest):
                width, height = int(r[1]), int(r[2])
        elif kind == "Audio" and audio is None:
            audio = codec
    return Probe(duration, video, audio, width, height, bitrate)


def build_attempts(target: str, options: Options, info: Probe) -> list[list[str]]:
    """FFmpeg output arguments to try in order: fast stream copy first (if possible), then re-encode."""
    plain = options == Options()
    attempts: list[list[str]] = []
    if target in AUDIO:
        if plain and info.audio_codec in _AUDIO_COPY.get(target, set()):
            attempts.append(["-vn", "-sn", "-c:a", "copy"])
        attempts.append(["-vn", "-sn", *_audio_args(target, options)])
        return attempts
    if info.video_codec is None:
        raise ConversionError("The file has no video stream")
    if target == "gif":
        return [_gif_args(options)]
    fits = bool(
        options.target_size_mb
        and info.bitrate_k
        and info.duration
        and info.bitrate_k * info.duration / 8192 <= options.target_size_mb * 0.92
        and options.max_side is None
        and options.quality is None
    )
    if (plain or fits) and _can_remux(target, info):
        attempts.append(["-map", "0", "-c", "copy"] if target == "mkv" else ["-c", "copy", "-sn"])
    attempts.append(_video_args(target, options, info))
    return attempts


def _can_remux(target: str, info: Probe) -> bool:
    if target not in _REMUX_VIDEO:
        return False
    video_ok, audio_ok = _REMUX_VIDEO[target], _REMUX_AUDIO[target]
    if video_ok is not None and info.video_codec not in video_ok:
        return False
    return info.audio_codec is None or audio_ok is None or info.audio_codec in audio_ok


def _audio_args(target: str, options: Options) -> list[str]:
    args = list(_AUDIO_CODEC[target])
    if options.audio_bitrate_k and target not in _LOSSLESS_AUDIO:
        args = _set_arg(_drop_arg(args, "-q:a"), "-b:a", f"{options.audio_bitrate_k}k")
    return args


def _video_args(target: str, options: Options, info: Probe) -> list[str]:
    args = list(_VIDEO_CODEC[target])
    max_side = options.max_side
    total_k = (
        options.target_size_mb * 8192 * 0.92 / info.duration
        if options.target_size_mb and info.duration
        else 0
    )
    if info.bitrate_k and total_k >= info.bitrate_k:
        total_k = 0  # already small enough: a normal quality-based encode will not grow past the limit
    if total_k:
        audio_k = min(96, max(32, int(total_k / 5))) if info.audio_codec else 0
        video_k = max(80, int(total_k - audio_k))
        args = _drop_arg(_drop_arg(args, "-crf"), "-q:v")
        args = _set_arg(args, "-b:v", f"{video_k}k")
        args += ["-maxrate", f"{video_k}k", "-bufsize", f"{video_k * 2}k"]
        if audio_k:
            args = _set_arg(args, "-b:a", f"{audio_k}k")
        if max_side is None:
            max_side = 854 if video_k < 800 else 1280 if video_k < 2000 else None
    elif options.quality and "-crf" in args:
        # quality 100 -> crf 16 (near lossless), quality 1 -> crf 40
        args = _set_arg(args, "-crf", str(round(40 - options.quality * 0.24)))
    if options.audio_bitrate_k:
        args = _set_arg(args, "-b:a", f"{options.audio_bitrate_k}k")
    filters = []
    if max_side and max(info.width or 0, info.height or 0) > max_side:
        filters.append(_fit_filter(max_side))
    if target in _EVEN_DIMS:
        filters.append("scale=trunc(iw/2)*2:trunc(ih/2)*2")
    if filters:
        args = ["-vf", ",".join(filters), *args]
    return ["-sn", *args]


def _gif_args(options: Options) -> list[str]:
    side = options.max_side or _GIF_WIDTH
    graph = (
        f"fps={_GIF_FPS},{_fit_filter(side)}:flags=lanczos,split[a][b];"
        "[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4"
    )
    return ["-an", "-sn", "-filter_complex", graph, "-loop", "0"]


def _fit_filter(side: int) -> str:
    return f"scale='if(gte(iw,ih),min({side},iw),-2)':'if(gte(iw,ih),-2,min({side},ih))'"


def _drop_arg(args: list[str], flag: str) -> list[str]:
    if flag not in args:
        return args
    i = args.index(flag)
    return args[:i] + args[i + 2 :]


def _set_arg(args: list[str], flag: str, value: str) -> list[str]:
    if flag in args:
        i = args.index(flag)
        return [*args[: i + 1], value, *args[i + 2 :]]
    return [*args, flag, value]


def run_ffmpeg(
    ffmpeg: Path, src: Path, dst: Path, args: list[str], duration: float | None, ctx: Context
) -> None:
    cmd = [
        str(ffmpeg), "-hide_banner", "-nostdin", "-y", "-loglevel", "error", "-nostats",
        "-progress", "pipe:1", "-i", str(src), *args, str(dst),
    ]  # fmt: skip
    with tempfile.TemporaryFile() as err:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=err, creationflags=_NO_WINDOW)
        assert proc.stdout is not None
        try:
            for raw in proc.stdout:
                if ctx.cancel.is_set():
                    proc.kill()
                    proc.wait()
                    raise CancelledError
                line = raw.decode("ascii", "replace").strip()
                if duration and line.startswith("out_time_us="):
                    value = line.partition("=")[2]
                    if value.isdigit():
                        ctx.progress(int(value) / 1_000_000 / duration)
            code = proc.wait()
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
        if code != 0:
            err.seek(0)
            raise ConversionError(
                _last_lines(err.read().decode("utf-8", "replace")) or f"FFmpeg exit code {code}"
            )


def _last_lines(text: str, n: int = 3) -> str:
    lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
    return " | ".join(lines[-n:])
