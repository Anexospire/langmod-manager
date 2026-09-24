"""Updates from WT Live, GitHub and Nexus Mods, against a fake network.

The answers the fake gives are shaped after what the real services returned
when this was written (WT Live's posts/get and feed/get_user, GitHub's
releases, Nexus's files.json), cut down to the fields that matter.
"""
from __future__ import annotations

import contextlib
import io
import shutil
import sys
import tempfile
import time
import unittest
import unittest.mock
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fakegame import build  # noqa: E402
from langmod.core import sources, updates  # noqa: E402
from langmod.core.library import Library  # noqa: E402
from langmod.core.prefs import Prefs  # noqa: E402
from langmod.core.sources import Source, SourceError  # noqa: E402

LIST = b'locTable{\r\n  file:t="%lang/menu.csv"\r\n  file:t="%lang/IFN1_01_units.csv"\r\n  file:t="%lang/IFN1_99_useroverwrite.csv"\r\n}\r\n'


def ifn1_zip(version: str, name: str = "Viper") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("readme.txt", f"IFN1 V{version}")
        zf.writestr("lang/localization.blk", LIST)
        zf.writestr("lang/IFN1_01_units.csv", f"<ID|readonly|noverify>;<English>\r\nf_16a_0;{name} v{version}\r\n")
        zf.writestr("lang/IFN1_99_useroverwrite.csv", "<ID|readonly|noverify>;<English>\r\n")
    return buf.getvalue()


def live_post(group: int, file_name: str, blob_key: str, size: int, text: str, created: float = 1515500686) -> dict:
    return {"lang_group": group, "id": group + 30000, "created": created, "type": "camouflage",
            "author": {"id": 25283595, "nickname": "InFerNos1"},
            "description": f"<p><b>(not a camouflage)</b></p><p>{text}</p>",
            "file": {"id": 1, "name": file_name, "link": f"https://live.warthunder.com/dl/{blob_key}/",
                     "type": "application/zip", "size": size}}


class FakeHttp:
    def __init__(self):
        self.posts: dict[str, dict] = {}
        self.feeds: dict[str, list[dict]] = {}
        self.json: dict[str, object] = {}
        self.files: dict[str, bytes] = {}
        self.calls: list[str] = []
        self.offline = False

    def _net(self, url):
        self.calls.append(url)
        if self.offline:
            raise SourceError("could not reach the internet: offline")

    def post_form(self, url, data):
        self._net(url)
        if url.endswith("/api/posts/get/"):
            post = self.posts.get(data["lang_group"])
            return dict(post) if post else {"status": "ERROR"}          # WT Live answers with the post itself
        if url.endswith("/api/feed/get_user/"):
            posts = self.feeds.get(data["user"], [])
            page = int(data.get("page", 0))
            return {"status": "OK", "data": {"list": posts[page * 25:(page + 1) * 25]}}
        raise SourceError(f"unexpected {url}")

    def get_json(self, url, headers=None):
        self._net(url)
        for prefix, answer in self.json.items():
            if url.startswith(prefix):
                if isinstance(answer, Exception):
                    raise answer
                return answer
        raise SourceError(f"{url} said 404 Not Found")

    def download(self, url, dest, expected=0, progress=None):
        self._net(url)
        data = self.files[url]
        dest.write_bytes(data)
        if expected and expected != len(data):
            raise SourceError("the download stopped short")
        return dest


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="langmod-upd-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.http = FakeHttp()
        patcher = unittest.mock.patch.object(sources, "http", self.http)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.lib = Library(self.tmp / "home")

    def publish_ifn1(self, version: str, key: str, text_date: str = "17 September, 2026", name: str = "Viper"):
        blob = ifn1_zip(version, name)
        url = f"https://live.warthunder.com/dl/{key}/"
        self.http.files[url] = blob
        self.http.posts["694386"] = live_post(694386, f"IFN1 Langmod V{version}.zip", key, len(blob),
                                              f"IFN1 Vehicle Re-Name Mod Version {version} - {text_date}")
        return blob

    def add_zip(self, name: str, blob: bytes):
        p = self.tmp / name
        p.write_bytes(blob)
        return self.lib.add(p).mod


