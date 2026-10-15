import gzip
import tarfile
import unicodedata
import zipfile

import pytest

from morphto.converters.archives import _TAR
from morphto.converters.extra_images import svg_size
from morphto.core.detect import detect, sniff
from morphto.core.engine import convert, plan
from morphto.core.formats import get_format
from morphto.core.registry import ConversionError


@pytest.fixture
def tree(tmp_path):
    root = tmp_path / "src"
    (root / "sub").mkdir(parents=True)
    (root / "a.txt").write_text("hello", encoding="utf-8")
    (root / "sub" / "файл.txt").write_text("мир", encoding="utf-8")
    return root


@pytest.fixture
def zip_file(tmp_path, tree):
    path = tmp_path / "pack.zip"
    with zipfile.ZipFile(path, "w") as zf:
        for p in sorted(tree.rglob("*")):
            zf.write(p, p.relative_to(tree).as_posix())
    return path


def _listing(folder):
    # NFC: macOS file systems may hand back "й" decomposed
    return sorted(
        unicodedata.normalize("NFC", p.relative_to(folder).as_posix())
        for p in folder.rglob("*")
        if p.is_file()
    )


def test_unpack_zip_into_a_folder(tmp_path, zip_file):
    out = convert(zip_file, "folder", out_dir=tmp_path / "out")
    assert out.is_dir() and out.name == "pack"
    assert _listing(out) == ["a.txt", "sub/файл.txt"]
    assert (out / "sub" / "файл.txt").read_text(encoding="utf-8") == "мир"


@pytest.mark.parametrize("fmt", ["tar", "tgz"] + (["7z"] if _TAR.is_file() else []))
def test_repack_round_trip(tmp_path, zip_file, fmt):
    packed = convert(zip_file, fmt, out_dir=tmp_path / "p")
    assert detect(packed).id == fmt
    out = convert(packed, "folder", out_dir=tmp_path / "u")
    assert _listing(out) == ["a.txt", "sub/файл.txt"]


def test_tar_cannot_escape_the_target_folder(tmp_path):
    evil = tmp_path / "evil.tar"
    payload = tmp_path / "x.txt"
    payload.write_text("x")
    with tarfile.open(evil, "w") as tf:
        tf.add(payload, "../escaped.txt")
    with pytest.raises(ConversionError):
        convert(evil, "folder", out_dir=tmp_path / "out")
    assert not (tmp_path / "escaped.txt").exists()


def test_single_gz_file(tmp_path):
    path = tmp_path / "server.log.gz"
    with gzip.open(path, "wb") as f:
        f.write(b"line 1\n")
    out = convert(path, "folder", out_dir=tmp_path / "out")
    assert (out / "server.log").read_bytes() == b"line 1\n"


def test_archive_signatures():
    assert sniff(b"7z\xbc\xaf\x27\x1c\x00\x04") == "7z"
    assert sniff(b"Rar!\x1a\x07\x01\x00") == "rar"
    assert sniff(b"\0" * 257 + b"ustar\x0000") == "tar"


def test_unpack_only_from_archives():
    with pytest.raises(ConversionError):
        plan(get_format("png"), get_format("folder"))


# ---- SVG / RAW / JPEG XL ----


@pytest.mark.parametrize(
    ("attrs", "size"),
    [
        ('width="24" height="24"', (24, 24)),
        ('width="2in" height="1in"', (192, 96)),
        ('viewBox="0 0 300 150"', (300, 150)),
        ('width="600" viewBox="0 0 300 150"', (600, 300)),
        ('width="100%" height="100%"', None),
    ],
)
def test_svg_size(tmp_path, attrs, size):
    path = tmp_path / "a.svg"
    path.write_text(f'<svg xmlns="http://www.w3.org/2000/svg" {attrs}/>', encoding="utf-8")
    assert svg_size(path) == size


def test_new_image_formats_are_detected(tmp_path):
    svg = tmp_path / "logo.dat"  # the content decides
    svg.write_text('<?xml version="1.0"?>\n<svg xmlns="http://www.w3.org/2000/svg"/>', encoding="utf-8")
    assert detect(svg).id == "svg"
    assert sniff(b"\xff\x0a\xfa") == "jxl"
    assert sniff(b"II*\x00\x08\x00", "nef") == "raw"
    assert sniff(b"II*\x00\x08\x00", "tif") == "tiff"


def test_engines_are_planned_for():
    assert [s.converter.engine for s in plan(get_format("svg"), get_format("ico"))] == ["resvg", None]
    assert [s.converter.engine for s in plan(get_format("raw"), get_format("jpg"))] == ["libraw", None]
    assert [s.converter.engine for s in plan(get_format("png"), get_format("jxl"))] == ["jxl"]


def test_m4r_ringtone_is_offered():
    from morphto.core.targets import available_targets

    assert "m4r" in {t.id for t in available_targets([get_format("mp3")])}
