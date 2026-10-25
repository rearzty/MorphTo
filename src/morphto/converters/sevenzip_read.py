"""A 7z reader for what 7-Zip writes by default: LZMA / LZMA2 with BCJ or Delta filters, solid blocks,
compressed headers. Names are UTF-16 in the archive and written as such, so they survive any Windows
language (Windows' tar.exe loses non-Latin names when the system code page cannot represent them).
PPMd, BCJ2 and encrypted archives raise Unsupported / PasswordProtected."""

from __future__ import annotations

import lzma
import struct
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO

_CHUNK = 1 << 20
_END, _HEADER, _ARCHIVE_PROPERTIES, _ADDITIONAL_STREAMS, _MAIN_STREAMS, _FILES = (
    0x00,
    0x01,
    0x02,
    0x03,
    0x04,
    0x05,
)
_PACK_INFO, _UNPACK_INFO, _SUBSTREAMS, _SIZE, _CRC, _FOLDER = 0x06, 0x07, 0x08, 0x09, 0x0A, 0x0B
_CODERS_UNPACK_SIZE, _NUM_UNPACK_STREAM, _EMPTY_STREAM, _EMPTY_FILE = 0x0C, 0x0D, 0x0E, 0x0F
_NAME, _ATTRIBUTES, _ENCODED_HEADER = 0x11, 0x15, 0x17
_ATTR_DIR = 0x10

_COPY, _LZMA, _LZMA2, _DELTA, _AES = (
    b"\x00",
    bytes.fromhex("030101"),
    b"\x21",
    b"\x03",
    bytes.fromhex("06f10701"),
)
_BRANCH = {
    bytes.fromhex("03030103"): lzma.FILTER_X86,
    bytes.fromhex("03030205"): lzma.FILTER_POWERPC,
    bytes.fromhex("03030401"): lzma.FILTER_IA64,
    bytes.fromhex("03030501"): lzma.FILTER_ARM,
    bytes.fromhex("03030701"): lzma.FILTER_ARMTHUMB,
    bytes.fromhex("03030805"): lzma.FILTER_SPARC,
}


class Unsupported(Exception):
    """Something only 7-Zip itself handles; the caller can fall back to another tool."""


class PasswordProtected(Exception):
    pass


