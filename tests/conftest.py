from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from PIL import Image

from morphto import engines

# Resolve a real FFmpeg before tests redirect MORPHTO_HOME to a temp dir.
_FFMPEG = engines.get("ffmpeg").tool("ffmpeg")


@pytest.fixture(autouse=True)
def isolated_home(tmp_path_factory, monkeypatch):
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("MORPHTO_HOME", str(home))
    if _FFMPEG:
        monkeypatch.setenv("MORPHTO_FFMPEG", str(_FFMPEG))
    return home


@pytest.fixture
def ffmpeg():
    if not _FFMPEG:
        pytest.skip("FFmpeg not available (run `morphto engines install ffmpeg`)")
    return _FFMPEG


@pytest.fixture
def png(tmp_path) -> Path:
    path = tmp_path / "sample.png"
    im = Image.linear_gradient("L").convert("RGBA").resize((320, 200))
    im.putpixel((0, 0), (255, 0, 0, 0))  # some transparency
    im.save(path)
    return path


@pytest.fixture
def animated_gif(tmp_path) -> Path:
    path = tmp_path / "anim.gif"
    frames = [Image.new("RGB", (64, 48), (i * 40, 100, 200 - i * 40)) for i in range(5)]
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=80, loop=0)
    return path


@pytest.fixture
def clip(tmp_path, ffmpeg) -> Path:
    path = tmp_path / "clip.mp4"
    subprocess.run(
        [
            str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=25:duration=2",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
            "-c:v", "libx264", "-c:a", "aac", "-shortest", str(path),
        ],
        check=True,
    )  # fmt: skip
    return path
