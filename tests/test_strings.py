"""Strings, picks, profiles, the game-text cache, getting mods and the bug report. No Qt.

Everything works in a temporary folder with the fake game; the network is
the fake one from ``test_updates``.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fakegame import build  # noqa: E402
from test_updates import FakeHttp, ifn1_zip, live_post  # noqa: E402
from langmod.core import catalog, profiles, report, sources, strings, updates  # noqa: E402
from langmod.core import install as inst  # noqa: E402
from langmod.core.analysis import analyze  # noqa: E402
from langmod.core.game import GameInstall, GameLang  # noqa: E402
from langmod.core.langcsv import read_table  # noqa: E402
from langmod.core.library import PERSONAL_FILE, Library  # noqa: E402
from langmod.core.plan import GAME, PICKS_FILE, PICKS_ID, build_plan, plan_files  # noqa: E402


def csv(text: str) -> bytes:
    return text.replace("\n", "\r\n").encode("utf-8")


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="langmod-str-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.game_root = build(self.tmp / "War Thunder")
        self.lib = Library(self.tmp / "home")
        self.game = GameLang(self.game_root, cache_dir=self.lib.root / "cache")

    def mod(self, name: str, files: dict[str, bytes]):
        d = self.tmp / name
        d.mkdir(parents=True)
        for n, data in files.items():
            (d / n).write_bytes(data)
        return self.lib.add(d).mod

    def two(self):
        a = self.mod("Alpha Names", {"AL_units.csv": csv(
            "<ID|readonly|noverify>;<English>;<French>\nt_34_85;T-34 (A);T-34 (A fr)\nf_16a_0;Viper\n")})
        b = self.mod("Bravo Names", {"BR_units.csv": csv(
            "<ID|readonly|noverify>;<English>\nt_34_85;T-34 (B)\nmod_only_key;Only in Bravo\n")})
        return a, b

    def shown(self, key: str, language: str = "English"):
        plan = build_plan(self.lib, self.game)
        return analyze(self.lib, self.game, plan, language).shown(key), plan


class Picking(Case):
    def test_pick_a_mod_or_the_game_over_the_load_order(self):
        a, b = self.two()
        (source, text), _ = self.shown("t_34_85")
        self.assertEqual((source, text), (b.id, "T-34 (B)"))              # the load order: the last wins
        strings.pick(self.lib, "t_34_85", a.id)
        (source, text), plan = self.shown("t_34_85")
        self.assertEqual((source, text), (a.id, "T-34 (A)"))
        self.assertEqual(plan.picks, {"t_34_85": a.id})
        files = plan_files(self.lib, plan)
        picks = read_table(files[PICKS_FILE])
        self.assertEqual(picks.columns, ["ID|readonly|noverify", "English", "French"])   # its own columns
        self.assertEqual(picks.rows, [["t_34_85", "T-34 (A)", "T-34 (A fr)"]])
        self.assertEqual(plan.loc_table[-1], f"%lang/{PICKS_FILE}")                     # after every mod
        an = analyze(self.lib, self.game, plan)
        c = next(c for c in an.conflicts if c.key == "t_34_85")
        self.assertEqual((c.winner, c.picked), (a.id, a.id))
        self.assertEqual([s for s, _t in an.sources("t_34_85")], [GAME, a.id, b.id])

        strings.pick(self.lib, "t_34_85", GAME)                             # back to the game's own
        (source, text), plan = self.shown("t_34_85")
        self.assertEqual((source, text), (GAME, "T-34-85"))
        self.assertEqual(read_table(plan_files(self.lib, plan)[PICKS_FILE]).rows[0][:3],
                         ["t_34_85", "T-34-85", "T-34-85"])
        # A pick follows the mod: a new version of it brings its new text.
        strings.pick(self.lib, "t_34_85", a.id)
        self.lib.update_file(a, "AL_units.csv", csv("<ID|readonly|noverify>;<English>\nt_34_85;T-34 (A2)\n"))
        self.assertEqual(self.shown("t_34_85")[0], (a.id, "T-34 (A2)"))
        # Switched off, a picked mod still gives its text; removed, the pick waits.
        self.lib.set_enabled(a.id, False)
        self.assertEqual(self.shown("t_34_85")[0], (a.id, "T-34 (A2)"))
        self.lib.remove(a.id)
        (source, _text), plan = self.shown("t_34_85")
        self.assertEqual(source, b.id)
        self.assertIn("so that pick waits", plan.reports[PICKS_ID].notes[0])
        self.assertTrue(strings.unpick(self.lib, "t_34_85"))
        self.assertNotIn(PICKS_ID, build_plan(self.lib, self.game).reports)

    def test_own_text_wins_over_picks_and_keeps_to_its_language(self):
        a, b = self.two()
        strings.pick(self.lib, "t_34_85", a.id)
        self.assertEqual(strings.own_text(self.lib, "t_34_85", "English"), None)
        strings.set_own_text(self.lib, "t_34_85", "English", 'Mine "quoted"; with a semicolon')
        own = self.lib.personal()
        self.assertEqual(self.lib.mods[-1].id, own.id)
        (source, text), plan = self.shown("t_34_85")
        self.assertEqual((source, text), (own.id, 'Mine "quoted"; with a semicolon'))
        self.assertLess(plan.loc_table.index(f"%lang/{PICKS_FILE}"), plan.loc_table.index(f"%lang/{PERSONAL_FILE}"))
        # French goes in a file of its own, so no English text is blanked by it.
        name = strings.set_own_text(self.lib, "t_34_85", "French", "Le mien")
        self.assertEqual(name, "zz_my_changes_french.csv")
        self.assertEqual(read_table(self.lib.read_file(own, name)).columns, ["ID|readonly|noverify", "French"])
        self.assertEqual(self.shown("t_34_85", "French")[0], (own.id, "Le mien"))
        self.assertEqual(self.shown("t_34_85", "English")[0][1], 'Mine "quoted"; with a semicolon')
        strings.set_own_text(self.lib, "t_34_85", "English", "Changed my mind")
        table = read_table(self.lib.read_file(own, PERSONAL_FILE))
        self.assertEqual(table.rows, [["t_34_85", "Changed my mind"]])       # replaced, not added twice
        self.assertTrue(strings.clear_own_text(self.lib, "t_34_85", "English"))
        self.assertEqual(self.shown("t_34_85")[0], (a.id, "T-34 (A)"))       # the pick shows again

    def test_own_text_keeps_what_its_file_already_had(self):
        own = self.lib.personal()
        self.lib.update_file(own, "zz_my_changes_polish.csv",
                             b'"<ID|readonly|noverify>";"<Comments>"\r\n"k1";"a note"\r\n')
        strings.set_own_text(self.lib, "k2", "Polish", "Mój")
        table = read_table(self.lib.read_file(own, "zz_my_changes_polish.csv"))
        self.assertEqual(table.columns, ["ID|readonly|noverify", "Comments", "Polish"])
        self.assertEqual(table.rows, [["k1", "a note", ""], ["k2", "", "Mój"]])

    def test_search(self):
        a, b = self.two()
        an = analyze(self.lib, self.game, build_plan(self.lib, self.game))
        keys, total = an.search("f-16")
        self.assertEqual((keys, total), (["f_16a_0"], 1))                   # the game's text has it
        self.assertEqual(an.search("VIPER")[0], ["f_16a_0"])                 # a mod's text, any case
        self.assertEqual(an.search("mod_only")[0], ["mod_only_key"])        # a key only a mod has
        self.assertEqual(an.search("t_34_85")[0][0], "t_34_85")             # the exact key first
        self.assertEqual(an.search("   "), ([], 0))
        keys, total = an.search("_", limit=2)
        self.assertEqual(len(keys), 2)
        self.assertGreater(total, 2)


class GameTextCache(Case):
    def test_read_once_per_game_version(self):
        texts = self.game.texts("English")
        self.assertEqual(texts["t_34_85"][0], "T-34-85")
        self.assertEqual(texts["event_name"][1], "%langRegional/special_events.csv")
        cached = list((self.lib.root / "cache").glob("texts-*.json"))
        self.assertEqual(len(cached), 1)
        again = GameLang(self.game_root, cache_dir=self.lib.root / "cache")
        with unittest.mock.patch.object(GameLang, "table", side_effect=AssertionError("read the tables")):
            self.assertEqual(again.texts("English")["t_34_85"][0], "T-34-85")
        self.assertEqual(again.row("f_16a_0"), (["ID|readonly|noverify", "English", "French"],
                                                ["f_16a_0", "F-16A Fighting Falcon", "F-16A"]))
        # A game update is another game: read anew, and a damaged cache is simply read past.
        build(self.game_root, version=(2, 60, 0, 1))
        newer = GameLang(self.game_root, cache_dir=self.lib.root / "cache")
        self.assertNotEqual(newer._cache_path("English"), again._cache_path("English"))
        newer._cache_path("English").write_text("{not json", "utf-8")
        self.assertEqual(newer.texts("English")["t_34_85"][0], "T-34-85")
        for i in range(10):
            GameLang(self.game_root, cache_dir=self.lib.root / "cache").texts(f"Lang{i}")
        self.assertLessEqual(len(list((self.lib.root / "cache").glob("texts-*.json"))), 6)


class Profiles(Case):
    def test_save_switch_and_keep_up(self):
        a, b = self.two()
        profiles.save_as(self.lib, "Both")
        profiles.save_as(self.lib, "Alpha only")               # the new one is in use: changes go to it
        self.lib.set_enabled(b.id, False)
        strings.pick(self.lib, "t_34_85", GAME)
        self.assertEqual(profiles.names(self.lib), ["Both", "Alpha only"])
        self.lib.move(a.id, 1)                                  # changes follow the profile in use
        profiles.switch(self.lib, "Both")
        self.assertEqual([m.enabled for m in self.lib.mods], [True, True])
        self.assertEqual([m.id for m in self.lib.mods], [a.id, b.id])
        self.assertEqual(strings.picks(self.lib), {})
        c = self.mod("Charlie", {"CH_x.csv": csv("<ID|readonly|noverify>;<English>\nx;y\n")})
        profiles.switch(self.lib, "Alpha only")
        self.assertEqual([m.id for m in self.lib.mods], [b.id, a.id, c.id])
        self.assertEqual({m.id: m.enabled for m in self.lib.mods}, {a.id: True, b.id: False, c.id: False})
        self.assertEqual(strings.picks(self.lib), {"t_34_85": GAME})
        self.assertEqual(profiles.active(self.lib), "Alpha only")
        with self.assertRaises(profiles.ProfileError):
            profiles.save_as(self.lib, " both ")
        self.assertEqual(profiles.rename(self.lib, "Alpha only", "Just A"), "Just A")
        self.assertEqual(profiles.active(self.lib), "Just A")
        profiles.delete(self.lib, "Just A")
        self.assertIsNone(profiles.active(self.lib))
        self.assertEqual(profiles.names(Library(self.lib.root)), ["Both"])          # kept in library.json

    def test_export_and_import_to_a_friend(self):
        http = FakeHttp()
        patcher = unittest.mock.patch.object(sources, "http", http)
        patcher.start()
        self.addCleanup(patcher.stop)
        a, b = self.two()
        ifn1 = self.tmp / "lang.zip"
        ifn1.write_bytes(ifn1_zip("75"))
        ifn = self.lib.add(ifn1).mod
        updates.follow(self.lib, ifn, sources.Source("wtlive", "694386", "IFN1"))
        strings.set_own_text(self.lib, "t_34_85", "English", "My tank")
        strings.pick(self.lib, "f_16a_0", a.id)
        self.lib.set_enabled(b.id, False)
        profiles.save_as(self.lib, "Mine")
        path = profiles.export(self.lib, "Mine", self.tmp / profiles.file_name("Mine"))
        self.assertEqual(path.name, "Mine.langmod-profile")
        doc = json.loads(path.read_text("utf-8"))
        self.assertEqual([m["name"] for m in doc["mods"]], ["Alpha Names", "Bravo Names", "IFN1", "My changes"])
        self.assertIn("My tank", doc["mods"][-1]["files"][PERSONAL_FILE])

        friend = Library(self.tmp / "friend")
        other = self.tmp / "Alpha Names"            # the friend has Alpha, under another id
        friend.add(other)
        (friend.root / "mods" / friend.mods[0].id).rename(friend.root / "mods" / "alpha")
        friend.mods[0].id = "alpha"
        friend.save()
        read = profiles.read_file(path)
        have = {e.name: e.have for e in profiles.entries(friend, read)}
        self.assertEqual(have["Alpha Names"].id, "alpha")
        self.assertIsNone(have["IFN1"])
        # IFN1 follows a page, so it can be fetched first, as the window does.
        blob = ifn1_zip("76")
        http.files["https://live.warthunder.com/dl/k76/"] = blob
        http.posts["694386"] = live_post(694386, "IFN1 Langmod V76.zip", "k76", len(blob), "Version 76")
        e = next(e for e in profiles.entries(friend, read) if e.name == "IFN1")
        found = updates.look(e.name, e.source)
        got = updates.take_new(friend, e.source, found.release, updates.fetch(found.release, updates.downloads(friend)))
        self.assertEqual(updates.judge(got.mod), ("current", "file"))
        name, missing = profiles.take(friend, read)
        self.assertEqual((name, missing), ("Mine", ["Bravo Names"]))
        self.assertEqual(profiles.active(friend), "Mine")
        strings_mod = next(m for m in friend.mods if m.name == "Mine strings")
        self.assertEqual([m.id for m in friend.mods if m.enabled], ["alpha", got.mod.id, strings_mod.id])
        self.assertEqual(strings.picks(friend), {"f_16a_0": "alpha"})
        game = GameLang(self.game_root)
        an = analyze(friend, game, build_plan(friend, game))
        self.assertEqual(an.shown("t_34_85"), (strings_mod.id, "My tank"))
        self.assertEqual(an.shown("f_16a_0"), ("alpha", "Viper"))
        self.assertEqual(profiles.take(friend, read)[0], "Mine (2)")        # a second import is another one
        bad = self.tmp / "bad.langmod-profile"
        bad.write_text('{"hello": 1}', "utf-8")
        with self.assertRaises(profiles.ProfileError):
            profiles.read_file(bad)


class GettingMods(Case):
    def test_catalog(self):
        self.assertEqual([e.key for e in catalog.CATALOG], ["ifn1", "lop", "wthlm"])
        self.assertIsNone(catalog.installed(self.lib, catalog.CATALOG[0]))
        z = self.tmp / "lang.zip"
        z.write_bytes(ifn1_zip("75"))
        mod = self.lib.add(z).mod                                    # by hand, following nothing yet
        self.assertIs(catalog.installed(self.lib, catalog.CATALOG[0]), mod)
        rel = sources.Release("76", "IFN1.zip", "u", 1, 0, "k", "p",
                              title="IFN1 Vehicle Re-Name Mod Version 76 Game version: Updated to 2.59.0 .9")
        self.assertEqual(catalog.made_for(rel), "2.59")
        rel.title = "Localization Overhaul Project Mod Version: Release 1.3.09 Game Version: 2.53.0.42"
        self.assertEqual(catalog.made_for(rel), "2.53")
        rel.title = "no version said"
        self.assertEqual(catalog.made_for(rel), "")


class BugReport(Case):
    def test_says_what_helps_and_nothing_personal(self):
        a, _b = self.two()
        install = GameInstall(self.game_root, "custom")
        (self.lib.root / "errors.log").write_text(f"Traceback in {Path.home() / 'x.py'}\nValueError: boom\n", "utf-8")
        text = report.bug_report(self.lib, install, self.game, inst.status(install))
        self.assertIn("Langmod Manager bug report", text)
        self.assertIn("Game version: 2.59.0.13", text)
        self.assertIn("1. [x] Alpha Names: 1 files", text)
        self.assertIn("ValueError: boom", text)
        self.assertNotIn(str(Path.home()), text)
        self.assertIn("%USERPROFILE%", text)
        self.assertIn("Game: not found", report.bug_report(Library(self.tmp / "empty")))


if __name__ == "__main__":
    unittest.main()