class Reading(unittest.TestCase):
    def test_links(self):
        self.assertEqual(sources.parse_link("https://live.warthunder.com/post/694386/en/"), Source("wtlive", "694386"))
        self.assertEqual(sources.parse_link("live.warthunder.com/user/36978671/"), Source("wtlive", "user:36978671"))
        self.assertEqual(sources.parse_link("https://github.com/Addysaurus/lang_modding.git"),
                         Source("github", "Addysaurus/lang_modding"))
        self.assertEqual(sources.parse_link("https://www.nexusmods.com/warthunder/mods/2162?tab=files"),
                         Source("nexus", "warthunder/2162"))
        self.assertIsNone(sources.parse_link("https://example.com/mod.zip"))

    def test_versions_and_dates(self):
        self.assertEqual(sources.version_of("IFN1 Langmod V76.zip"), "76")
        self.assertEqual(sources.version_of("1.3.09 LOP.zip"), "1.3.09")
        self.assertEqual(sources.version_of("LOP_1.3.08.zip"), "1.3.08")
        self.assertEqual(sources.version_of("", "Mod Version: Release 1.3.09 Game Version: 2.53.0.42"), "1.3.09")
        self.assertTrue(sources.is_newer("1.18.01", "1.17.4"))
        self.assertFalse(sources.is_newer("76", "76"))
        self.assertIsNone(sources.is_newer("76", ""))
        when = time.localtime(sources.date_in("Version 76 - 17 September, 2026 Game version"))
        self.assertEqual((when.tm_year, when.tm_mon, when.tm_mday), (2026, 9, 17))
        self.assertEqual(time.localtime(sources.date_in("posted 2026-07-24")).tm_mon, 7)
        self.assertEqual(sources.date_in("no date here"), 0.0)

    def test_suggestions(self):
        self.assertEqual(sources.suggest(["IFN1", "IFN1_"]).ref, "694386")
        self.assertEqual(sources.suggest(["1.3.09 LOP"]).ref, "user:36978671")
        self.assertEqual(sources.suggest(["WTHLM_1.18.01"]).kind, "github")
        nexus = sources.suggest(["Some Mod-4321-2-0-1773898746.zip"])
        self.assertEqual((nexus.kind, nexus.ref), ("nexus", "warthunder/4321"))
        self.assertIsNone(sources.suggest(["My Mod"]))

    def test_shared_build_leaves_nexus_out(self):
        with unittest.mock.patch.object(sources, "NEXUS", False):
            self.assertIsNone(sources.parse_link("https://www.nexusmods.com/warthunder/mods/2162"))
            self.assertIsNone(sources.suggest(["Some Mod-4321-2-0-1773898746.zip"]))
            self.assertEqual(sources.suggest(["IFN1"]).kind, "wtlive")
            self.assertEqual(sources.places(), "WT Live or GitHub")
            with self.assertRaises(SourceError):
                sources.latest(Source("nexus", "warthunder/2162"), "key")


