from pathlib import Path

import pytest
from PIL import Image

from morphto.converters.images import WRITE
from morphto.core import Options, convert
from morphto.core.detect import detect


@pytest.mark.parametrize("target", sorted(WRITE))
def test_png_to_every_format(png, target):
    out = convert(png, target)
    assert out.is_file() and out.stat().st_size > 0
    if target != "pdf":
        with Image.open(out) as im:
            assert im.size[0] > 0
    assert detect(out).id == target


def test_jpg_has_no_alpha_and_white_background(png):
    out = convert(png, "jpg")
    with Image.open(out) as im:
        assert im.mode == "RGB"
        r, g, b = im.getpixel((0, 0))
        assert min(r, g, b) > 200  # transparent pixel became white, not black


def test_animation_is_kept(animated_gif):
    out = convert(animated_gif, "webp")
    with Image.open(out) as im:
        assert im.n_frames == 5


def test_max_side_and_quality(png):
    big = convert(png, "jpg", options=Options(quality=95))
    small = convert(png, "jpg", options=Options(quality=30, max_side=100))
    with Image.open(small) as im:
        assert max(im.size) == 100
    assert small.stat().st_size < big.stat().st_size


def test_ico_contains_multiple_sizes(tmp_path):
    src = tmp_path / "logo.png"
    Image.new("RGBA", (300, 200), (10, 20, 30, 255)).save(src)
    out = convert(src, "ico")
    with Image.open(out) as im:
        sizes = im.info["sizes"]
    assert (256, 256) in sizes and (16, 16) in sizes


def test_heic_read(tmp_path):
    src = Path(__file__).parent / "data" / "sample.heic"
    assert detect(src).id == "heic"
    out = convert(src, "jpg", out_dir=tmp_path)
    with Image.open(out) as im:
        assert im.size == (64, 40)


def test_exif_orientation_applied(tmp_path):
    src = tmp_path / "rotated.jpg"
    im = Image.new("RGB", (40, 20), "red")
    exif = Image.Exif()
    exif[0x0112] = 6  # rotate 90° CW
    im.save(src, exif=exif.tobytes())
    out = convert(src, "png")
    with Image.open(out) as res:
        assert res.size == (20, 40)
