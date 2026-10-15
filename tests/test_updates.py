import hashlib
import threading

import pytest

from morphto import engines, updates


@pytest.mark.parametrize(
    ("candidate", "current", "newer"),
    [
        ("v0.2.0", "0.1.0", True),
        ("0.10.0", "0.9.3", True),  # numeric, not alphabetical
        ("0.2.0", "0.2.0", False),
        ("v0.1.9", "0.2.0", False),
        ("1.0", "0.9.9", True),
        ("garbage", "0.1.0", False),
    ],
)
def test_is_newer(candidate, current, newer):
    assert updates.is_newer(candidate, current) is newer


def _release_json(version="0.3.0"):
    base = f"https://github.com/rearzty/MorphTo/releases/download/v{version}"
    return {
        "tag_name": f"v{version}",
        "html_url": f"https://github.com/rearzty/MorphTo/releases/tag/v{version}",
        "assets": [
            {"name": name, "browser_download_url": f"{base}/{name}"}
            for name in ("MorphTo-setup.exe", updates.package_name(version), "SHA256SUMS.txt")
        ],
    }


def test_parse_release_picks_versioned_installer():
    release = updates.parse_release(_release_json())
    assert release.version == "0.3.0"
    assert release.setup_name == updates.package_name("0.3.0")
    assert release.setup_url.endswith(f"/v0.3.0/{updates.package_name('0.3.0')}")
    assert release.sums_url.endswith("/SHA256SUMS.txt")


def _fake_network(monkeypatch, payload: bytes, listed_hash: str):
    def fetch(url):
        return f"{listed_hash}  {updates.package_name('0.3.0')}\nabc  MorphTo-0.3.0-portable.zip\n"

    def download(url, dest, progress, cancel):
        dest.write_bytes(payload)
        return hashlib.sha256(payload).hexdigest()

    monkeypatch.setattr(engines, "_fetch_text", fetch)
    monkeypatch.setattr(engines, "_download", download)


def test_download_installer_verifies_checksum(monkeypatch):
    payload = b"installer bytes"
    _fake_network(monkeypatch, payload, hashlib.sha256(payload).hexdigest())
    path = updates.download_installer(
        updates.parse_release(_release_json()), lambda f: None, threading.Event()
    )
    assert path.name == updates.package_name("0.3.0") and path.read_bytes() == payload


def test_download_installer_rejects_bad_checksum(monkeypatch):
    _fake_network(monkeypatch, b"tampered", "0" * 64)
    with pytest.raises(updates.UpdateError):
        updates.download_installer(updates.parse_release(_release_json()), lambda f: None, threading.Event())
    assert not list((updates.data_dir() / "updates").glob("*.exe"))


def test_source_checkout_cannot_self_update():
    assert updates.installed_app_dir() is None
