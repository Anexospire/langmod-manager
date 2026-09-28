"""Read-only access to War Thunder ``*.vromfs.bin`` archives.

The language archives are small and compressed, so everything is read into memory and there is no memory map.

Layout::

    0x00  magic        "VRFs" (legacy) or "VRFx" (extended header)
    0x04  platform     b"\\x00\\x00PC" and friends
    0x08  u32          size of the unpacked image
    0x0C  u32          bits 0..25: packed payload size (0 = stored uncompressed),
                       bit 30: zstd + obfuscation, bit 31: MD5 digest follows
    -- VRFx only --
    0x10  u16          extension size
    0x12  u16          flags
    0x14  u8[4]        game version, stored reversed
    -- payload --

The unpacked image starts with two 16-byte table descriptors, the name
pointers and the (offset, size) data table, followed by the file bodies.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

from compression import zstd

MAGIC_VRFS = b"VRFs"
MAGIC_VRFX = b"VRFx"
FLAG_COMPRESSED = 0x40
PACKED_SIZE_MASK = 0x03FF_FFFF
NAME_MAP_ENTRY = "\xff?nm"

_KEY_HEAD = bytes.fromhex("55aa55aa0ff00ff055aa55aa48124812")
_KEY_TAIL = bytes.fromhex("4812481255aa55aa0ff00ff055aa55aa")


class VromfError(Exception):
    """The file is not a VROMF archive, or it is damaged."""


@dataclass(frozen=True)
class VromfEntry:
    name: str
    offset: int
    size: int


def _deobfuscate(payload: bytes) -> bytes:
    buf = bytearray(payload)
    n = len(buf)
    if n >= 16:
        for i in range(16):
            buf[i] ^= _KEY_HEAD[i]
    if n >= 32:
        start = n - 16 - (n % 4)
        for i in range(16):
            buf[start + i] ^= _KEY_TAIL[i]
    return bytes(buf)


class Vromf:
    """An opened archive, fully unpacked in memory."""

    def __init__(self, path: str | Path, data: bytes | None = None):
        self.path = Path(path)
        raw = data if data is not None else self.path.read_bytes()
        if len(raw) < 16 or raw[:4] not in (MAGIC_VRFS, MAGIC_VRFX):
            raise VromfError(f"{self.path.name} is not a VROMF archive")
        original_size, packed_raw = struct.unpack_from("<II", raw, 8)
        packed_size = packed_raw & PACKED_SIZE_MASK
        flags = (packed_raw >> 24) & 0xC0
        self.version: tuple[int, int, int, int] | None = None
        start = 16
        if raw[:4] == MAGIC_VRFX:
            ext_size = struct.unpack_from("<H", raw, 16)[0]
            v = raw[20:24]
            self.version = (v[3], v[2], v[1], v[0])
            start = 16 + max(ext_size, 8)
        if flags & FLAG_COMPRESSED and packed_size:
            try:
                image = zstd.decompress(_deobfuscate(raw[start:start + packed_size]))
            except zstd.ZstdError as exc:
                raise VromfError(f"{self.path.name}: {exc}") from exc
        else:
            image = raw[start:start + original_size]
        if len(image) != original_size:
            raise VromfError(f"{self.path.name}: unpacked size does not match the header")
        self.image = image
        self.entries = self._entries(image)
        self._by_name = {e.name: e for e in self.entries}
        self._by_lower = {e.name.lower(): e for e in self.entries}
        self._name_map = None
        self._dict = None

    @staticmethod
    def _entries(image: bytes) -> list[VromfEntry]:
        names_offset, names_count = struct.unpack_from("<II", image, 0)
        data_offset, data_count = struct.unpack_from("<II", image, 16)
        if names_count != data_count:
            raise VromfError("name and data tables disagree")
        ptrs = struct.unpack_from(f"<{names_count}Q", image, names_offset)
        out = []
        for i, p in enumerate(ptrs):
            end = image.index(b"\x00", p)
            name = image[p:end].decode("utf-8", "replace")
            offset, size = struct.unpack_from("<II", image, data_offset + i * 16)
            out.append(VromfEntry(name, offset, size))
        return out

    @property
    def version_str(self) -> str:
        return ".".join(map(str, self.version)) if self.version else "unknown"

    def names(self) -> list[str]:
        return [e.name for e in self.entries]

    def get(self, name: str) -> VromfEntry | None:
        """Look a file up by name; case is ignored, as the game does."""
        return self._by_name.get(name) or self._by_lower.get(name.lower())

    def read(self, name: str) -> bytes:
        e = self.get(name)
        if e is None:
            raise KeyError(name)
        return self.image[e.offset:e.offset + e.size]

    @property
    def zstd_dict(self):
        if self._dict is None:
            for e in self.entries:
                if e.name.endswith(".dict"):
                    self._dict = zstd.ZstdDict(self.read(e.name))
                    break
        return self._dict

    @property
    def name_map(self):
        """The shared name table "slim" BLK files point into, if there is one."""
        if self._name_map is None and self.get(NAME_MAP_ENTRY) is not None:
            from .blk import read_name_map
            self._name_map = read_name_map(self.read(NAME_MAP_ENTRY), self.zstd_dict)
        return self._name_map
