import io

import pytest
from PIL import Image

from morphto.platform.clipboard import _dib_to_image
from morphto.platform.windows import (
    MOD_ALT,
    MOD_CONTROL,
    MOD_SHIFT,
    MOD_WIN,
    format_hotkey,
    parse_hotkey,
    split_hotkey,
)


@pytest.mark.parametrize(
    ("text", "mods", "vk"),
    [
        ("Win+Alt+C", MOD_WIN | MOD_ALT, ord("C")),
        ("ctrl+shift+f9", MOD_CONTROL | MOD_SHIFT, 0x78),
        ("Ctrl+Alt+Space", MOD_CONTROL | MOD_ALT, 0x20),
        ("Alt+1", MOD_ALT, ord("1")),
    ],
)
def test_parse_hotkey(text, mods, vk):
    assert parse_hotkey(text) == (mods, vk)


@pytest.mark.parametrize("text", ["C", "Foo+C", "Ctrl+Nope", ""])
def test_parse_hotkey_rejects(text):
    with pytest.raises(ValueError):
        parse_hotkey(text)


def test_hotkey_roundtrip():
    assert format_hotkey(["Win", "Ctrl"], "M") == "Ctrl+Win+M"
    assert split_hotkey("Win+Alt+C") == (["Alt", "Win"], "C")


@pytest.mark.parametrize("mode", ["RGB", "RGBA"])
def test_dib_decoding(mode):
    src = Image.new(mode, (7, 5), (10, 200, 30, 255)[: len(mode)])
    bmp = io.BytesIO()
    src.save(bmp, "BMP")
    image = _dib_to_image(bmp.getvalue()[14:])  # clipboard DIB = BMP without the file header
    assert image is not None and image.size == (7, 5)
    assert image.convert("RGB").getpixel((3, 2)) == (10, 200, 30)


def test_engine_prune_keeps_only_tools(isolated_home):
    from morphto import engines

    engine = engines.get("ffmpeg")
    engine.install_dir.mkdir(parents=True)
    for name in ("ffmpeg.exe", "ffprobe.exe", "ffplay.exe"):
        (engine.install_dir / name).write_bytes(b"x")
    engine.prune()
    assert sorted(p.name for p in engine.install_dir.iterdir()) == ["ffmpeg.exe"]


def test_engine_prefers_small_7z_download():
    from morphto import engines

    engine = engines.get("ffmpeg")
    assert engine.archives[0].kind == "7z"
    assert engine.size_mb == engine.archives[0].size_mb


def test_wheel_engine_picks_wheel_for_this_python():
    import sys

    from morphto import engines

    engine = engines.get("heif")
    tag = f"cp{sys.version_info.major}{sys.version_info.minor}"
    release = {
        "urls": [
            {"packagetype": "sdist", "filename": "pillow_heif-1.8.1.tar.gz", "url": "x", "digests": {"sha256": "0"}},
            {"packagetype": "bdist_wheel", "filename": f"pillow_heif-1.8.1-{tag}-{tag}t-win_amd64.whl",
             "url": "free-threaded", "digests": {"sha256": "1"}},
            {"packagetype": "bdist_wheel", "filename": f"pillow_heif-1.8.1-{tag}-{tag}-win_amd64.whl",
             "url": "right", "digests": {"sha256": "2"}, "size": 7},
        ]
    }  # fmt: skip
    assert engine.pick_wheel(release) == ("right", "2", 7)


def test_wheel_data_is_moved_next_to_the_package(tmp_path):
    from morphto.engines import _install_wheel_data

    (tmp_path / "pkg").mkdir()
    platlib = tmp_path / "pkg-1.0.data" / "platlib"
    platlib.mkdir(parents=True)
    (platlib / "lib.dll").write_bytes(b"x")
    _install_wheel_data(tmp_path)
    assert (tmp_path / "lib.dll").is_file() and not (tmp_path / "pkg-1.0.data").exists()


def test_heic_output_needs_the_encoder():
    from morphto.core import get_format
    from morphto.core.engine import plan, required_engines

    steps = plan(get_format("png"), get_format("heic"))
    assert [s.converter.engine for s in steps] == ["heif"]
    assert required_engines(steps) == ["heif"]  # not installed in the isolated test profile