class Sources(Base):
    def test_wtlive_post_whose_file_is_swapped(self):
        self.publish_ifn1("76", "aaa111")
        src = Source("wtlive", "694386")
        rel = sources.latest(src)
        self.assertEqual((rel.version, rel.key, rel.file_name), ("76", "wtlive:aaa111", "IFN1 Langmod V76.zip"))
        self.assertEqual(src.label, "WT Live · InFerNos1")
        self.assertGreater(rel.published, 1.7e9)               # the date written in the post, not 2018's
        self.http.posts.clear()
        with self.assertRaises(SourceError):
            sources.latest(src)

    def test_wtlive_author_with_a_post_per_version(self):
        feed = [live_post(1161765, "1.3.09 LOP.zip", "k9", 10, "Mod Version: Release 1.3.09", created=1767933462),
                {**live_post(1, "", "x", 0, "an image post"), "file": None},
                live_post(1148727, "LOP_1.3.08.zip", "k8", 10, "Mod Version: Release 1.3.08", created=1755334119),
                live_post(999, "Another Mod V99.zip", "o99", 10, "not this one", created=1769000000)]
        self.http.feeds["36978671"] = feed
        rel = sources.latest(Source("wtlive", "user:36978671", "LOP"))
        self.assertEqual((rel.version, rel.key), ("1.3.09", "wtlive:k9"))
        self.assertEqual(sources.latest(Source("wtlive", "user:36978671", "Another")).version, "99")

    def test_github_skips_prereleases(self):
        self.http.json["https://api.github.com/repos/Addysaurus/lang_modding/releases"] = [
            {"tag_name": "1.19.00d", "prerelease": True, "assets": []},
            {"tag_name": "1.18.01", "prerelease": False, "draft": False, "published_at": "2026-07-24T21:06:19Z",
             "html_url": "https://github.com/Addysaurus/lang_modding/releases/tag/1.18.01", "id": 5,
             "assets": [{"id": 77, "name": "WTHLM_1.18.01.zip", "size": 410793,
                         "browser_download_url": "https://github.com/x/WTHLM_1.18.01.zip"}]}]
        src = Source("github", "Addysaurus/lang_modding")
        rel = sources.latest(src)
        self.assertEqual((rel.version, rel.key, rel.size), ("1.18.01", "github:77", 410793))
        self.assertEqual(src.label, "GitHub · Addysaurus")

    def test_nexus_key_premium_and_not(self):
        src = Source("nexus", "warthunder/2162")
        with self.assertRaises(SourceError):
            sources.latest(src)                                   # no key
        self.http.json[f"{sources.NEXUS_API}/games/warthunder/mods/2162/files.json"] = {"files": [
            {"file_id": 10, "name": "IFN1 Langmod", "file_name": "IFN1 Langmod V75.zip", "version": "75",
             "uploaded_timestamp": 1780000000, "size_kb": 690},
            {"file_id": 11, "name": "IFN1 Langmod", "file_name": "IFN1 Langmod V76.zip", "version": "76",
             "uploaded_timestamp": 1790000000, "size_kb": 700}]}
        self.http.json[f"{sources.NEXUS_API}/users/validate.json"] = {"is_premium": False}
        rel = sources.latest(src, "key")
        self.assertEqual((rel.version, rel.key, rel.downloadable), ("76", "nexus:11", False))
        self.assertIn("Premium", rel.note)
        self.assertIn("file_id=11", rel.page)
        self.http.json[f"{sources.NEXUS_API}/users/validate.json"] = {"is_premium": True}
        self.http.json[f"{sources.NEXUS_API}/games/warthunder/mods/2162/files/11/download_link.json"] = [
            {"URI": "https://cdn.example/IFN1.zip"}]
        self.assertEqual(sources.latest(src, "key").url, "https://cdn.example/IFN1.zip")


