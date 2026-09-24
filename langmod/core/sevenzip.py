"""Reading ``.7z`` archives, as far as language mod packages need, with nothing to install.

The layout follows 7-Zip's own description of the format (``7zFormat.txt``):
a 32-byte signature header points at the archive's header, at the end of
the file, which is usually packed itself (an "encoded header"). The header
lists *folders* - runs of data packed as one, each through a chain of
coders - and the files, which are cut out of the folders' unpacked data one
after another. A solid archive has one folder for everything.

What unpacks: LZMA2 (7-Zip's default), LZMA, BZip2, Deflate, Zstandard (the
7-Zip ZS fork), stored files, and the BCJ and Delta filters in front of
LZMA or LZMA2, all through Python's own ``lzma``, ``bz2``, ``zlib`` and
``compression.zstd``. PPMd, BCJ2, Deflate64 and encrypted archives are
recognised and refused with a message that says so. Every file is checked
against the CRC the archive keeps for it.
"""
from __future__ import annotations

import bz2
import lzma
import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path

from ..i18n import tr

SIGNATURE = b"7z\xbc\xaf\x27\x1c"
MAX_UNPACKED = 1024 * 1024 * 1024        # a folder that unpacks to more than this is not a mod
MAX_COUNT = 1_000_000                    # files, folders or streams: more is a damaged header
FILETIME_EPOCH = 11644473600             # seconds from 1601 to 1970

# Property IDs in the header.
END, HEADER, ARCHIVE_PROPERTIES, ADDITIONAL_STREAMS, MAIN_STREAMS, FILES_INFO = 0x00, 0x01, 0x02, 0x03, 0x04, 0x05
PACK_INFO, UNPACK_INFO, SUBSTREAMS_INFO, SIZE, CRC, FOLDER = 0x06, 0x07, 0x08, 0x09, 0x0A, 0x0B
CODERS_UNPACK_SIZE, NUM_UNPACK_STREAM, EMPTY_STREAM, EMPTY_FILE = 0x0C, 0x0D, 0x0E, 0x0F
NAME, MTIME, WIN_ATTRIBUTES, ENCODED_HEADER = 0x11, 0x14, 0x15, 0x17

COPY, LZMA, LZMA2 = b"\x00", b"\x03\x01\x01", b"\x21"
DEFLATE, DEFLATE64, BZIP2, ZSTD = b"\x04\x01\x08", b"\x04\x01\x09", b"\x04\x02\x02", b"\x04\xf7\x11\x01"
PPMD, BCJ2, AES = b"\x03\x04\x01", b"\x03\x03\x01\x1b", b"\x06\xf1\x07\x01"
DELTA = b"\x03"
#: Filters that stand in front of LZMA or LZMA2, with the lzma module's name for each.
BRANCH = {b"\x03\x03\x01\x03": "FILTER_X86", b"\x03\x03\x02\x05": "FILTER_POWERPC",
          b"\x03\x03\x04\x01": "FILTER_IA64", b"\x03\x03\x05\x01": "FILTER_ARM",
          b"\x03\x03\x07\x01": "FILTER_ARMTHUMB", b"\x03\x03\x08\x05": "FILTER_SPARC",
          b"\x04": "FILTER_X86", b"\x05": "FILTER_POWERPC", b"\x06": "FILTER_IA64", b"\x07": "FILTER_ARM",
          b"\x08": "FILTER_ARMTHUMB", b"\x09": "FILTER_SPARC", b"\x0a": "FILTER_ARM64", b"\x0b": "FILTER_RISCV"}
REFUSED = {PPMD: "PPMd", BCJ2: "BCJ2", DEFLATE64: "Deflate64"}
DIRECTORY_ATTRIBUTE = 0x10


class SevenZipError(Exception):
    """Not a 7z archive, a damaged one, or one packed in a way this reader does not unpack."""


