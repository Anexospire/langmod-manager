"""A tiny War Thunder folder, built from scratch for the tests.

The archive is a real (uncompressed) VRFs file, so the same reader the
manager uses on the game reads it here too.
"""
from __future__ import annotations

import struct
from pathlib import Path


def pack_vromf(files: dict[str, bytes]) -> bytes:
    names = list(files)
    count = len(names)
    names_table = 32
    data_table = names_table + 8 * count
    strings_at = data_table + 16 * count
    blob = b""
    name_ptrs = []
    for n in names:
        name_ptrs.append(strings_at + len(blob))
        blob += n.encode("utf-8") + b"\x00"
    body_at = strings_at + len(blob)
    body_at += (-body_at) % 16
    image = bytearray(body_at)
    struct.pack_into("<II", image, 0, names_table, count)
    struct.pack_into("<II", image, 16, data_table, count)
    struct.pack_into(f"<{count}Q", image, names_table, *name_ptrs)
    image[strings_at:strings_at + len(blob)] = blob
    for i, n in enumerate(names):
        offset = len(image)
        image += files[n]
        struct.pack_into("<II", image, data_table + 16 * i, offset, len(files[n]))
        image += b"\x00" * ((-len(image)) % 16)
    header = b"VRFs" + b"\x00\x00PC" + struct.pack("<II", len(image), 0)
    return header + bytes(image)


def csv_bytes(rows: list[tuple[str, ...]], columns=("English", "French")) -> bytes:
    def q(s: str) -> str:
        return '"' + s.replace('"', '""') + '"'
    lines = [";".join(q(f"<{c}>") for c in ("ID|readonly|noverify",) + tuple(columns))]
    lines += [";".join(q(c) for c in row) for row in rows]
    return ("\r\n".join(lines) + "\r\n").encode("utf-8")


BASE_LIST = """\
regional:t="%langRegional/special_events.csv"
default_lang:t="English"
locTable{
  file:t="%lang/menu.csv"
  file:t="%lang/units.csv"
  file:t="%lang/inf.csv"
}
"""

UNITS = [
    ("f_16a_0", "F-16A Fighting Falcon", "F-16A"),
    ("t_34_85", "T-34-85", "T-34-85"),
    ("new_tank_2024", "Brand New Tank", "Char neuf"),
]
MENU = [("mainmenu/play", "Play", "Jouer"), ("mainmenu/quit", "Quit", "Quitter")]
INF = [("soldier_rifle", "Rifleman", "Fusilier")]
REGIONAL = [("event_name", "Autumn Event", "Événement")]

CONFIG = """\
language:t="English"
video{
  driver:t="dx11"
}
debug{
  screenshotAsJpeg:b=yes
}
"""


def build(root: Path, version=(2, 59, 0, 13), regional: dict[str, list] | None = None) -> Path:
    """``regional``: the event tables, file name -> rows, in the game's order (special_events.csv alone)."""
    regional = regional or {"special_events.csv": REGIONAL}
    listing = "".join(f'regional:t="%langRegional/{n}"\n' for n in regional)
    root.mkdir(parents=True, exist_ok=True)
    files = {
        "lang/localization.blk": BASE_LIST.replace('regional:t="%langRegional/special_events.csv"\n', listing).encode(),
        "lang/units.csv": csv_bytes(UNITS),
        "lang/menu.csv": csv_bytes(MENU),
        "lang/inf.csv": csv_bytes(INF),
    }
    data = bytearray(pack_vromf(files))
    # Make it a VRFx so the version shows, as the game's own archives do.
    head = b"VRFx" + bytes(data[4:16]) + struct.pack("<HH", 8, 0) + bytes(reversed(version))
    (root / "lang.vromfs.bin").write_bytes(head + bytes(data[16:]))
    cache = root / "cache" / "binary.2.59.0"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "regional-lang.vromfs.bin").write_bytes(
        pack_vromf({f"lang/{n}": csv_bytes(rows) for n, rows in regional.items()}))
    (root / "config.blk").write_text(CONFIG, "utf-8", newline="\r\n")
    return root
