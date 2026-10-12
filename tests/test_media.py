import threading

import pytest

from morphto.converters.media import Probe, build_attempts
from morphto.core import Options, convert
from morphto.core.detect import detect
from morphto.core.registry import CancelledError

H264_AAC = Probe(duration=60, video_codec="h264", audio_codec="aac", width=1920, height=1080, bitrate_k=8000)


def test_remux_is_tried_first_for_compatible_codecs():
    attempts = build_attempts("mkv", Options(), H264_AAC)
    assert attempts[0] == ["-map", "0", "-c", "copy"]
    assert "libx264" in attempts[-1]


def test_no_remux_when_codec_incompatible():
    vp9 = Probe(duration=60, video_codec="vp9", audio_codec="opus", width=640, height=480)
    attempts = build_attempts("mov", Options(), vp9)
    assert len(attempts) == 1 and "libx264" in attempts[0]


def test_audio_stream_copy():
    attempts = build_attempts("m4a", Options(), H264_AAC)
    assert attempts[0] == ["-vn", "-sn", "-c:a", "copy"]


def test_target_size_sets_bitrate_and_downscales():
    args = build_attempts("mp4", Options(target_size_mb=10), H264_AAC)[-1]
    assert "-crf" not in args
    video_k = int(args[args.index("-b:v") + 1].rstrip("k"))
    assert 1000 < video_k < 1300  # ~10 MB over 60 s minus audio
    assert "min(1280" in args[args.index("-vf") + 1]


def test_target_size_already_small_enough_copies():
    small = Probe(duration=60, video_codec="h264", audio_codec="aac", width=640, height=360, bitrate_k=500)
    attempts = build_attempts("mp4", Options(target_size_mb=10), small)
    assert attempts[0][:2] == ["-c", "copy"]


def test_audio_bitrate_option():
    args = build_attempts("mp3", Options(audio_bitrate_k=96), H264_AAC)[-1]
    assert "-q:a" not in args and args[args.index("-b:a") + 1] == "96k"


@pytest.mark.parametrize("target", ["mkv", "webm", "gif", "mp3", "wav", "avi", "mov"])
def test_video_conversions(clip, target):
    out = convert(clip, target)
    assert out.is_file() and out.stat().st_size > 0
    assert detect(out).id == target


def test_gif_to_mp4(animated_gif, ffmpeg):
    out = convert(animated_gif, "mp4")
    assert detect(out).id == "mp4"


def test_cancel(clip):
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(CancelledError):
        convert(clip, "webm", cancel=cancel)


FFMPEG_I_VIDEO = """Input #0, mov,mp4,m4a,3gp,3g2,mj2, from 'clip.mp4':
  Duration: 00:01:08.50, start: 0.000000, bitrate: 3211 kb/s
  Stream #0:0[0x1](und): Video: h264 (High) (avc1 / 0x31637661), yuv420p(progressive), 1280x720 [SAR 1:1 DAR 16:9], 3132 kb/s, 30 fps (default)
  Stream #0:1[0x2](und): Audio: aac (LC) (mp4a / 0x6134706D), 44100 Hz, mono, fltp, 69 kb/s (default)
"""

FFMPEG_I_MP3_WITH_COVER = """Input #0, mp3, from 'song.mp3':
  Duration: 00:03:00.00, start: 0.025057, bitrate: 320 kb/s
  Stream #0:0: Audio: mp3 (mp3float), 44100 Hz, stereo, fltp, 320 kb/s
  Stream #0:1: Video: mjpeg (Baseline), yuvj420p(pc, bt470bg/unknown/unknown), 600x600 [SAR 1:1 DAR 1:1], 90k tbr, 90k tbn (attached pic)
"""


def test_parse_probe_video():
    from morphto.converters.media import parse_probe

    info = parse_probe(FFMPEG_I_VIDEO)
    assert info.duration == pytest.approx(68.5)
    assert (info.video_codec, info.audio_codec) == ("h264", "aac")
    assert (info.width, info.height, info.bitrate_k) == (1280, 720, 3211)


def test_parse_probe_ignores_cover_art():
    from morphto.converters.media import parse_probe

    info = parse_probe(FFMPEG_I_MP3_WITH_COVER)
    assert info.video_codec is None and info.audio_codec == "mp3"


def test_parse_probe_no_duration():
    from morphto.converters.media import parse_probe

    info = parse_probe(
        "Input #0, image2\n  Duration: N/A, bitrate: N/A\n  Stream #0:0: Video: png, rgba, 64x48\n"
    )
    assert info.duration is None and info.video_codec == "png" and info.width == 64