class _Buf:
    """The header's bytes, read front to back."""

    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def byte(self) -> int:
        if self.pos >= len(self.data):
            raise SevenZipError(tr("the archive's header ends too soon: it is damaged"))
        self.pos += 1
        return self.data[self.pos - 1]

    def take(self, n: int) -> bytes:
        if n < 0 or self.pos + n > len(self.data):
            raise SevenZipError(tr("the archive's header ends too soon: it is damaged"))
        self.pos += n
        return self.data[self.pos - n:self.pos]

    def number(self) -> int:
        """7z's own variable-length number: the high bits of the first byte say how many follow."""
        first = self.byte()
        mask = 0x80
        value = 0
        for i in range(8):
            if not first & mask:
                return value | ((first & (mask - 1)) << (8 * i))
            value |= self.byte() << (8 * i)
            mask >>= 1
        return value

    def count(self, limit: int = MAX_COUNT) -> int:
        n = self.number()
        if n > limit:
            raise SevenZipError(tr("the archive's header is damaged"))
        return n

    def uint32(self) -> int:
        return struct.unpack("<I", self.take(4))[0]

    def uint64(self) -> int:
        return struct.unpack("<Q", self.take(8))[0]

    def bits(self, n: int) -> list[bool]:
        out, mask, b = [], 0, 0
        for _ in range(n):
            if not mask:
                b, mask = self.byte(), 0x80
            out.append(bool(b & mask))
            mask >>= 1
        return out

    def defined(self, n: int) -> list[bool]:
        """A byte saying all are defined, or a bit for each."""
        return [True] * n if self.byte() else self.bits(n)


@dataclass
class _Coder:
    method: bytes
    ins: int
    outs: int
    props: bytes


@dataclass
class _Folder:
    coders: list[_Coder]
    binds: list[tuple[int, int]]        # (in stream, out stream): that out stream feeds that in stream
    packed: list[int]                   # the in streams fed straight from the archive
    sizes: list[int] = field(default_factory=list)      # unpacked size of each out stream
    crc: int | None = None
    substreams: int = 1

    @property
    def outs(self) -> int:
        return sum(c.outs for c in self.coders)

    @property
    def ins(self) -> int:
        return sum(c.ins for c in self.coders)

    def final(self) -> int:
        """The out stream nothing else reads: what the folder unpacks to."""
        bound = {o for _i, o in self.binds}
        for o in range(self.outs):
            if o not in bound:
                return o
        raise SevenZipError(tr("the archive's header is damaged"))

    @property
    def size(self) -> int:
        return self.sizes[self.final()]


@dataclass
class _Streams:
    pack_pos: int = 0
    pack_sizes: list[int] = field(default_factory=list)
    folders: list[_Folder] = field(default_factory=list)
    sizes: list[int] = field(default_factory=list)      # each substream, in order
    crcs: list[int | None] = field(default_factory=list)


@dataclass
class Entry:
    name: str                  # as stored, "lang/units.csv" (backslashes made forward)
    size: int
    is_dir: bool
    mtime: float               # seconds since 1970, 0 if the archive keeps none
    crc: int | None = None
    folder: int = -1           # the folder its data is in; -1 for an empty file or a folder
    offset: int = 0            # where in that folder's unpacked data it starts


def _read_folder(buf: _Buf) -> _Folder:
    coders = []
    for _ in range(buf.count(32)):
        flags = buf.byte()
        if flags & 0x80:
            raise SevenZipError(tr("the archive uses a very old feature this reader does not know"))
        method = buf.take(flags & 0x0F)
        ins, outs = (buf.count(32), buf.count(32)) if flags & 0x10 else (1, 1)
        props = buf.take(buf.count(1 << 16)) if flags & 0x20 else b""
        coders.append(_Coder(method, ins, outs, props))
    folder = _Folder(coders, [], [])
    folder.binds = [(buf.count(64), buf.count(64)) for _ in range(folder.outs - 1)]
    npacked = folder.ins - len(folder.binds)
    if npacked == 1:
        bound = {i for i, _o in folder.binds}
        folder.packed = [next(i for i in range(folder.ins) if i not in bound)]
    else:
        folder.packed = [buf.count(64) for _ in range(npacked)]
    return folder


