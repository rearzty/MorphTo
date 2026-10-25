"""A small 7z writer: one solid LZMA2 stream, names, folders and CRCs. Python's own lzma module does the
compression, so creating .7z needs no external program (Windows' tar.exe crashes on it on some builds)."""

from __future__ import annotations

import lzma
import struct
import zlib
from collections.abc import Callable
from pathlib import Path

_SIGNATURE = b"7z\xbc\xaf\x27\x1c\x00\x04"
_CHUNK = 1 << 20

# Property IDs
_END, _HEADER, _MAIN_STREAMS, _FILES, _PACK_INFO, _UNPACK_INFO, _SUBSTREAMS = (
    0x00,
    0x01,
    0x04,
    0x05,
    0x06,
    0x07,
    0x08,
)
_SIZE, _CRC, _FOLDER, _CODERS_UNPACK_SIZE, _NUM_UNPACK_STREAM = 0x09, 0x0A, 0x0B, 0x0C, 0x0D
_EMPTY_STREAM, _EMPTY_FILE, _NAME, _ATTRIBUTES = 0x0E, 0x0F, 0x11, 0x15
_LZMA2_ID = 0x21
_ATTR_DIR, _ATTR_FILE = 0x10, 0x20


def _number(n: int) -> bytes:
    """7z variable-length integer: leading 1-bits of the first byte count the extra bytes."""
    for extra in range(8):
        if n < (1 << (7 * (extra + 1))):
            high = n >> (8 * extra)
            first = (0xFF << (8 - extra)) & 0xFF | high
            return bytes([first]) + n.to_bytes(8, "little")[:extra]
    return b"\xff" + n.to_bytes(8, "little")


def _bits(flags: list[bool]) -> bytes:
    out = bytearray((len(flags) + 7) // 8)
    for i, flag in enumerate(flags):
        if flag:
            out[i // 8] |= 0x80 >> (i % 8)
    return bytes(out)


def _lzma2_prop(dict_size: int) -> int:
    for p in range(40):
        if (2 | (p & 1)) << (p // 2 + 11) >= dict_size:
            return p
    return 40


def write_7z(folder: Path, dst: Path, check_cancelled: Callable[[], None] = lambda: None) -> None:
    """Pack everything inside ``folder`` into ``dst`` (paths relative to ``folder``)."""
    entries = sorted(folder.rglob("*"), key=lambda p: p.relative_to(folder).as_posix())
    names = [p.relative_to(folder).as_posix() for p in entries]
    is_dir = [p.is_dir() for p in entries]
    sizes = [0 if d else p.stat().st_size for p, d in zip(entries, is_dir, strict=True)]
    empty = [d or s == 0 for d, s in zip(is_dir, sizes, strict=True)]
    streams = [i for i, e in enumerate(empty) if not e]

    dict_size = 1 << 23  # preset 6: 8 MiB
    filters = [{"id": lzma.FILTER_LZMA2, "dict_size": dict_size, "preset": 6}]
    compressor = lzma.LZMACompressor(format=lzma.FORMAT_RAW, filters=filters)
    crcs: list[int] = []
    packed = 0
    with dst.open("wb") as out:
        out.write(b"\0" * 32)  # signature header, written at the end
        for i in streams:
            crc = 0
            with entries[i].open("rb") as f:
                while chunk := f.read(_CHUNK):
                    check_cancelled()
                    crc = zlib.crc32(chunk, crc)
                    data = compressor.compress(chunk)
                    out.write(data)
                    packed += len(data)
            crcs.append(crc)
        if streams:
            data = compressor.flush()
            out.write(data)
            packed += len(data)

        header = bytearray([_HEADER])
        if streams:
            unpacked = sum(sizes[i] for i in streams)
            header += bytes([_MAIN_STREAMS])
            header += (
                bytes([_PACK_INFO])
                + _number(0)
                + _number(1)
                + bytes([_SIZE])
                + _number(packed)
                + bytes([_END])
            )
            header += bytes([_UNPACK_INFO, _FOLDER]) + _number(1) + b"\0"  # one folder, not external
            header += b"\x01" + bytes([0x21, _LZMA2_ID]) + _number(1) + bytes([_lzma2_prop(dict_size)])
            header += bytes([_CODERS_UNPACK_SIZE]) + _number(unpacked) + bytes([_END])
            header += bytes([_SUBSTREAMS, _NUM_UNPACK_STREAM]) + _number(len(streams))
            if len(streams) > 1:
                header += bytes([_SIZE]) + b"".join(_number(sizes[i]) for i in streams[:-1])
            header += bytes([_CRC, 1]) + b"".join(struct.pack("<I", c) for c in crcs)
            header += bytes([_END, _END])
        header += bytes([_FILES]) + _number(len(entries))
        if any(empty):
            prop = _bits(empty)
            header += bytes([_EMPTY_STREAM]) + _number(len(prop)) + prop
            empty_files = [not is_dir[i] for i, e in enumerate(empty) if e]
            if any(empty_files):
                prop = _bits(empty_files)
                header += bytes([_EMPTY_FILE]) + _number(len(prop)) + prop
        name_data = b"\0" + b"".join(n.encode("utf-16-le") + b"\0\0" for n in names)
        header += bytes([_NAME]) + _number(len(name_data)) + name_data
        attrs = b"\x01\0" + b"".join(struct.pack("<I", _ATTR_DIR if d else _ATTR_FILE) for d in is_dir)
        header += bytes([_ATTRIBUTES]) + _number(len(attrs)) + attrs
        header += bytes([_END, _END])

        out.write(header)
        start = struct.pack("<QQI", packed, len(header), zlib.crc32(header))
        out.seek(0)
        out.write(_SIGNATURE + struct.pack("<I", zlib.crc32(start)) + start)
