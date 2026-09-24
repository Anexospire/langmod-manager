"""The 7z reader, against archives 7-Zip itself made (``tests/data``), and adding them as mods.

With 7-Zip installed, larger archives are made on the spot in every way it
packs them, and read back byte for byte.
"""
from __future__ import annotations

import os
import random
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fakegame import build  # noqa: E402
from langmod.core.game import GameLang  # noqa: E402
from langmod.core.library import Library, LibraryError, read_package  # noqa: E402
from langmod.core.plan import build_plan  # noqa: E402
from langmod.core.sevenzip import SevenZip, SevenZipError  # noqa: E402

DATA = Path(__file__).resolve().parent / "data"
READABLE = ("lzma2", "lzma", "bzip2", "deflate", "store-nonsolid", "delta-lzma2")
SEVEN_ZIP = next((p for p in (Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "7-Zip" / "7z.exe",
                              Path(r"C:\Program Files (x86)\7-Zip\7z.exe")) if p.is_file()), None)


class Reading(unittest.TestCase):
    def test_every_way_7zip_packs(self):
        want = None
        for kind in READABLE:
            z = SevenZip(DATA / f"mod-{kind}.7z")
            files = {e.name: z.read(e) for e in z.entries if not e.is_dir}
            dirs = sorted(e.name for e in z.entries if e.is_dir)
            self.assertEqual(dirs, ["Seven Mod", "Seven Mod/extras", "Seven Mod/lang"], kind)
            self.assertIn("Seven Mod/lang/SV_01_units.csv", files, kind)
            self.assertIn("ünïcode 名前".encode("utf-8"), files["Seven Mod/lang/SV_02_names.csv"], kind)
            self.assertTrue(all(e.mtime > 1.7e9 for e in z.entries if not e.is_dir), kind)
            want = want or files
            self.assertEqual(files, want, f"{kind} unpacks differently")

    def test_refused_with_a_reason(self):
        for kind, why in (("ppmd", "PPMd"), ("password", "password")):
            z = None
            with self.assertRaises(SevenZipError) as caught:
                z = SevenZip(DATA / f"mod-{kind}.7z")
                for e in z.entries:
                    z.read(e)
            self.assertIn(why, str(caught.exception))
            self.assertIn("7-Zip", str(caught.exception))

    def test_damaged_or_cut_short(self):
        tmp = Path(tempfile.mkdtemp(prefix="langmod-7z-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        good = (DATA / "mod-lzma2.7z").read_bytes()
        cut = tmp / "cut.7z"
        cut.write_bytes(good[:len(good) - 40])
        with self.assertRaises(SevenZipError):
            SevenZip(cut)
        flipped = bytearray((DATA / "mod-store-nonsolid.7z").read_bytes())
        flipped[200] ^= 0xFF                                    # inside a stored file's data
        bad = tmp / "bad.7z"
        bad.write_bytes(bytes(flipped))
        z = SevenZip(bad)
        with self.assertRaises(SevenZipError) as caught:
            for e in z.entries:
                z.read(e)
        self.assertIn("checksum", str(caught.exception))
        (tmp / "no.7z").write_bytes(b"not an archive at all")
        with self.assertRaises(SevenZipError):
            SevenZip(tmp / "no.7z")

    @unittest.skipUnless(SEVEN_ZIP, "7-Zip is not installed")
    def test_made_on_the_spot(self):
        tmp = Path(tempfile.mkdtemp(prefix="langmod-7z-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        rng = random.Random(5)
        words = ["Tiger", "Panther", "F-16", "Т-34", "Śmigłowiec", "战斗机", "\"q\""]
        files = {
            "Big/lang/BG_units.csv": "\r\n".join(f"k{i};{rng.choice(words)} {i}" for i in range(20000)).encode(),
            "Big/lang/localization.blk": b'locTable{\r\n  file:t="%lang/BG_units.csv"\r\n}\r\n',
            "Big/noise.bin": os.urandom(40000),
            "Big/empty.csv": b"",
        }
        for name, data in files.items():
            (tmp / "src" / name).parent.mkdir(parents=True, exist_ok=True)
            (tmp / "src" / name).write_bytes(data)
        for label, switches in (("default", []), ("ultra", ["-mx=9"]), ("lzma", ["-m0=LZMA"]),
                                ("bcj", ["-m0=BCJ", "-m1=LZMA"]), ("nonsolid", ["-ms=off"]),
                                ("threads", ["-mmt=4"]), ("plainheader", ["-mhc=off"])):
            arc = tmp / f"{label}.7z"
            subprocess.run([str(SEVEN_ZIP), "a", "-t7z", str(arc), "Big", *switches], cwd=tmp / "src",
                           check=True, capture_output=True)
            z = SevenZip(arc)
            got = {e.name: z.read(e) for e in z.entries if not e.is_dir}
            self.assertEqual(got, files, label)


class AddingAsMods(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="langmod-7z-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.lib = Library(self.tmp / "home")

    def test_a_7z_adds_like_a_zip(self):
        pkg = read_package(DATA / "mod-lzma2.7z")
        self.assertEqual(sorted(pkg.files), ["SV_01_units.csv", "SV_02_names.csv", "localization.blk"])
        self.assertEqual(pkg.label, "mod-lzma2")
        self.assertTrue(pkg.stamp.startswith("20"))
        self.assertIn("Seven Mod, packed with 7-Zip", pkg.readme)
        # Named after what it is inside, not the name it came with: a 7z called .zip still opens.
        disguised = self.tmp / "Seven Mod v2.zip"
        shutil.copy(DATA / "mod-lzma.7z", disguised)
        mod = self.lib.add(disguised).mod
        self.assertEqual((mod.name, mod.version), ("Seven Mod", "2"))
        self.assertEqual(mod.prefix, "SV_")
        game_root = build(self.tmp / "War Thunder")
        game = GameLang(game_root)
        plan = build_plan(self.lib, game)
        self.assertEqual(plan.reports[mod.id].loads, ["SV_01_units.csv", "SV_02_names.csv"])
        # A newer version as a 7z is taken as an update of it.
        newer = self.tmp / "Seven Mod v3.7z"
        shutil.copy(DATA / "mod-bzip2.7z", newer)
        res = self.lib.add(newer)
        self.assertEqual((res.action, res.mod.id, res.mod.version), ("updated", mod.id, "3"))

    def test_what_cannot_be_opened_says_why(self):
        with self.assertRaises(LibraryError) as caught:
            self.lib.add(DATA / "mod-ppmd.7z")
        self.assertIn("PPMd", str(caught.exception))
        rar = self.tmp / "mod.rar"
        rar.write_bytes(b"Rar!\x1a\x07\x01\x00" + b"\x00" * 40)
        with self.assertRaises(LibraryError) as caught:
            self.lib.add(rar)
        self.assertIn("RAR", str(caught.exception))
        junk = self.tmp / "mod.zip"
        junk.write_bytes(b"hello")
        with self.assertRaises(LibraryError) as caught:
            self.lib.add(junk)
        self.assertIn("not a zip or 7z", str(caught.exception))
        self.assertEqual(self.lib.mods, [])


if __name__ == "__main__":
    unittest.main()