class Updating(Base):
    def test_follow_check_install_and_keep_edits(self):
        mod = self.add_zip("lang (2).zip", ifn1_zip("75"))
        self.assertEqual(updates.auto_follow(self.lib, mod).ref, "694386")
        self.assertIsNone(updates.auto_follow(self.lib, mod))                   # already follows
        self.lib.update_file(mod, "IFN1_99_useroverwrite.csv", b"<ID|readonly|noverify>;<English>\r\nmine;Mine\r\n")
        self.publish_ifn1("76", "aaa111", text_date="17 September, 2030")
        updates.check(self.lib, mod)
        self.assertEqual(updates.judge(mod), ("update", "date"))                 # its files are older
        done = updates.install(self.lib, mod)
        mod = self.lib.get(mod.id)
        self.assertEqual((mod.version, mod.installed_key), ("76", "wtlive:aaa111"))
        self.assertIn(b"v76", self.lib.read_file(mod, "IFN1_01_units.csv"))
        self.assertIn(b"Mine", self.lib.read_file(mod, "IFN1_99_useroverwrite.csv"))   # the player's edit
        self.assertTrue(any("kept your edited" in n.lower() for n in done.result.notes))
        self.assertEqual(updates.judge(mod), ("current", "file"))
        self.assertFalse((self.lib.root / "downloads" / "IFN1 Langmod V76.zip").exists())
        self.publish_ifn1("77", "bbb222")                                        # the author swaps the file
        updates.check(self.lib, mod)
        self.assertEqual(updates.judge(mod), ("update", "file"))
        again = Library(self.lib.root).get(mod.id)                               # all of it is saved
        self.assertEqual(again.installed_key, "wtlive:aaa111")
        self.assertEqual(updates.latest_of(again).version, "77")

    def test_judging_by_version_and_date(self):
        mod = self.add_zip("IFN1 Re-Name Mod-2162-74-1-1773898746.zip", ifn1_zip("74"))
        self.assertEqual(mod.version, "74.1")
        updates.follow(self.lib, mod, Source("wtlive", "694386"))
        self.publish_ifn1("76", "k")
        updates.check(self.lib, mod)
        self.assertEqual(updates.judge(mod), ("update", "version"))
        mod.version = ""
        mod.stamp = time.strftime("%Y-%m-%d", time.localtime(updates.latest_of(mod).published - 86400))
        self.assertEqual(updates.judge(mod), ("current", "date"))                # a day before: the same release
        updates.mark_current(self.lib, mod)
        self.assertEqual(updates.judge(mod), ("current", "file"))

    def test_same_zip_is_recognised(self):
        blob = self.publish_ifn1("76", "k76")
        mod = self.add_zip("IFN1 Langmod V76.zip", blob)
        updates.auto_follow(self.lib, mod)
        updates.check(self.lib, mod)
        self.assertEqual(mod.installed_key, "wtlive:k76")

    def test_offline_and_missing(self):
        mod = self.add_zip("lang.zip", ifn1_zip("75"))
        updates.auto_follow(self.lib, mod)
        self.http.offline = True
        self.assertEqual(updates.check_all(self.lib), [])
        self.assertEqual(updates.state(mod), "error")
        self.assertIn("offline", mod.check_error)
        self.http.offline = False
        self.publish_ifn1("76", "k")
        updates.check(self.lib, mod)
        self.assertNotEqual(updates.state(mod), "error")

    def test_answers_for_a_mod_that_changed_meanwhile_are_dropped(self):
        """A check or a download runs in the background; the player may change the mod meanwhile."""
        mod = self.add_zip("lang.zip", ifn1_zip("75"))
        updates.auto_follow(self.lib, mod)
        self.publish_ifn1("76", "k76")
        found = updates.look(mod.id, updates.source_of(mod))
        updates.follow(self.lib, mod, Source("github", "Addysaurus/lang_modding"))    # now follows elsewhere
        self.assertIsNone(updates.record(self.lib, found))
        self.assertIsNone(mod.latest)
        updates.follow(self.lib, mod, Source("wtlive", "694386", "IFN1"))
        found = updates.look(mod.id, updates.source_of(mod))
        self.assertEqual(updates.record(self.lib, found).version, "76")
        path = updates.fetch(updates.latest_of(mod), updates.downloads(self.lib))
        self.lib.remove(mod.id)                                                       # removed meanwhile
        with self.assertRaises(SourceError):
            updates.take(self.lib, mod.id, updates.latest_of(mod), path)
        self.assertFalse(path.exists())                                               # no download left over
        self.assertIsNone(updates.record(self.lib, found))

    def test_looking_changes_nothing_shared(self):
        mod = self.add_zip("lang.zip", ifn1_zip("75"))
        updates.auto_follow(self.lib, mod)
        self.publish_ifn1("76", "k76")
        before = Path(self.lib.state_path).read_bytes()
        src = updates.source_of(mod)
        found = updates.look(mod.id, src)
        self.assertEqual(found.release.version, "76")
        self.assertEqual(Path(self.lib.state_path).read_bytes(), before)             # nothing saved
        self.assertIsNone(mod.latest)
        self.assertEqual(src.label, updates.source_of(mod).label)                   # nor the caller's source

    def test_when_checks_are_due(self):
        prefs = Prefs(self.lib)
        self.assertFalse(updates.due(self.lib))                  # nothing follows anywhere
        mod = self.add_zip("lang.zip", ifn1_zip("75"))
        updates.auto_follow(self.lib, mod)
        self.assertTrue(updates.due(self.lib))
        prefs.set("update_last", time.time())
        self.assertFalse(updates.due(self.lib))
        prefs.set("update_last", time.time() - 2 * 3600)
        self.assertTrue(updates.due(self.lib))
        prefs.set("update_check", "daily")
        self.assertFalse(updates.due(self.lib))
        prefs.set("update_check", "never")
        prefs.set("update_last", 0.0)
        self.assertFalse(updates.due(self.lib))


