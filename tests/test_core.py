from pathlib import Path

import pytest

from morphto.core import ConversionError, Options, convert, get_format
from morphto.core.engine import output_path
from morphto.core.formats import Format
from morphto.core.registry import Context, Converter, Registry


class Fake(Converter):
    def __init__(self, name, pairs, cost=10):
        self.name, self._pairs, self.cost = name, pairs, cost

    def pairs(self):
        return self._pairs

    def convert(self, src: Path, dst: Path, src_fmt: Format, dst_fmt: Format, options: Options, ctx: Context):
        dst.write_bytes(src.read_bytes() + f">{dst_fmt.id}".encode())


def test_find_path_prefers_cheapest_chain():
    reg = Registry()
    reg.add(Fake("a", [("png", "jpg"), ("jpg", "webp")], cost=1))
    reg.add(Fake("b", [("png", "webp")], cost=5))
    assert [s.dst for s in reg.find_path("png", "webp")] == ["jpg", "webp"]
    reg.add(Fake("c", [("png", "webp")], cost=1))
    assert [s.converter.name for s in reg.find_path("png", "webp")] == ["c"]


def test_find_path_none_and_self():
    reg = Registry()
    reg.add(Fake("a", [("png", "jpg"), ("jpg", "jpg")]))
    assert reg.find_path("jpg", "png") is None
    assert reg.find_path("png", "png") is None
    assert len(reg.find_path("jpg", "jpg")) == 1


def test_targets_reachable_multi_hop():
    reg = Registry()
    reg.add(Fake("a", [("psd", "png"), ("png", "ico"), ("png", "psd")]))
    assert reg.targets("psd") == {"png", "ico"}


def test_multi_step_conversion(tmp_path):
    reg = Registry()
    reg.add(Fake("a", [("png", "jpg"), ("jpg", "webp")]))
    src = tmp_path / "x.png"
    src.write_bytes(b"\x89PNG\r\n\x1a\n")
    progress = []
    out = convert(src, "webp", registry=reg, progress=progress.append)
    assert out == tmp_path / "x.webp"
    assert out.read_bytes().endswith(b">jpg>webp")
    assert progress[-1] == 1.0
    assert progress == sorted(progress)


def test_output_never_overwrites(tmp_path):
    fmt = get_format("png")
    (tmp_path / "a.png").touch()
    (tmp_path / "a (1).png").touch()
    assert output_path("a", fmt, tmp_path) == tmp_path / "a (2).png"


def test_cross_category_multi_hop_is_refused(png):
    with pytest.raises(ConversionError):
        convert(png, "mp4")


def test_unknown_input(tmp_path):
    f = tmp_path / "notes.xyz"
    f.write_text("hello")
    with pytest.raises(ConversionError):
        convert(f, "png")


def test_options_merge():
    base = Options(quality=80, max_side=1920)
    assert base.merged(Options(quality=50)) == Options(quality=50, max_side=1920)
