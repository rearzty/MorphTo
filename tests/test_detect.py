import pytest

from morphto.core.detect import detect, sniff


@pytest.mark.parametrize(
    ("head", "ext", "expected"),
    [
        (b"\x89PNG\r\n\x1a\n" + b"\0" * 8, "", "png"),
        (b"\xff\xd8\xff\xe0", "", "jpg"),
        (b"GIF89a", "", "gif"),
        (b"RIFF\0\0\0\0WEBPVP8 ", "", "webp"),
        (b"RIFF\0\0\0\0WAVEfmt ", "", "wav"),
        (b"RIFF\0\0\0\0AVI LIST", "", "avi"),
        (b"\x00\x00\x00\x1cftypheic\x00\x00\x00\x00mif1heic", "", "heic"),
        (b"\x00\x00\x00\x1cftypavif\x00\x00\x00\x00avifmif1", "", "avif"),
        (b"\x00\x00\x00\x1cftypmif1\x00\x00\x00\x00mif1avif", "", "avif"),
        (b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2", "", "mp4"),
        (b"\x00\x00\x00\x14ftypqt  \x00\x00\x02\x00qt  ", "", "mov"),
        (b"\x00\x00\x00\x18ftypM4A \x00\x00\x02\x00M4A mp42", "", "m4a"),
        (b"\x1a\x45\xdf\xa3\x9f\x42\x86\x81\x01\x42\x82\x84webm", "", "webm"),
        (b"\x1a\x45\xdf\xa3\x9f\x42\x86\x81\x01\x42\x82\x88matroska", "", "mkv"),
        (b"OggS\0\x02" + b"\0" * 22 + b"OpusHead", "", "opus"),
        (b"OggS\0\x02" + b"\0" * 22 + b"\x01vorbis", "", "ogg"),
        (b"fLaC\0\0\0\x22", "", "flac"),
        (b"ID3\x04\0\0", "mp3", "mp3"),
        (b"\xff\xfb\x90\x64", "", "mp3"),
        (b"\xff\xf1\x50\x80", "", "aac"),
        (b"%PDF-1.7", "", "pdf"),
        (b"8BPS\0\x01", "", "psd"),
        (b"\x00\x00\x01\x00\x01\x00", "ico", "ico"),
        (b"II*\x00", "", "tiff"),
        (b"just some text", "", None),
    ],
)
def test_sniff(head, ext, expected):
    assert sniff(head, ext) == expected


def test_detect_trusts_content_over_extension(tmp_path, png):
    disguised = tmp_path / "photo.jpg"
    disguised.write_bytes(png.read_bytes())
    assert detect(disguised).id == "png"


def test_detect_falls_back_to_extension(tmp_path):
    f = tmp_path / "image.tga"
    f.write_bytes(b"\0\0\x02\0\0\0\0\0")
    assert detect(f).id == "tga"


def test_typescript_is_not_video(tmp_path):
    f = tmp_path / "index.ts"
    f.write_text("export const x = 1;\n")
    assert detect(f) is None


def test_detect_missing_file(tmp_path):
    assert detect(tmp_path / "nope.png") is None
