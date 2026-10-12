import json

from morphto.core import get_format
from morphto.core.targets import available_targets, find_target, rank
from morphto.i18n import LANGUAGES, LOCALES_DIR
from morphto.settings import Settings


def ids(targets):
    return [t.id for t in targets]


def test_png_targets_exclude_self_and_video():
    targets = ids(available_targets([get_format("png")]))
    assert "jpg" in targets and "pdf" in targets and "preset:web" in targets
    assert "png" not in targets and "mp4" not in targets


def test_video_targets_include_audio_and_gif_but_not_png():
    targets = ids(available_targets([get_format("mp4")]))
    assert {"mkv", "gif", "mp3", "preset:discord"} <= set(targets)
    assert "png" not in targets


def test_common_targets_for_mixed_input():
    targets = ids(available_targets([get_format("png"), get_format("mp4")]))
    assert targets == ["gif"]  # PNG -> GIF (Pillow) and MP4 -> GIF (FFmpeg)
    targets = ids(available_targets([get_format("png"), get_format("heic")]))
    assert "jpg" in targets and "png" not in targets and "heic" not in targets


def test_rank_defaults_and_history():
    heic = get_format("heic")
    targets = available_targets([heic])
    assert ids(rank(targets, heic))[:2] == ["jpg", "png"]
    ranked = ids(rank(targets, heic, {"webp": 3, "png": 1}))
    assert ranked[:3] == ["webp", "png", "jpg"]


def test_find_target():
    assert find_target(".PNG").id == "png"
    assert find_target("jpeg").id == "jpg"
    assert find_target("discord").preset == "discord"
    assert find_target("preset:web").format.id == "webp"


def test_settings_roundtrip():
    s = Settings.load()
    s.record_use("png", "jpg")
    s.record_use("png", "jpg")
    s.save()
    assert Settings.load().history == {"png": {"jpg": 2}}


def test_translations_complete():
    en = json.loads((LOCALES_DIR / "en.json").read_text(encoding="utf-8"))
    for code in LANGUAGES:
        data = json.loads((LOCALES_DIR / f"{code}.json").read_text(encoding="utf-8"))
        assert data.keys() == en.keys(), code
        for key, text in en.items():
            # every {placeholder} must survive translation
            for part in text.split("{")[1:]:
                assert "{" + part.split("}")[0] + "}" in data[key], (code, key)