class _Reader:
    def __init__(self, data: bytes) -> None:
        self.data, self.pos = data, 0

    def byte(self) -> int:
        return self.take(1)[0]

    def take(self, n: int) -> bytes:
        if self.pos + n > len(self.data):
            raise Unsupported("truncated header")
        self.pos += n
        return self.data[self.pos - n : self.pos]

    def number(self) -> int:
        first = self.byte()
        mask, extra = 0x80, 0
        while extra < 8 and first & mask:
            extra += 1
            mask >>= 1
        value = int.from_bytes(self.take(extra), "little") if extra else 0
        if extra < 8:
            value |= (first & (mask - 1)) << (8 * extra)
        return value

    def bits(self, n: int) -> list[bool]:
        raw = self.take((n + 7) // 8)
        return [bool(raw[i // 8] & (0x80 >> (i % 8))) for i in range(n)]

    def defined(self, n: int) -> list[bool]:
        return [True] * n if self.byte() else self.bits(n)


class _Folder:
    def __init__(self) -> None:
        self.coders: list[tuple[bytes, int, int, bytes]] = []  # id, inputs, outputs, properties
        self.bind_pairs: list[tuple[int, int]] = []  # (input index, output index)
        self.pack_index = 0  # first packed stream of this folder
        self.unpack_sizes: list[int] = []
        self.substreams = 1
        self.sizes: list[int] = []  # of the substreams

    @property
    def size(self) -> int:
        bound = {out for _inp, out in self.bind_pairs}
        return next(s for i, s in enumerate(self.unpack_sizes) if i not in bound)

    def filters(self) -> list[dict] | None:
        """The liblzma raw filter chain (None for stored data). Only one-in/one-out coders."""
        if any(ins != 1 or outs != 1 for _cid, ins, outs, _p in self.coders):
            raise Unsupported("complex coders (BCJ2)")
        if len(self.coders) == 1 and self.coders[0][0] == _COPY:
            return None
        # With one input and output per coder, coder i has input i and output i. Start from the coder whose
        # output is the folder's result, then follow which coder feeds its input.
        bound_outputs = {out for _inp, out in self.bind_pairs}
        feeds = {inp: out for inp, out in self.bind_pairs}
        coder = next(i for i in range(len(self.coders)) if i not in bound_outputs)
        chain = []
        while True:
            chain.append(self._filter(*self.coders[coder]))
            if coder not in feeds:
                break
            coder = feeds[coder]
        return chain

    @staticmethod
    def _filter(cid: bytes, _ins: int, _outs: int, props: bytes) -> dict:
        if cid == _AES:
            raise PasswordProtected
        if cid == _LZMA2:
            p = props[0] if props else 0
            return {
                "id": lzma.FILTER_LZMA2,
                "dict_size": (2 | (p & 1)) << (p // 2 + 11) if p < 40 else 0xFFFFFFFF,
            }
        if cid == _LZMA:
            lclppb, dict_size = props[0], int.from_bytes(props[1:5], "little")
            return {
                "id": lzma.FILTER_LZMA1, "dict_size": dict_size,
                "lc": lclppb % 9, "lp": lclppb // 9 % 5, "pb": lclppb // 45,
            }  # fmt: skip
        if cid == _DELTA:
            return {"id": lzma.FILTER_DELTA, "dist": (props[0] + 1) if props else 1}
        if cid in _BRANCH:
            return {"id": _BRANCH[cid]}
        raise Unsupported(f"compression method {cid.hex()}")


def _skip_digests(r: _Reader, count: int) -> None:
    r.take(4 * sum(r.defined(count)))


def _streams_info(r: _Reader) -> tuple[int, list[int], list[_Folder]]:
    pack_pos, pack_sizes, folders = 0, [], []
    while (prop := r.byte()) != _END:
        if prop == _PACK_INFO:
            pack_pos, count = r.number(), r.number()
            while (sub := r.byte()) != _END:
                if sub == _SIZE:
                    pack_sizes = [r.number() for _ in range(count)]
                elif sub == _CRC:
                    _skip_digests(r, count)
        elif prop == _UNPACK_INFO:
            if r.byte() != _FOLDER:
                raise Unsupported("bad folder info")
            folders = [_Folder() for _ in range(r.number())]
            if r.byte():
                raise Unsupported("external folder info")
            next_pack = 0
            for f in folders:
                total_in = total_out = 0
                for _ in range(r.number()):
                    flag = r.byte()
                    cid = r.take(flag & 0x0F)
                    ins, outs = (r.number(), r.number()) if flag & 0x10 else (1, 1)
                    props = r.take(r.number()) if flag & 0x20 else b""
                    f.coders.append((cid, ins, outs, props))
                    total_in, total_out = total_in + ins, total_out + outs
                f.bind_pairs = [(r.number(), r.number()) for _ in range(total_out - 1)]
                packed = total_in - len(f.bind_pairs)
                if packed > 1:
                    [r.number() for _ in range(packed)]
                f.pack_index = next_pack
                next_pack += packed
            if r.byte() != _CODERS_UNPACK_SIZE:
                raise Unsupported("bad folder info")
            for f in folders:
                f.unpack_sizes = [r.number() for _ in range(sum(c[2] for c in f.coders))]
            while (sub := r.byte()) != _END:
                if sub == _CRC:
                    _skip_digests(r, len(folders))
        elif prop == _SUBSTREAMS:
            sub = r.byte()
            if sub == _NUM_UNPACK_STREAM:
                for f in folders:
                    f.substreams = r.number()
                sub = r.byte()
            explicit: list[list[int]] = [[] for _ in folders]
            if sub == _SIZE:
                for i, f in enumerate(folders):
                    explicit[i] = [r.number() for _ in range(max(0, f.substreams - 1))]
                sub = r.byte()
            for f, sizes in zip(folders, explicit, strict=True):
                f.sizes = [*sizes, f.size - sum(sizes)] if f.substreams else []
            while sub != _END:
                if sub != _CRC:
                    raise Unsupported("bad substream info")
                _skip_digests(r, sum(f.substreams for f in folders))
                sub = r.byte()
        else:
            raise Unsupported(f"stream property {prop}")
    for f in folders:
        if not f.sizes and f.substreams:
            f.sizes = [f.size]
    return pack_pos, pack_sizes, folders


def _decode(src: BinaryIO, start: int, pack_sizes: list[int], folder: _Folder, check_cancelled):
    """Yield the folder's unpacked bytes in chunks."""
    src.seek(start + sum(pack_sizes[: folder.pack_index]))
    left_packed = pack_sizes[folder.pack_index]
    left = folder.size
    chain = folder.filters()
    if chain is None:
        while left > 0:
            check_cancelled()
            data = src.read(min(_CHUNK, left))
            if not data:
                raise Unsupported("the archive ended early")
            left -= len(data)
            yield data
        return
    decomp = lzma.LZMADecompressor(format=lzma.FORMAT_RAW, filters=chain)
    while left > 0:
        check_cancelled()
        chunk = b""
        if decomp.needs_input:
            chunk = src.read(min(_CHUNK, left_packed)) if left_packed > 0 else b""
            left_packed -= len(chunk)
            if not chunk and decomp.needs_input:
                raise Unsupported("the archive ended early")
        data = decomp.decompress(chunk, max_length=min(_CHUNK, left))
        if data:
            left -= len(data)
            yield data
        elif decomp.eof:
            raise Unsupported("the archive ended early")


def _safe_name(name: str) -> str | None:
    parts = [p for p in name.replace("\\", "/").split("/") if p not in ("", ".", "..")]
    if not parts or ":" in parts[0]:
        return None
    return "/".join(parts)


def read_7z(src_path: Path, dest: Path, check_cancelled: Callable[[], None] = lambda: None) -> None:
    """Extract ``src_path`` into ``dest``."""
    with src_path.open("rb") as src:
        head = src.read(32)
        if head[:6] != b"7z\xbc\xaf\x27\x1c":
            raise Unsupported("not a 7z archive")
        next_offset, next_size, _crc = struct.unpack("<QQI", head[12:32])
        src.seek(32 + next_offset)
        r = _Reader(src.read(next_size))
        kind = r.byte()
        while kind == _ENCODED_HEADER:  # the header itself is compressed
            pack_pos, pack_sizes, folders = _streams_info(r)
            data = b"".join(_decode(src, 32 + pack_pos, pack_sizes, folders[0], check_cancelled))
            r = _Reader(data)
            kind = r.byte()
        if kind != _HEADER:
            raise Unsupported("bad header")

        pack_pos, pack_sizes, folders = 0, [], []
        names: list[str] = []
        empty_stream: list[bool] = []
        empty_file: list[bool] = []
        attributes: list[int | None] = []
        while (prop := r.byte()) != _END:
            if prop == _ARCHIVE_PROPERTIES:
                while r.byte() != _END:
                    r.take(r.number())
            elif prop == _ADDITIONAL_STREAMS:
                _streams_info(r)
            elif prop == _MAIN_STREAMS:
                pack_pos, pack_sizes, folders = _streams_info(r)
            elif prop == _FILES:
                count = r.number()
                empty_stream = [False] * count
                attributes = [None] * count
                while (ftype := r.byte()) != _END:
                    body = _Reader(r.take(r.number()))
                    if ftype == _EMPTY_STREAM:
                        empty_stream = body.bits(count)
                    elif ftype == _EMPTY_FILE:
                        empty_file = body.bits(sum(empty_stream))
                    elif ftype == _NAME:
                        if body.byte():
                            raise Unsupported("external names")
                        names = body.data[1:].decode("utf-16-le").split("\0")[:count]
                    elif ftype == _ATTRIBUTES:
                        flags = body.defined(count)
                        if body.byte():
                            raise Unsupported("external attributes")
                        attributes = [struct.unpack("<I", body.take(4))[0] if d else None for d in flags]
            else:
                raise Unsupported(f"header property {prop}")

        # Files with data take the substreams in order; the others are folders or empty files.
        targets: list[Path | None] = []
        empty_index = 0
        for i, raw in enumerate(names):
            name = _safe_name(raw)
            if empty_stream[i]:
                is_file = empty_file[empty_index] if empty_index < len(empty_file) else False
                empty_index += 1
                if name is None:
                    continue
                if is_file and not ((attributes[i] or 0) & _ATTR_DIR):
                    (dest / name).parent.mkdir(parents=True, exist_ok=True)
                    (dest / name).write_bytes(b"")
                else:
                    (dest / name).mkdir(parents=True, exist_ok=True)
            else:
                attr = attributes[i] or 0
                # Unix symlinks (mode in the high 16 bits) hold the link target as data: skip, like tar does
                # on Windows, rather than create a file with a path in it.
                symlink = attr & 0x8000 and (attr >> 16) & 0o170000 == 0o120000
                targets.append(dest / name if name is not None and not symlink else None)

        start = 32 + pack_pos
        stream = 0
        for folder in folders:
            data = _decode(src, start, pack_sizes, folder, check_cancelled)
            buffer = b""
            for size in folder.sizes:
                path = targets[stream] if stream < len(targets) else None
                stream += 1
                out = None
                if path is not None:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    out = path.open("wb")
                try:
                    left = size
                    while left > 0:
                        if not buffer:
                            buffer = next(data, b"")
                            if not buffer:
                                raise Unsupported("the archive ended early")
                        piece, buffer = buffer[:left], buffer[left:]
                        left -= len(piece)
                        if out is not None:
                            out.write(piece)
                finally:
                    if out is not None:
                        out.close()
