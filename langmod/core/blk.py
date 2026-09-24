"""Dagor BLK files, binary and text.

The game keeps ``lang/localization.blk`` in binary form inside
``lang.vromfs.bin``; mods ship a text copy of it, and ``config.blk`` is text
too. Both forms read into the same tree of :class:`Block` and :class:`Param`,
and :func:`to_text` writes that tree back out as text the game accepts.

Binary layout (integers ULEB128 unless noted), first byte the flavour::

    0x01  FAT             names embedded in the file
    0x02  FAT_ZSTD        u24 length + zstd frame of a FAT body
    0x03  SLIM            names from the archive's shared name map
    0x04  SLIM_ZSTD       zstd frame of a SLIM body
    0x05  SLIM_ZSTD_DICT  the same, compressed with the archive's dictionary

    names_count, [names_size, names], blocks_count, params_count,
    params_data_size, params_data, params_count x 8 bytes, blocks table
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Any

from compression import zstd

from ..i18n import tr


class BlkError(Exception):
    """Malformed or unsupported BLK data."""


@dataclass(slots=True)
class Param:
    name: str
    type: str       # the text suffix: "t", "i", "r", "b", "p2", "c", ...
    value: Any


@dataclass(slots=True)
class Block:
    name: str | None
    params: list[Param] = field(default_factory=list)
    blocks: list["Block"] = field(default_factory=list)

    def block(self, name: str) -> "Block | None":
        for b in self.blocks:
            if b.name == name:
                return b
        return None

    def values(self, name: str) -> list[Any]:
        return [p.value for p in self.params if p.name == name]

    def value(self, name: str, default: Any = None) -> Any:
        for p in self.params:
            if p.name == name:
                return p.value
        return default


# -- binary -----------------------------------------------------------------

_SUFFIX = {1: "t", 2: "i", 3: "r", 4: "p2", 5: "p3", 6: "p4", 7: "ip2", 8: "ip3",
           9: "b", 10: "c", 11: "m", 12: "i64", 13: "ip4"}
_VEC = {4: "<2f", 5: "<3f", 6: "<4f", 7: "<2i", 8: "<3i", 13: "<4i"}


def _uleb(buf: bytes, pos: int) -> tuple[int, int]:
    result = shift = 0
    while True:
        try:
            byte = buf[pos]
        except IndexError:
            raise BlkError("unexpected end of data") from None
        pos += 1
        result |= (byte & 0x7F) << shift
        if byte < 0x80:
            return result, pos
        shift += 7


def read_name_map(data: bytes, zstd_dict=None) -> list[str]:
    """The ``\\xff?nm`` entry: 8-byte hash, 32-byte dictionary hash, zstd names."""
    if len(data) < 40:
        raise BlkError("name map is too short")
    use_dict = zstd_dict is not None and any(data[8:40])
    raw = zstd.decompress(data[40:], zstd_dict=zstd_dict) if use_dict else zstd.decompress(data[40:])
    _count, pos = _uleb(raw, 0)
    size, pos = _uleb(raw, pos)
    parts = raw[pos:pos + size].split(b"\x00")
    if parts and parts[-1] == b"":
        parts.pop()
    return [p.decode("utf-8", "replace") for p in parts]


def _param(body: bytes, off: int, names: list[str], pdata: bytes) -> Param:
    name_id = body[off] | (body[off + 1] << 8) | (body[off + 2] << 16)
    kind = body[off + 3]
    raw = body[off + 4:off + 8]
    try:
        name = names[name_id]
        suffix = _SUFFIX[kind]
    except (IndexError, KeyError):
        raise BlkError(f"bad parameter record at {off}") from None
    word = struct.unpack("<I", raw)[0]
    if kind == 1:
        if word & 0x8000_0000:
            value: Any = names[word & 0x7FFF_FFFF]
        else:
            end = pdata.find(b"\x00", word)
            value = pdata[word:end if end >= 0 else len(pdata)].decode("utf-8", "replace")
    elif kind == 2:
        value = struct.unpack("<i", raw)[0]
    elif kind == 3:
        value = struct.unpack("<f", raw)[0]
    elif kind == 9:
        value = word != 0
    elif kind == 10:
        value = (raw[2], raw[1], raw[0], raw[3])
    elif kind == 11:
        m = struct.unpack_from("<12f", pdata, word)
        value = (m[0:3], m[3:6], m[6:9], m[9:12])
    elif kind == 12:
        value = struct.unpack_from("<q", pdata, word)[0]
    else:
        value = struct.unpack_from(_VEC[kind], pdata, word)
    return Param(name, suffix, value)


def _decode_body(body: bytes, name_map: list[str] | None) -> Block:
    pos = 0
    names_count, pos = _uleb(body, pos)
    if names_count:
        size, pos = _uleb(body, pos)
        parts = body[pos:pos + size].split(b"\x00")
        pos += size
        if parts and parts[-1] == b"":
            parts.pop()
        names = [p.decode("utf-8", "replace") for p in parts]
    elif name_map is None:
        raise BlkError("this BLK needs the archive's name map")
    else:
        names = name_map
    blocks_count, pos = _uleb(body, pos)
    params_count, pos = _uleb(body, pos)
    pdata_size, pos = _uleb(body, pos)
    pdata = body[pos:pos + pdata_size]
    pos += pdata_size
    params = [_param(body, off, names, pdata) for off in range(pos, pos + params_count * 8, 8)]
    pos += params_count * 8
    infos = []
    for _ in range(blocks_count):
        name_id, pos = _uleb(body, pos)
        pcount, pos = _uleb(body, pos)
        bcount, pos = _uleb(body, pos)
        first = 0
        if bcount:
            first, pos = _uleb(body, pos)
        infos.append((name_id, pcount, bcount, first))
    if not infos:
        return Block(None, params)
    blocks = []
    cursor = 0
    for name_id, pcount, _b, _f in infos:
        blocks.append(Block(names[name_id - 1] if name_id else None, params[cursor:cursor + pcount]))
        cursor += pcount
    for blk, (_n, _p, bcount, first) in zip(blocks, infos):
        blk.blocks = blocks[first:first + bcount]
    return blocks[0]


def parse_binary(data: bytes, name_map: list[str] | None = None, zstd_dict=None) -> Block:
    if not data:
        return Block(None)
    flavour = data[0]
    try:
        if flavour in (1, 3):
            body = data[1:]
        elif flavour == 2:
            length = data[1] | (data[2] << 8) | (data[3] << 16)
            body = zstd.decompress(data[4:4 + length])
            if body[:1] == b"\x01":
                body = body[1:]
        elif flavour == 4:
            body = zstd.decompress(data[1:])
        elif flavour == 5:
            if zstd_dict is None:
                raise BlkError("BLK is dictionary-compressed but no dictionary was given")
            body = zstd.decompress(data[1:], zstd_dict=zstd_dict)
        else:
            raise BlkError(f"not a binary BLK (first byte 0x{flavour:02x})")
    except zstd.ZstdError as exc:
        raise BlkError(str(exc)) from exc
    return _decode_body(body, name_map)


def is_binary(data: bytes) -> bool:
    return bool(data) and data[0] in (1, 2, 3, 4, 5)


# -- text -------------------------------------------------------------------

_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "'": "'", "~": "~"}
_STOP = set(" \t\r\n{}=:;\"'")


class _Reader:
    """Hand-written reader for the text form, forgiving by design.

    Mods are edited by hand, so a line it cannot make sense of is noted in
    ``problems`` and skipped instead of failing the whole file.
    """

    def __init__(self, text: str):
        self.s = text
        self.i = 0
        self.n = len(text)
        self.problems: list[str] = []

    def line(self) -> int:
        return self.s.count("\n", 0, self.i) + 1

    def skip_space(self, newlines: bool = True) -> None:
        s, n = self.s, self.n
        while self.i < n:
            c = s[self.i]
            if c in " \t﻿" or (newlines and c in "\r\n;"):
                self.i += 1
            elif s.startswith("//", self.i):
                end = s.find("\n", self.i)
                self.i = n if end < 0 else end
            elif s.startswith("/*", self.i):
                end = s.find("*/", self.i + 2)
                self.i = n if end < 0 else end + 2
            else:
                return

    def skip_line(self) -> None:
        end = self.s.find("\n", self.i)
        self.i = self.n if end < 0 else end + 1

    def quoted(self) -> str:
        q = self.s[self.i]
        if self.s.startswith(q * 3, self.i):
            end = self.s.find(q * 3, self.i + 3)
            if end < 0:
                raise BlkError(tr("a string in triple quotes is never closed"))
            out = self.s[self.i + 3:end]
            self.i = end + 3
            return out
        self.i += 1
        out = []
        s, n = self.s, self.n
        while self.i < n:
            c = s[self.i]
            if c == "~" and self.i + 1 < n:
                out.append(_ESCAPES.get(s[self.i + 1], s[self.i + 1]))
                self.i += 2
            elif c == q:
                self.i += 1
                return "".join(out)
            elif c == "\n":
                raise BlkError(tr("a string runs past the end of the line"))
            else:
                out.append(c)
                self.i += 1
        raise BlkError(tr("a string is never closed"))

    def bare(self) -> str:
        s, n = self.s, self.n
        start = self.i
        while self.i < n:
            c = s[self.i]
            if c in _STOP or s.startswith("//", self.i) or s.startswith("/*", self.i):
                break
            self.i += 1
        return s[start:self.i]

    def raw_value(self) -> str:
        s, n = self.s, self.n
        start = self.i
        while self.i < n and s[self.i] not in "\r\n;}" and not s.startswith("//", self.i):
            self.i += 1
        return s[start:self.i].strip()

    def block(self, into: Block, depth: int) -> None:
        while True:
            self.skip_space()
            if self.i >= self.n:
                if depth:
                    self.problems.append(tr("line {line}: block {name} is never closed", line=self.line(),
                                            name=repr(into.name)))
                return
            c = self.s[self.i]
            if c == "}":
                self.i += 1
                if depth:
                    return
                self.problems.append(tr("line {line}: a closing brace with nothing to close", line=self.line()))
                continue
            start = self.i
            try:
                self.statement(into, depth)
            except BlkError as exc:
                self.i = start
                self.problems.append(tr("line {line}: {problem}", line=self.line(), problem=exc))
                self.skip_line()

    def statement(self, into: Block, depth: int) -> None:
        name = self.quoted() if self.s[self.i] in "\"'" else self.bare()
        if not name:
            raise BlkError(tr("{char} where a name should be", char=repr(self.s[self.i])))
        self.skip_space(newlines=False)
        if name == "include" and self.i < self.n and self.s[self.i] in "\"'":
            into.params.append(Param("include", "include", self.quoted()))
            return
        self.skip_space()
        c = self.s[self.i] if self.i < self.n else ""
        if c == "{":
            self.i += 1
            child = Block(name)
            into.blocks.append(child)
            self.block(child, depth + 1)
            return
        suffix = "t"
        if c == ":":
            self.i += 1
            suffix = self.bare().lower()
            self.skip_space(newlines=False)
            c = self.s[self.i] if self.i < self.n else ""
        if c != "=":
            raise BlkError(tr("no = after {name}", name=repr(name)))
        self.i += 1
        self.skip_space(newlines=False)
        if suffix == "t" and self.i < self.n and self.s[self.i] in "\"'":
            value: Any = self.quoted()
        else:
            value = _convert(suffix, self.raw_value())
        into.params.append(Param(name, suffix, value))


def _convert(suffix: str, raw: str) -> Any:
    raw = raw.strip('"')
    try:
        if suffix == "t":
            return raw
        if suffix in ("i", "i64"):
            return int(raw)
        if suffix == "r":
            return float(raw)
        if suffix == "b":
            return raw.lower() in ("yes", "true", "on", "1")
        if suffix in ("p2", "p3", "p4"):
            return tuple(float(x) for x in raw.split(","))
        if suffix in ("ip2", "ip3", "ip4", "c"):
            return tuple(int(x) for x in raw.split(","))
    except ValueError:
        raise BlkError(tr("{value} cannot be read as type {type}", value=repr(raw), type=repr(suffix))) from None
    return raw


def parse_text(text: str) -> tuple[Block, list[str]]:
    """Read text BLK. Returns the tree and a list of lines that were skipped."""
    r = _Reader(text)
    root = Block(None)
    r.block(root, 0)
    return root, r.problems


def parse_any(data: bytes, name_map=None, zstd_dict=None) -> tuple[Block, list[str]]:
    if is_binary(data):
        return parse_binary(data, name_map, zstd_dict), []
    return parse_text(data.decode("utf-8-sig", "replace"))


def _escape(s: str) -> str:
    return (s.replace("~", "~~").replace('"', '~"').replace("\n", "~n")
            .replace("\r", "~r").replace("\t", "~t"))


def _fmt(p: Param) -> str:
    v = p.value
    if p.type == "t" or p.type == "include":
        return f'"{_escape(v)}"'
    if p.type == "b":
        return "yes" if v else "no"
    if p.type == "r":
        return repr(float(v))
    if p.type == "m":
        return "[" + " ".join("[" + ", ".join(repr(c) for c in row) + "]" for row in v) + "]"
    if isinstance(v, tuple):
        return ", ".join(repr(c) if isinstance(c, float) else str(c) for c in v)
    return str(v)


def to_text(block: Block, indent: str = "  ") -> str:
    lines: list[str] = []

    def emit(b: Block, depth: int) -> None:
        pad = indent * depth
        for p in b.params:
            if p.type == "include":
                lines.append(f"{pad}include {_fmt(p)}")
            else:
                lines.append(f"{pad}{p.name}:{p.type}={_fmt(p)}")
        for child in b.blocks:
            if lines and depth == 0:
                lines.append("")
            lines.append(f"{pad}{child.name}{{")
            emit(child, depth + 1)
            lines.append(f"{pad}}}")

    emit(block, 0)
    return "\n".join(lines) + "\n"
