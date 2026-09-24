"""Langmod Manager's own updates: finding a newer release, fetching and checking it, and the swap.

Against a fake GitHub and throwaway folders; no program is started.
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import os
import shutil
import sys
import tempfile
import unittest
import unittest.mock
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_updates import FakeHttp  # noqa: E402
from langmod import cli  # noqa: E402
from langmod.core import selfupdate, sources  # noqa: E402
from langmod.core.library import Library  # noqa: E402
from langmod.core.prefs import Prefs  # noqa: E402
from langmod.core.sources import SourceError  # noqa: E402

API = "https://api.github.com/repos/me/lm/releases"


def release_zip(names: dict[str, bytes] | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in (names or {"Langmod Manager/Langmod Manager.exe": b"new program",
                                     "Langmod Manager/_internal/base.dll": b"new insides",
                                     "Langmod Manager/Read me.txt": b"new read me"}).items():
            zf.writestr(name, data)
    return buf.getvalue()


def gh_release(tag: str, blob: bytes, prerelease: bool = False, name: str = "", digest: bool = True) -> dict:
    name = name or f"Langmod-Manager-{tag.lstrip('v')}-windows.zip"
    asset = {"name": name, "size": len(blob),
             "browser_download_url": f"https://github.com/me/lm/releases/download/{tag}/{name}"}
    if digest:
        asset["digest"] = "sha256:" + hashlib.sha256(blob).hexdigest()
    return {"tag_name": tag, "prerelease": prerelease, "draft": False,
            "html_url": f"https://github.com/me/lm/releases/tag/{tag}", "assets": [asset]}


class Versions(unittest.TestCase):
    def test_order(self):
        p = selfupdate.parse
        self.assertLess(p("1.0.0rc2"), p("1.0.0rc3ea"))
        self.assertLess(p("1.0.0rc3ea"), p("1.0.0"))                  # the full release comes after its candidates
        self.assertEqual(p("1.0.0rc3ea"), p("v1.0.0rc3+ea"))          # the tag, and pyproject's spelling
        self.assertLess(p("1.0.0"), p("1.0.1"))
        self.assertEqual(p("1.0"), p("1.0.0"))
        self.assertIsNone(p("latest"))


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="langmod-self-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.http = FakeHttp()
        for patcher in (unittest.mock.patch.object(sources, "http", self.http),
                        unittest.mock.patch.dict(os.environ, {"LANGMOD_SELF_REPO": "me/lm"})):
            patcher.start()
            self.addCleanup(patcher.stop)
        os.environ.pop("LANGMOD_SELF_API", None)
        self.blob = release_zip()


class Finding(Base):
    def test_the_newest_release_above_this_one(self):
        self.http.json[API] = [gh_release("v2.0.0", self.blob) | {"draft": True},
                               gh_release("v1.5.0", self.blob, name="source.zip"),     # no Windows zip
                               gh_release("v1.0.1", self.blob),
                               gh_release("v1.0.0rc2", self.blob)]
        new = selfupdate.check("1.0.0rc3ea")
        self.assertEqual((new.version, new.file_name), ("1.0.1", "Langmod-Manager-1.0.1-windows.zip"))
        self.assertEqual(new.sha256, hashlib.sha256(self.blob).hexdigest())
        self.assertIsNone(selfupdate.check("1.0.1"))

    def test_test_releases_only_for_test_builds(self):
        self.http.json[API] = [gh_release("v1.0.0rc9", self.blob, prerelease=True)]
        self.assertEqual(selfupdate.check("1.0.0rc3ea").version, "1.0.0rc9")     # early access sees them
        self.http.json[API] = [gh_release("v1.1.0rc1", self.blob, prerelease=True)]
        self.assertIsNone(selfupdate.check("1.0.0"))                               # a full release does not

    def test_nowhere_to_look_asks_nothing(self):
        with unittest.mock.patch.dict(os.environ, {"LANGMOD_SELF_REPO": ""}):
            self.assertFalse(selfupdate.available())
            self.assertIsNone(selfupdate.check())
        self.assertEqual(self.http.calls, [])

    def test_what_was_found_is_kept_until_it_is_this_version(self):
        lib = Library(self.tmp / "home")
        prefs = Prefs(lib)
        self.assertTrue(selfupdate.due(prefs))
        self.http.json[API] = [gh_release("v9.0.0", self.blob)]
        selfupdate.record(prefs, selfupdate.check())
        self.assertFalse(selfupdate.due(prefs))
        self.assertEqual(selfupdate.known(Prefs(Library(self.tmp / "home"))).version, "9.0.0")
        self.assertFalse(selfupdate.newer(selfupdate.known(prefs), "9.0.0"))     # once installed, no longer news


class Fetching(Base):
    def found(self, blob: bytes) -> selfupdate.Update:
        self.http.json[API] = [gh_release("v9.0.0", self.blob)]
        new = selfupdate.check()
        self.http.files[new.url] = blob
        return new

    def test_the_download_is_checked_against_github(self):
        new = self.found(self.blob)
        path = selfupdate.download(new, self.tmp / "downloads")
        staged = selfupdate.unpack(path, self.tmp / "update" / new.version)
        self.assertEqual((staged / "Langmod Manager.exe").read_bytes(), b"new program")
        self.assertFalse(path.exists())                                 # the zip goes once unpacked
        tampered = bytearray(self.blob)
        tampered[-30] ^= 1
        new = self.found(bytes(tampered))
        with self.assertRaises(SourceError):
            selfupdate.download(new, self.tmp / "downloads")
        self.assertEqual(list((self.tmp / "downloads").iterdir()), [])

    def test_only_from_github(self):
        new = self.found(self.blob)
        new.url = "https://example.com/Langmod-Manager-9.0.0-windows.zip"
        with self.assertRaises(SourceError):
            selfupdate.download(new, self.tmp / "downloads")
        self.assertEqual(self.http.calls, [API + "?per_page=30"])

    def test_anything_but_a_release_is_refused(self):
        for names in ({"Other/Langmod Manager.exe": b"x"},
                      {"Langmod Manager/Langmod Manager.exe": b"x", "Langmod Manager/../evil.exe": b"x"},
                      {"Langmod Manager/Read me.txt": b"x"}):
            zip_path = self.tmp / "bad.zip"
            zip_path.write_bytes(release_zip(names))
            with self.assertRaises(SourceError):
                selfupdate.unpack(zip_path, self.tmp / "update")
            self.assertFalse((self.tmp / "evil.exe").exists())
        zip_path.write_bytes(b"not a zip at all")
        with self.assertRaises(SourceError):
            selfupdate.unpack(zip_path, self.tmp / "update")


class Swapping(unittest.TestCase):
    """The new copy taking the old one's place, with the processes left out."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="langmod-swap-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.target = self.tmp / "Games" / "Langmod Manager"
        for rel, data in (("Langmod Manager.exe", b"old program"), ("_internal/old.dll", b"old insides"),
                          ("my notes.txt", b"the player's"), ("extra/mine.csv", b"the player's too")):
            (self.target / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.target / rel).write_bytes(data)
        self.staged = self.tmp / "home" / "update" / "9.0.0" / "Langmod Manager"
        for rel, data in (("Langmod Manager.exe", b"new program"), ("_internal/base.dll", b"new insides")):
            (self.staged / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.staged / rel).write_bytes(data)
        self.started, self.told = [], []
        for name, fake in (("_start", self.started.append), ("_tell", self.told.append), ("_wait", lambda *a: None)):
            patcher = unittest.mock.patch.object(selfupdate, name, fake)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_it_takes_the_old_ones_place(self):
        self.assertEqual(selfupdate.finish(1, self.target, self.staged), 0)
        self.assertEqual((self.target / "Langmod Manager.exe").read_bytes(), b"new program")
        self.assertEqual((self.target / "my notes.txt").read_bytes(), b"the player's")
        self.assertEqual((self.target / "extra" / "mine.csv").read_bytes(), b"the player's too")
        self.assertFalse((self.target / "_internal" / "old.dll").exists())      # not the old build's insides
        self.assertEqual(self.started, [self.target])
        old = self.target.with_name("Langmod Manager.old")
        self.assertTrue(old.is_dir())
        selfupdate.tidy(self.target, self.tmp / "home")                          # the next start clears up
        self.assertFalse(old.exists())
        self.assertFalse((self.tmp / "home" / "update").exists())

    def test_a_folder_it_cannot_move_starts_the_old_one_again(self):
        with unittest.mock.patch.object(selfupdate.os, "replace", side_effect=OSError("in use")):
            self.assertEqual(selfupdate.finish(1, self.target, self.staged, tries=2), 1)
        self.assertEqual((self.target / "Langmod Manager.exe").read_bytes(), b"old program")
        self.assertEqual((self.started, len(self.told)), ([self.target], 1))

    def test_a_copy_cut_short_puts_the_old_one_back(self):
        def half(src, dst):
            (Path(dst) / "Langmod Manager.exe").parent.mkdir(parents=True)
            (Path(dst) / "Langmod Manager.exe").write_bytes(b"half")
            raise OSError("disk full")

        with unittest.mock.patch.object(selfupdate.shutil, "copytree", half):
            self.assertEqual(selfupdate.finish(1, self.target, self.staged), 1)
        self.assertEqual((self.target / "Langmod Manager.exe").read_bytes(), b"old program")
        self.assertEqual((self.target / "_internal" / "old.dll").read_bytes(), b"old insides")
        self.assertEqual(len(self.told), 1)


class CommandLine(Base):
    def test_upgrade_check_and_the_hidden_step(self):
        self.http.json[API] = [gh_release("v9.0.0", self.blob)]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(cli.main(["--home", str(self.tmp / "home"), "upgrade", "--check"]), 0)
        self.assertIn("Langmod Manager 9.0.0 is out", out.getvalue())
        with unittest.mock.patch.object(selfupdate, "finish", return_value=0) as finish:
            self.assertEqual(cli.main(["_finish-update", "123", str(self.tmp / "app")]), 0)
        finish.assert_called_once_with(123, self.tmp / "app")


if __name__ == "__main__":
    unittest.main()