def _read_streams(buf: _Buf) -> _Streams:
    s = _Streams()
    nid = buf.byte()
    if nid == PACK_INFO:
        s.pack_pos = buf.number()
        n = buf.count()
        nid = buf.byte()
        while nid != END:
            if nid == SIZE:
                s.pack_sizes = [buf.number() for _ in range(n)]
            elif nid == CRC:
                for d in buf.defined(n):
                    if d:
                        buf.uint32()
            else:
                raise SevenZipError(tr("the archive's header is damaged"))
            nid = buf.byte()
        nid = buf.byte()
    if nid == UNPACK_INFO:
        if buf.byte() != FOLDER:
            raise SevenZipError(tr("the archive's header is damaged"))
        n = buf.count()
        if buf.byte():
            raise SevenZipError(tr("the archive keeps its folder list elsewhere, which this reader does not follow"))
        s.folders = [_read_folder(buf) for _ in range(n)]
        if buf.byte() != CODERS_UNPACK_SIZE:
            raise SevenZipError(tr("the archive's header is damaged"))
        for f in s.folders:
            f.sizes = [buf.number() for _ in range(f.outs)]
        nid = buf.byte()
        while nid != END:
            if nid != CRC:
                raise SevenZipError(tr("the archive's header is damaged"))
            for f, d in zip(s.folders, buf.defined(n)):
                if d:
                    f.crc = buf.uint32()
            nid = buf.byte()
        nid = buf.byte()
    if nid == SUBSTREAMS_INFO:
        nid = buf.byte()
        if nid == NUM_UNPACK_STREAM:
            for f in s.folders:
                f.substreams = buf.count()
            nid = buf.byte()
        if nid == SIZE:
            for f in s.folders:
                if not f.substreams:
                    continue
                parts = [buf.number() for _ in range(f.substreams - 1)]
                rest = f.size - sum(parts)
                if rest < 0:
                    raise SevenZipError(tr("the archive's header is damaged"))
                s.sizes += parts + [rest]
            nid = buf.byte()
        else:
            for f in s.folders:
                if f.substreams > 1:
                    raise SevenZipError(tr("the archive's header is damaged"))
                if f.substreams == 1:
                    s.sizes.append(f.size)
        known = [f.crc if f.substreams == 1 and f.crc is not None else None for f in s.folders]
        wanted = sum(f.substreams for f, k in zip(s.folders, known) if k is None)
        found: list[int | None] = [None] * wanted
        while nid != END:
            if nid != CRC:
                raise SevenZipError(tr("the archive's header is damaged"))
            found = [buf.uint32() if d else None for d in buf.defined(wanted)]
            nid = buf.byte()
        it = iter(found)
        for f, k in zip(s.folders, known):
            s.crcs += [k] if k is not None else [next(it) for _ in range(f.substreams)]
        nid = buf.byte()                       # the end of the streams, after the end of the substreams
    elif s.folders:
        s.sizes = [f.size for f in s.folders]
        s.crcs = [f.crc for f in s.folders]
    if nid != END:
        raise SevenZipError(tr("the archive's header is damaged"))
    return s