class Launching(Base):
    def run_cli(self, *args: str) -> str:
        from langmod import cli
        out = io.StringIO()
        with unittest.mock.patch.object(cli, "start_game", return_value="steam://rungameid/236390") as start, \
                unittest.mock.patch.object(cli, "game_running", return_value=False), \
                contextlib.redirect_stdout(out):
            self.assertEqual(cli.main(["--home", str(self.lib.root), "--game", str(self.game), *args]), 0)
        self.starts = start.call_count
        return out.getvalue()

    def setUp(self):
        super().setUp()
        self.game = build(self.tmp / "War Thunder")

    def test_launch_installs_updates_first_when_allowed(self):
        mod = self.add_zip("lang.zip", ifn1_zip("75"))
        updates.auto_follow(self.lib, mod)
        self.publish_ifn1("76", "k76", text_date="17 September, 2030")
        out = self.run_cli("launch")
        self.assertIn("updates available: IFN1", out)             # told, not installed
        self.assertEqual(self.starts, 1)
        Prefs(Library(self.lib.root)).set("update_install", "auto")
        Prefs(Library(self.lib.root)).set("update_last", 0.0)
        out = self.run_cli("launch")
        self.assertIn("updated IFN1 to 76", out)
        self.assertIn(b"v76", (self.game / "lang" / "IFN1_01_units.csv").read_bytes())
        self.assertEqual(self.starts, 1)

    def test_launch_offline_still_starts(self):
        mod = self.add_zip("lang.zip", ifn1_zip("75"))
        updates.auto_follow(self.lib, mod)
        Prefs(self.lib).set("update_install", "auto")
        self.http.offline = True
        self.run_cli("launch")
        self.assertEqual(self.starts, 1)

    def test_cli_follow_updates_update(self):
        mod = self.add_zip("My Mod.zip", ifn1_zip("75"))
        self.lib.get(mod.id).follow = None
        self.lib.save()
        out = self.run_cli("follow", mod.id, "https://live.warthunder.com/post/694386/en/")
        self.assertIn("now follows", out)
        self.publish_ifn1("76", "k", text_date="1 January, 2031")
        self.assertIn("update available", self.run_cli("updates"))
        self.assertIn("UPDATE AVAILABLE", self.run_cli("list"))
        self.assertIn("updated", self.run_cli("update"))
        self.assertIn("up to date", self.run_cli("updates"))


class RealHttp(unittest.TestCase):
    """The real network code against a server on this machine that cuts off, stalls or answers oddly."""

    @classmethod
    def setUpClass(cls):
        import http.server
        import threading

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                body = {"/ok": b"hello", "/cut": b"x" * 1000}.get(self.path, b"")
                self.send_response(200)
                self.send_header("Content-Length", "100000" if self.path == "/cut" else str(len(body) or 10))
                self.end_headers()
                if self.path == "/stall":
                    time.sleep(1.5)
                self.wfile.write(body)
                if self.path == "/cut":
                    self.wfile.flush()
                    self.connection.shutdown(2)

        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="langmod-http-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        patcher = unittest.mock.patch.object(sources, "TIMEOUT", 0.5)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_download_that_stops_leaves_nothing_behind(self):
        self.assertEqual(sources.Http().download(self.base + "/ok", self.tmp / "ok.zip").read_bytes(), b"hello")
        for path in ("/cut", "/stall"):                      # closed early; no more data before the timeout
            with self.subTest(path), self.assertRaises(SourceError):
                sources.Http().download(self.base + path, self.tmp / "mod.zip")
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir()), ["ok.zip"])

    def test_an_answer_that_stalls_or_has_another_shape_is_a_failed_check(self):
        with self.assertRaises(SourceError):
            sources.Http().get_json(self.base + "/stall")
        with unittest.mock.patch.object(sources.http, "post_form", return_value=[1, 2]):
            found = updates.look("x", Source("wtlive", "user:1"))
        self.assertIn("does not understand", found.error)


if __name__ == "__main__":
    unittest.main()