def _read_files(buf: _Buf, streams: _Streams) -> list[Entry]:
    n = buf.count()
    empty = [False] * n
    empty_file: list[bool] = []
    names: list[str] = []
    mtimes: list[float] = [0.0] * n
    attrs: list[int | None] = [None] * n
    while True:
        kind = buf.byte()
        if kind == END:
            break
        body = _Buf(buf.take(buf.count(1 << 30)))
        if kind == EMPTY_STREAM:
            empty = body.bits(n)
        elif kind == EMPTY_FILE:
            empty_file = body.bits(sum(empty))
        elif kind == NAME:
            if body.byte():
                raise SevenZipError(tr("the archive keeps its file names elsewhere, which this reader does not follow"))
            names = body.data[1:].decode("utf-16-le", "replace").split("\x00")[:n]
        elif kind in (MTIME, WIN_ATTRIBUTES):
            defined = body.defined(n)
            if body.byte():
                raise SevenZipError(tr("the archive keeps its file details elsewhere, which this reader does not follow"))
            for i, d in enumerate(defined):
                if not d:
                    continue
                if kind == MTIME:
                    mtimes[i] = max(0.0, body.uint64() / 10_000_000 - FILETIME_EPOCH)
                else:
                    attrs[i] = body.uint32()
        # Times of creation and access, "anti" items, padding: nothing a mod needs.
    if len(names) != n:
        raise SevenZipError(tr("the archive's file names are damaged"))
    starts: list[tuple[int, int]] = []           # (folder, offset) of each substream
    for fi, f in enumerate(streams.folders):
        offset = 0
        for size in streams.sizes[len(starts):len(starts) + f.substreams]:
            starts.append((fi, offset))
            offset += size
    entries, stream, empty_seen = [], 0, 0
    for i in range(n):
        name = names[i].replace("\\", "/")
        if empty[i]:
            is_file = empty_seen < len(empty_file) and empty_file[empty_seen]
            empty_seen += 1
            is_dir = not is_file or bool((attrs[i] or 0) & DIRECTORY_ATTRIBUTE)
            entries.append(Entry(name, 0, is_dir, mtimes[i]))
            continue
        if stream >= len(streams.sizes):
            raise SevenZipError(tr("the archive's header is damaged"))
        folder, offset = starts[stream]
        entries.append(Entry(name, streams.sizes[stream], False, mtimes[i], streams.crcs[stream], folder, offset))
        stream += 1
    return entries


def _lzma_filter(coder: _Coder, out_size: int) -> dict:
    """The lzma module's description of an LZMA or LZMA2 coder. The dictionary is never made bigger
    than what it unpacks to, so a 64 MB dictionary for a 200 KB file costs 200 KB, not 64 MB."""
    def room(declared: int) -> int:
        return max(4096, min(declared, max(out_size, 1)))
    if coder.method == LZMA:
        if len(coder.props) < 5:
            raise SevenZipError(tr("the archive's LZMA settings are damaged"))
        d = coder.props[0]
        if d >= 9 * 5 * 5:
            raise SevenZipError(tr("the archive's LZMA settings are damaged"))
        return {"id": lzma.FILTER_LZMA1, "lc": d % 9, "lp": d // 9 % 5, "pb": d // 45,
                "dict_size": room(struct.unpack("<I", coder.props[1:5])[0])}
    p = coder.props[0] if coder.props else 40
    if p > 40:
        raise SevenZipError(tr("the archive's LZMA2 settings are damaged"))
    declared = 0xFFFFFFFF if p == 40 else (2 | (p & 1)) << (p // 2 + 11)
    return {"id": lzma.FILTER_LZMA2, "dict_size": room(declared)}


def _branch_filter(coder: _Coder) -> dict:
    if coder.method == DELTA:
        return {"id": lzma.FILTER_DELTA, "dist": (coder.props[0] if coder.props else 0) + 1}
    name = BRANCH[coder.method]
    if not hasattr(lzma, name):
        raise SevenZipError(tr("it is packed with the {filter} filter, which the manager cannot undo: unpack it "
                               "with 7-Zip, then add the folder", filter=name[7:]))
    spec = {"id": getattr(lzma, name)}
    if len(coder.props) == 4 and struct.unpack("<I", coder.props)[0]:
        spec["start_offset"] = struct.unpack("<I", coder.props)[0]
    return spec


def _refuse(method: bytes) -> None:
    if method == AES:
        raise SevenZipError(tr("it is password-protected: unpack it with 7-Zip, then add the folder"))
    if method in REFUSED:
        raise SevenZipError(tr("it is packed with {method}, which the manager cannot unpack: unpack it with "
                               "7-Zip, then add the folder", method=REFUSED[method]))
    known = {COPY, LZMA, LZMA2, DEFLATE, BZIP2, ZSTD, DELTA} | set(BRANCH)
    if method not in known:
        raise SevenZipError(tr("it is packed with a method the manager does not know (id {id}): unpack it with "
                               "7-Zip, then add the folder", id=method.hex()))


def _decode(folder: _Folder, packed: bytes) -> bytes:
    for c in folder.coders:
        _refuse(c.method)
    if len(folder.packed) != 1 or any(c.ins != 1 or c.outs != 1 for c in folder.coders):
        raise SevenZipError(tr("it is packed in a way the manager cannot unpack: unpack it with 7-Zip, then add "
                            "the folder"))
    # The coders in the order data goes through them when unpacking: from the one the archive feeds,
    # along the bindings, to the one whose output is the folder's.
    order = [folder.packed[0]]
    while True:
        feeds = [i for i, o in folder.binds if o == order[-1]]
        if not feeds:
            break
        if feeds[0] in order or len(order) > len(folder.coders):
            raise SevenZipError(tr("the archive's header is damaged"))
        order.append(feeds[0])
    if order[-1] != folder.final() or len(order) != len(folder.coders):
        raise SevenZipError(tr("the archive's header is damaged"))
    first, rest = folder.coders[order[0]], [folder.coders[i] for i in order[1:]]
    if first.method in (LZMA, LZMA2):
        if any(c.method not in BRANCH and c.method != DELTA for c in rest):
            raise SevenZipError(tr("it is packed in a way the manager cannot unpack: unpack it with 7-Zip, then "
                                "add the folder"))
        # One chain for liblzma, listed in the order it was packed: the filters, then the compressor.
        compressor = _lzma_filter(first, folder.sizes[order[0]])
        chain = [_branch_filter(c) for c in reversed(rest)] + [compressor]
        try:
            out = lzma.LZMADecompressor(lzma.FORMAT_RAW, filters=chain).decompress(packed)
            if len(out) < folder.size and first.method == LZMA and len(rest) == 1 and rest[0].method in BRANCH:
                # A branch filter keeps its last few bytes back until the data ends, and LZMA (unlike LZMA2)
                # never says where that is. At the end it passes them through unchanged, so they are
                # LZMA's own last bytes.
                raw = lzma.LZMADecompressor(lzma.FORMAT_RAW, filters=[compressor]).decompress(packed)
                out += raw[len(out):folder.size]
        except lzma.LZMAError as exc:
            raise SevenZipError(tr("its data is damaged ({error})", error=exc)) from None
        return out[:folder.size]
    if rest:
        raise SevenZipError(tr("it is packed in a way the manager cannot unpack: unpack it with 7-Zip, then add "
                            "the folder"))
    try:
        if first.method == COPY:
            return packed
        if first.method == BZIP2:
            return bz2.decompress(packed)
        if first.method == DEFLATE:
            d = zlib.decompressobj(-15)
            return d.decompress(packed) + d.flush()
        from compression import zstd
        return zstd.decompress(packed)
    except (OSError, ValueError, zlib.error, EOFError) as exc:
        raise SevenZipError(tr("its data is damaged ({error})", error=exc)) from None


class SevenZip:
    """A 7z archive: its entries, and the bytes of any file in it."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._unpacked: dict[int, bytes] = {}
        try:
            with open(self.path, "rb") as fh:
                self._fh = fh
                self._size = self.path.stat().st_size
                self.entries = self._read_header()
        except OSError as exc:
            raise SevenZipError(tr("it could not be read: {error}", error=exc)) from None
        finally:
            self._fh = None

    def _at(self, offset: int, size: int) -> bytes:
        if offset < 0 or size < 0 or offset + size > self._size:
            raise SevenZipError(tr("the archive is cut short: download it again"))
        if self._fh is not None:
            self._fh.seek(offset)
            data = self._fh.read(size)
        else:
            with open(self.path, "rb") as fh:
                fh.seek(offset)
                data = fh.read(size)
        if len(data) != size:
            raise SevenZipError(tr("the archive is cut short: download it again"))
        return data

    def _read_header(self) -> list[Entry]:
        head = self._at(0, 32) if self._size >= 32 else b""
        if head[:6] != SIGNATURE:
            raise SevenZipError(tr("it is not a 7z archive"))
        if zlib.crc32(head[12:32]) != struct.unpack("<I", head[8:12])[0]:
            raise SevenZipError(tr("the archive's first bytes are damaged: download it again"))
        offset, size, crc = struct.unpack("<QQI", head[12:32])
        if size == 0:
            self.streams = _Streams()
            return []
        header = self._at(32 + offset, size)
        if zlib.crc32(header) != crc:
            raise SevenZipError(tr("the archive's header is damaged: download it again"))
        buf = _Buf(header)
        nid = buf.byte()
        for _ in range(4):                     # a packed header; in principle it may be packed again
            if nid != ENCODED_HEADER:
                break
            streams = _read_streams(buf)
            if len(streams.folders) != 1:
                raise SevenZipError(tr("the archive's header is damaged"))
            buf = _Buf(self._unpack(streams, 0))
            nid = buf.byte()
        if nid != HEADER:
            raise SevenZipError(tr("the archive's header is damaged"))
        nid = buf.byte()
        if nid == ARCHIVE_PROPERTIES:
            while buf.byte():
                buf.take(buf.count(1 << 30))
            nid = buf.byte()
        if nid == ADDITIONAL_STREAMS:
            _read_streams(buf)
            nid = buf.byte()
        self.streams = _Streams()
        if nid == MAIN_STREAMS:
            self.streams = _read_streams(buf)
            nid = buf.byte()
        entries: list[Entry] = []
        if nid == FILES_INFO:
            entries = _read_files(buf, self.streams)
            nid = buf.byte()
        if nid != END:
            raise SevenZipError(tr("the archive's header is damaged"))
        return entries

    def _unpack(self, streams: _Streams, index: int) -> bytes:
        folder = streams.folders[index]
        if folder.size > MAX_UNPACKED:
            raise SevenZipError(tr("it unpacks to far more than any language mod: it is not one"))
        first = sum(len(f.packed) for f in streams.folders[:index])
        if first >= len(streams.pack_sizes):
            raise SevenZipError(tr("the archive's header is damaged"))
        start = 32 + streams.pack_pos + sum(streams.pack_sizes[:first])
        data = _decode(folder, self._at(start, streams.pack_sizes[first]))
        if len(data) != folder.size:
            raise SevenZipError(tr("its data is damaged: it unpacks to the wrong size"))
        if folder.crc is not None and zlib.crc32(data) != folder.crc:
            raise SevenZipError(tr("its data is damaged: a checksum does not match"))
        return data

    def read(self, entry: Entry) -> bytes:
        """The bytes of one file, checked against the archive's checksum for it."""
        if entry.folder < 0:
            return b""
        if entry.folder not in self._unpacked:
            self._unpacked[entry.folder] = self._unpack(self.streams, entry.folder)
        data = self._unpacked[entry.folder][entry.offset:entry.offset + entry.size]
        if len(data) != entry.size or (entry.crc is not None and zlib.crc32(data) != entry.crc):
            raise SevenZipError(tr("{file} in it is damaged: a checksum does not match", file=entry.name))
        return data
