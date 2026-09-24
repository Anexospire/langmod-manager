"""Core tests: no Qt, no real game folder is ever written.

Everything that writes works in a temporary folder with a small fake game
(see ``fakegame.py``). The IFN1 tests at the end read the real mod and the
real game when they are on this machine, and are skipped otherwise.
"""
from __future__ import annotations

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

from fakegame import CONFIG, build, csv_bytes, UNITS  # noqa: E402
from langmod.core import blk, config, install  # noqa: E402
from langmod.core.analysis import analyze  # noqa: E402
from langmod.core import library as library_module  # noqa: E402
from langmod.core.game import GameInstall, GameLang, lang_stamp  # noqa: E402
from langmod.core.langcsv import is_noise, read_table, write_table  # noqa: E402
from langmod.core.library import Library, LibraryError, clean_label, file_prefix, read_package  # noqa: E402
from langmod.core.plan import build_plan, overlay_rows, plan_files  # noqa: E402


def mod_list(*files: str, extra: str = "") -> bytes:
    """A mod's own localization.blk, frozen at an older game: it lacks inf.csv."""
    lines = [extra, "locTable{", '  file:t="%lang/menu.csv"', '  file:t="%lang/units.csv"',
             '  file:t="%lang/_legal.csv"']
    lines += [f'  file:t="%lang/{f}"' for f in files]
    lines.append("}")
    return "\r\n".join(lines).encode()


def plain_csv(text: str) -> bytes:
    return text.replace("\n", "\r\n").encode("utf-8")


class Temp(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="langmod-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def folder(self, name: str, files: dict[str, bytes]) -> Path:
        d = self.tmp / name
        for rel, data in files.items():
            p = d / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
        return d

    def zip(self, name: str, files: dict[str, bytes]) -> Path:
        p = self.tmp / name
        with zipfile.ZipFile(p, "w") as zf:
            for rel, data in files.items():
                zf.writestr(rel, data)
        return p


class BlkText(unittest.TestCase):
    def test_reads_comments_blocks_and_escapes(self):
        root, problems = blk.parse_text(
            '// comment\nname:t="a~"b"  // trailing\n/* block\ncomment */\n'
            'locTable{\n  file:t="%lang/x.csv"\n  //file:t="%lang/off.csv"\n  file:t="%lang/y.csv"\n}\n'
            '"quoted name":i=3; flag:b=yes\n1{ en-US:t="English" }\n')
        self.assertEqual(problems, [])
        self.assertEqual(root.value("name"), 'a"b')
        self.assertEqual(root.block("locTable").values("file"), ["%lang/x.csv", "%lang/y.csv"])
        self.assertEqual(root.value("quoted name"), 3)
        self.assertIs(root.value("flag"), True)
        self.assertEqual(root.block("1").value("en-US"), "English")

    def test_bad_lines_are_skipped_not_fatal(self):
        root, problems = blk.parse_text('a:t="ok"\nthis line is nonsense\nb:i=notanumber\nc:t="fine"\n')
        self.assertEqual(root.value("a"), "ok")
        self.assertEqual(root.value("c"), "fine")
        self.assertEqual(len(problems), 2)

    def test_unclosed_block_is_reported(self):
        root, problems = blk.parse_text('locTable{\n  file:t="%lang/a.csv"\n')
        self.assertEqual(root.block("locTable").values("file"), ["%lang/a.csv"])
        self.assertTrue(any("never closed" in p for p in problems))

    def test_round_trip(self):
        text = 'a:t="x~y"\nb:b=no\nc:i=-4\nd:r=0.5\nblock{\n  e:p2=1.0, 2.0\n  inner{\n    f:t="%lang/z.csv"\n  }\n}\n'
        root, _ = blk.parse_text(text)
        again, problems = blk.parse_text(blk.to_text(root))
        self.assertEqual(problems, [])
        self.assertEqual(again, root)


class ConfigSwitch(unittest.TestCase):
    def test_adds_the_line_inside_debug(self):
        out = config.set_test_localization(CONFIG.replace("\n", "\r\n"), True)
        self.assertTrue(config.test_localization(out))
        self.assertIn("  screenshotAsJpeg:b=yes\r\n  testLocalization:b=yes\r\n}", out)
        self.assertEqual(out.replace("  testLocalization:b=yes\r\n", ""), CONFIG.replace("\n", "\r\n"))

    def test_flips_an_existing_line_only(self):
        text = 'video{\n  testLocalization:b=yes\n}\ndebug{\n  testLocalization:b=no\n}\n'
        self.assertFalse(config.test_localization(text))    # the one in video{} does not count
        out = config.set_test_localization(text, True)
        self.assertEqual(out, text.replace("b=no", "b=yes"))
        self.assertEqual(config.set_test_localization(out, False), text)

    def test_adds_a_debug_block_when_there_is_none(self):
        out = config.set_test_localization('language:t="English"', True)
        self.assertTrue(config.test_localization(out))
        self.assertTrue(out.startswith('language:t="English"\n'))
        self.assertEqual(config.set_test_localization('language:t="English"', False), 'language:t="English"')

    def test_one_line_debug_block(self):
        out = config.set_test_localization("debug{ netLogerr:b=yes }\n", True)
        self.assertTrue(config.test_localization(out))
        self.assertEqual(config.game_language('language:t="German"\n'), "German")

    def test_comments_around_the_switch(self):
        text = "debug{\r\n  /* a { brace } */\r\n  testLocalization:b=yes // for IFN1\r\n}\r\n"
        self.assertTrue(config.test_localization(text))
        out = config.set_test_localization(text, False)
        self.assertEqual(out, text.replace("b=yes", "b=no"))            # that line, its comment kept
        self.assertFalse(config.test_localization(out))


class LangCsv(unittest.TestCase):
    def test_loose_mod_rows(self):
        data = plain_csv('<ID|readonly|noverify>;<English>\n'
                         'This file adds new entries.;\n;\n-- Republic of China;\n'
                         'j_16_0;AVIC | J16 ""Qian Lang""\n'
                         'oh_58d_0;"<color=#9EB0D3>Bell</color>\nOH-58D"\n'
                         'air_defence/x_0;Light AA Gun;12.7-mm M2 HB;\n'
                         'lonely_key\n')
        t = read_table(data)
        self.assertEqual(t.columns, ["ID|readonly|noverify", "English"])
        texts = t.texts()
        self.assertEqual(texts["j_16_0"], 'AVIC | J16 ""Qian Lang""')
        self.assertEqual(texts["oh_58d_0"], "<color=#9EB0D3>Bell</color>\r\nOH-58D")
        self.assertEqual(texts["air_defence/x_0"], "Light AA Gun")
        self.assertEqual(texts["lonely_key"], "")
        self.assertEqual(t.problems, [])
        self.assertTrue(is_noise("This file adds new entries.") and is_noise("") and is_noise("-- Republic of China"))
        self.assertFalse(is_noise("air_defence/x_0"))

    def test_unterminated_quote_is_reported(self):
        t = read_table(plain_csv('<ID|readonly|noverify>;<English>\na;"never closed\nb;two\nc;three\n'))
        self.assertTrue(any("never closed" in p for p in t.problems))

    def test_bom_and_bad_bytes(self):
        t = read_table(b"\xef\xbb\xbf<ID|readonly|noverify>;<English>\r\nk;caf\xe9\r\n")
        self.assertEqual(t.columns[1], "English")
        self.assertTrue(t.problems)

    def test_write_round_trip(self):
        rows = [["k1", 'say "hi"', "x"], ["k2", "two\r\nlines", ""]]
        t = read_table(write_table(["ID|readonly|noverify", "English", "French"], rows))
        self.assertEqual(t.rows, rows)


class Packages(Temp):
    def test_nexus_names(self):
        self.assertEqual(clean_label("IFN1 Re-Name Mod-2162-74-1-1773898746.zip"), ("IFN1 Re-Name Mod", "74.1"))
        self.assertEqual(clean_label("Cool Mod v2.3.zip"), ("Cool Mod", "2.3"))
        self.assertEqual(clean_label("WTHLM_1.19.00.zip"), ("WTHLM", "1.19.00"))
        self.assertEqual(clean_label("Tiger 1.0 (2).7z"), ("Tiger", "1.0"))
        self.assertEqual(clean_label("1.3.09 LOP.zip"), ("LOP", "1.3.09"))
        for whole in ("IFN1.zip", "Leopard 2A6 names.zip", "Mod 2.zip"):
            self.assertEqual(clean_label(whole), (whole[:-4], ""))
        self.assertEqual(file_prefix(["IFN1_01_units.csv", "IFN1_02_x.csv", "localization.blk"]), "IFN1_")

    def test_finds_the_lang_folder_inside(self):
        z = self.zip("Mod.zip", {"Mod v1/readme.txt": b"hello", "Mod v1/lang/localization.blk": mod_list(),
                                 "Mod v1/lang/AB_a.csv": b"x", "Mod v1/extras/other.csv": b"y"})
        pkg = read_package(z)
        self.assertEqual(sorted(pkg.files), ["AB_a.csv", "localization.blk"])
        self.assertEqual(pkg.readme, "hello")
        self.assertTrue(any("extras" in n for n in pkg.notes))

    def test_no_language_files(self):
        from langmod.core.library import LibraryError
        with self.assertRaises(LibraryError):
            read_package(self.zip("empty.zip", {"readme.txt": b"hi"}))


class LibraryFlow(Temp):
    def setUp(self):
        super().setUp()
        self.lib = Library(self.tmp / "home")

    def v1(self):
        return self.zip("lang.zip", {
            "lang/localization.blk": mod_list("AB_01_units.csv", "AB_80_module.csv", "AB_99_user.csv"),
            "lang/AB_01_units.csv": plain_csv("<ID|readonly|noverify>;<English>\nf_16a_0;Viper v1\n"),
            "lang/AB_99_user.csv": plain_csv("<ID|readonly|noverify>;<English>\n"),
        })

    def test_update_keeps_edits_and_modules(self):
        res = self.lib.add(self.v1())
        self.assertEqual((res.action, res.mod.name, res.mod.prefix), ("added", "AB", "AB_"))
        mod = res.mod
        module = self.zip("Module 80.zip", {"lang/AB_80_module.csv": plain_csv("<ID|readonly|noverify>;<English>\nt_34_85;T-34/85\n")})
        res = self.lib.add(module)
        self.assertEqual((res.action, res.mod.id), ("module", mod.id))
        self.assertEqual(mod.files["AB_80_module.csv"].origin, "Module 80")
        self.lib.update_file(mod, "AB_99_user.csv", plain_csv("<ID|readonly|noverify>;<English>\nmine;Mine\n"))
        v2 = self.zip("lang (1).zip", {
            "lang/localization.blk": mod_list("AB_01_units.csv", "AB_80_module.csv", "AB_99_user.csv"),
            "lang/AB_01_units.csv": plain_csv("<ID|readonly|noverify>;<English>\nf_16a_0;Viper v2\n"),
            "lang/AB_99_user.csv": plain_csv("<ID|readonly|noverify>;<English>\n"),
        })
        res = self.lib.add(v2)
        self.assertEqual(res.action, "updated")
        self.assertIn(b"Viper v2", self.lib.read_file(mod, "AB_01_units.csv"))
        self.assertIn(b"Mine", self.lib.read_file(mod, "AB_99_user.csv"))
        self.assertIn("AB_80_module.csv", mod.files)
        self.assertTrue(any("kept your edited" in n.lower() for n in res.notes))
        # A fresh library object reads the same state back.
        again = Library(self.tmp / "home")
        self.assertEqual(again.get(mod.id).files["AB_99_user.csv"].edited, True)

    def test_both_changed_leaves_a_new_copy(self):
        mod = self.lib.add(self.v1()).mod
        self.lib.update_file(mod, "AB_01_units.csv", plain_csv("<ID|readonly|noverify>;<English>\nf_16a_0;Mine\n"))
        v2 = self.zip("v2.zip", {"lang/localization.blk": mod_list("AB_01_units.csv"),
                                 "lang/AB_01_units.csv": plain_csv("<ID|readonly|noverify>;<English>\nf_16a_0;v2\n")})
        res = self.lib.add(v2, into=mod.id, how="update")
        self.assertIn(b"Mine", self.lib.read_file(mod, "AB_01_units.csv"))
        self.assertTrue((self.lib.mod_dir(mod) / "AB_01_units.csv.new").is_file())
        self.assertNotIn("AB_99_user.csv", mod.files)      # the new version dropped it
        self.assertTrue(res.notes)

    def test_order_and_switches(self):
        a = self.lib.add(self.v1()).mod
        b = self.lib.add(self.folder("Short Names", {"SN_a.csv": b"<ID|readonly|noverify>;<English>\r\n"})).mod
        mine = self.lib.personal()
        c = self.lib.add(self.folder("Third", {"TH_a.csv": b"<ID|readonly|noverify>;<English>\r\n"})).mod
        self.assertEqual([m.id for m in self.lib.mods], [a.id, b.id, c.id, mine.id])  # personal stays last
        self.lib.move(c.id, 0)
        self.lib.set_enabled(b.id, False)
        self.assertEqual([m.id for m in self.lib.enabled()], [c.id, a.id, mine.id])
        self.lib.update_file(a, "IFN1_99_useroverwrite.csv", b"<ID|readonly|noverify>;<English>\r\nmine;Mine\r\n")
        token = self.lib.remove(a.id)
        self.assertFalse(self.lib.mod_dir(a).exists())
        self.assertNotIn(a.id, [m.id for m in self.lib.mods])
        self.assertEqual(self.lib.removed()[0][0], token)
        back = Library(self.lib.root).unremove()                    # even after the manager was closed
        self.assertEqual(back.id, a.id)
        lib = Library(self.lib.root)
        self.assertEqual([m.id for m in lib.mods], [c.id, a.id, b.id, mine.id])     # where it was
        self.assertIn(b"Mine", lib.read_file(lib.get(a.id), "IFN1_99_useroverwrite.csv"))   # edits too
        self.assertEqual(lib.removed(), [])
        with self.assertRaises(LibraryError):
            lib.unremove()
        # A month on, what was removed is cleared away.
        old = lib.root / "removed" / "20000101-000000-old"
        (old / "files").mkdir(parents=True)
        (old / "mod.json").write_text('{"index": 0, "mod": {"id": "old", "name": "Old"}}', "utf-8")
        lib.remove(b.id)                                         # removing clears what is too old
        self.assertFalse(old.exists())
        self.assertEqual(len(lib.removed()), 1)


class GameCase(Temp):
    """A fresh fake game and library, plus two mods that disagree about one string."""

    def setUp(self):
        super().setUp()
        self.game_root = build(self.tmp / "War Thunder")
        self.game = GameLang(self.game_root)
        self.lib = Library(self.tmp / "home")

    def add_two(self):
        big = self.lib.add(self.zip("lang.zip", {
            "lang/localization.blk": mod_list("AB_01_units.csv", "AB_02_more.csv", "AB_80_optional.csv"),
            "lang/AB_01_units.csv": plain_csv("<ID|readonly|noverify>;<English>\n;\n-- Tanks;\n"
                                              "t_34_85;T-34/85 (AB)\nf_16a_0;F-16A Fighting Falcon\n"),
            "lang/AB_02_more.csv": plain_csv("<ID|readonly|noverify>;<English>\nmainmenu/play;Battle!\n"),
        })).mod
        small = self.lib.add(self.folder("Short Names", {
            "SN_names.csv": plain_csv("<ID|readonly|noverify>;<English>\nt_34_85;T-34 (SN)\nnot_a_real_key;x\n"),
            "AB_02_more.csv": plain_csv("<ID|readonly|noverify>;<English>\nmainmenu/quit;Leave\n"),
        })).mod
        return big, small


class Planning(GameCase):
    def test_game_list_is_the_base_and_mods_follow_in_order(self):
        big, small = self.add_two()
        plan = build_plan(self.lib, self.game)
        self.assertEqual(plan.loc_table[:3], ["%lang/menu.csv", "%lang/units.csv", "%lang/inf.csv"])
        self.assertEqual(plan.loc_table[3:], ["%lang/AB_01_units.csv", "%lang/AB_02_more.csv",
                                              "%lang/AB_02_more.csv".replace("AB_", "short-names__AB_"),
                                              "%lang/SN_names.csv"])
        r = plan.reports[big.id]
        self.assertEqual(r.stale_list, ["inf.csv"])           # what used to show as IDs
        self.assertEqual(r.gone, ["_legal.csv"])
        self.assertEqual(r.not_shipped, ["AB_80_optional.csv"])
        self.assertEqual(plan.reports[small.id].renamed, [("AB_02_more.csv", "short-names__AB_02_more.csv")])
        root, problems = blk.parse_text(plan.localization)
        self.assertEqual(problems, [])
        self.assertEqual(root.value("default_lang"), "English")
        self.assertEqual(root.values("regional"), ["%langRegional/special_events.csv"])

    def test_conflicts_last_mod_wins(self):
        big, small = self.add_two()
        plan = build_plan(self.lib, self.game)
        an = analyze(self.lib, self.game, plan)
        self.assertEqual([c.key for c in an.conflicts], ["t_34_85"])
        self.assertEqual(an.conflicts[0].winner, small.id)
        self.assertEqual(an.conflicts[0].game, "T-34-85")
        s = an.stats[big.id]
        self.assertEqual((s.changes, s.same, s.noise), (2, 1, 2))
        self.assertEqual(an.stats[small.id].new, 1)
        self.lib.move(small.id, 0)
        an = analyze(self.lib, self.game, build_plan(self.lib, self.game))
        self.assertEqual(an.conflicts[0].winner, big.id)

    def test_full_copy_becomes_the_changed_rows_only(self):
        # A copy of units.csv from before "new_tank_2024" existed, with one name changed.
        old_copy = csv_bytes([("f_16a_0", "Viper", "F-16A"), ("t_34_85", "T-34-85", "T-34-85")])
        mod = self.lib.add(self.folder("Old Style", {"units.csv": old_copy})).mod
        plan = build_plan(self.lib, self.game)
        self.assertEqual(plan.reports[mod.id].replaced, [("units.csv", 1, 2)])
        files = plan_files(self.lib, plan)
        self.assertNotIn("units.csv", files)                  # never shadow the game's own table
        overlay = read_table(files["old-style__units.csv"])
        self.assertEqual(overlay.texts(), {"f_16a_0": "Viper"})
        self.assertEqual(plan.loc_table[-1], "%lang/old-style__units.csv")

    def test_overlay_rows_compares_by_column_name(self):
        game = csv_bytes([("k", "Same", "Pareil")], columns=("English", "French"))
        mine = csv_bytes([("k", "Pareil", "Same")], columns=("French", "English"))
        self.assertEqual(overlay_rows(mine, game)[1], [])

    def test_regional_table_moved_into_the_list(self):
        mod = self.lib.add(self.zip("lang.zip", {
            "lang/localization.blk": mod_list("RG_a.csv", extra="").replace(
                b"locTable{", b'locTable{\r\n  file:t="%langRegional/special_events.csv"'),
            "lang/RG_a.csv": plain_csv("<ID|readonly|noverify>;<English>\nevent_name;Renamed\n"),
        })).mod
        plan = build_plan(self.lib, self.game)
        root, _ = blk.parse_text(plan.localization)
        self.assertEqual(root.values("regional"), [])
        self.assertEqual(plan.reports[mod.id].regional, ["special_events.csv"])
        self.assertEqual(plan.loc_table[3:], ["%langRegional/special_events.csv", "%lang/RG_a.csv"])

    def test_a_regional_table_two_mods_move_loads_once_before_both(self):
        """Loaded again before the second mod, it would put the game's text back over the first's."""
        for prefix, folder in (("RG", "One"), ("XY", "Two")):
            self.lib.add(self.folder(folder, {
                "localization.blk": mod_list(f"{prefix}_a.csv").replace(
                    b"locTable{", b'locTable{\r\n  file:t="%langRegional/special_events.csv"'),
                f"{prefix}_a.csv": plain_csv("<ID|readonly|noverify>;<English>\nevent_name;Renamed\n"),
            }))
        plan = build_plan(self.lib, self.game)
        self.assertEqual(plan.loc_table[3:], ["%langRegional/special_events.csv", "%lang/RG_a.csv", "%lang/XY_a.csv"])

    def test_a_regional_table_moves_for_a_mod_that_sets_its_strings(self):
        """Left regional, it loads after the mods and wins: a mod that does not move it itself would
        change nothing there."""
        mod = self.lib.add(self.folder("Events", {
            "EV_a.csv": plain_csv("<ID|readonly|noverify>;<English>\nevent_name;Renamed\n")})).mod
        plan = build_plan(self.lib, self.game)
        self.assertEqual(plan.loc_table[3:], ["%langRegional/special_events.csv", "%lang/EV_a.csv"])
        self.assertEqual(blk.parse_text(plan.localization)[0].values("regional"), [])
        self.assertEqual(plan.reports[mod.id].regional, ["special_events.csv"])
        self.lib.set_enabled(mod.id, False)
        self.add_two()                                              # nothing of the event table in these
        plan = build_plan(self.lib, self.game)
        self.assertNotIn("%langRegional/special_events.csv", plan.loc_table)
        self.assertEqual(blk.parse_text(plan.localization)[0].values("regional"), ["%langRegional/special_events.csv"])
        self.assertEqual((plan.regional_moved, plan.regional_last), ([], ["%langRegional/special_events.csv"]))

    def test_a_game_table_the_list_comments_out_stays_out(self):
        """IFN1 comments out the game's encyclopedia_tips.csv to use only its own tips: that is on purpose, not
        a list from before the game had the file (which a line simply missing is)."""
        mod = self.lib.add(self.folder("Own Tips", {
            "localization.blk": mod_list("OT_a.csv").replace(b'  file:t="%lang/units.csv"',
                                                            b'  file:t="%lang/units.csv"\r\n  //\tfile:t="%lang/inf.csv"'),
            "OT_a.csv": plain_csv("<ID|readonly|noverify>;<English>\nmainmenu/play;Go\n")})).mod
        plan = build_plan(self.lib, self.game)
        r = plan.reports[mod.id]
        self.assertEqual((r.left_out, r.stale_list), (["inf.csv"], []))
        self.assertEqual(plan.left_out, ["%lang/inf.csv"])
        self.assertNotIn("%lang/inf.csv", plan.loc_table)
        self.assertNotIn("inf.csv", plan.localization)
        self.assertEqual(analyze(self.lib, self.game, plan).shown("soldier_rifle"), ("", ""))
        self.lib.set_enabled(mod.id, False)                    # the mod that wants it out is off: it loads
        plan = build_plan(self.lib, self.game)
        self.assertIn("%lang/inf.csv", plan.loc_table)
        self.assertEqual(plan.left_out, [])
        self.add_two()                                         # a list merely without it still gets it
        self.assertEqual(build_plan(self.lib, self.game).reports[self.lib.mods[1].id].stale_list, ["inf.csv"])

    def test_a_row_that_only_repeats_an_event_table_moves_nothing(self):
        """Every game table ends in an empty CLIPPED_LANG_KEEP_IT_ALWAYS_FIRST row, and mods copy it."""
        mod = self.lib.add(self.folder("Copied Rows", {"CR_a.csv": csv_bytes(
            [("CLIPPED_LANG_KEEP_IT_ALWAYS_FIRST", "", ""), ("event_name", "Autumn Event", "Événement"),
             ("t_34_85", "T-34 (CR)", "T-34")])})).mod
        plan = build_plan(self.lib, self.game)
        self.assertEqual(plan.regional_moved, [])
        self.assertEqual(plan.reports[mod.id].regional, [])
        self.assertEqual(plan.regional_last, ["%langRegional/special_events.csv"])
        self.assertEqual(plan.loc_table[3:], ["%lang/CR_a.csv"])

    def test_moved_event_tables_keep_the_games_order(self):
        tournaments = [("cup_name", "Spring Cup", "Coupe")]
        self.game_root = build(self.tmp / "Two Tables", regional={"special_events.csv": [("event_name", "Autumn Event", "É")],
                                                                  "tournaments.csv": tournaments})
        self.game = GameLang(self.game_root)
        for prefix, folder, ref in (("RG", "One", "tournaments"), ("XY", "Two", "special_events")):
            self.lib.add(self.folder(folder, {
                "localization.blk": mod_list(f"{prefix}_a.csv").replace(
                    b"locTable{", f'locTable{{\r\n  file:t="%langRegional/{ref}.csv"'.encode()),
                f"{prefix}_a.csv": plain_csv("<ID|readonly|noverify>;<English>\nmainmenu/play;Go\n"),
            }))
        plan = build_plan(self.lib, self.game)
        self.assertEqual(plan.regional_moved, ["%langRegional/special_events.csv", "%langRegional/tournaments.csv"])
        self.assertEqual(plan.loc_table[3:5], plan.regional_moved)
        self.assertEqual(plan.regional_last, [])

    def test_a_mods_own_file_in_its_event_list_loads_after_its_others(self):
        """The other way round from moving an event table into locTable: a file of its own listed with
        the event tables, so that it loads last and wins over them."""
        mod = self.lib.add(self.folder("Late", {
            "localization.blk": mod_list("LT_a.csv", extra='regional:t="%langRegional/special_events.csv"\r\n'
                                                           'regional:t="%lang/LT_events.csv"'),
            "LT_a.csv": plain_csv("<ID|readonly|noverify>;<English>\nmainmenu/play;Go\n"),
            "LT_events.csv": plain_csv("<ID|readonly|noverify>;<English>\nevent_name;Renamed\n"),
        })).mod
        plan = build_plan(self.lib, self.game)
        r = plan.reports[mod.id]
        self.assertEqual(r.loads, ["LT_a.csv", "LT_events.csv"])
        self.assertEqual(r.unlisted, [])
        self.assertEqual(r.regional, ["special_events.csv"])
        self.assertEqual(plan.loc_table[3:], ["%langRegional/special_events.csv", "%lang/LT_a.csv", "%lang/LT_events.csv"])
        self.assertEqual(analyze(self.lib, self.game, plan).shown("event_name"), (mod.id, "Renamed"))

    def test_a_copy_of_an_event_table_is_cut_down_like_a_copy_of_any_other(self):
        copy = csv_bytes([("event_name", "Renamed Event", "Événement"), ("old_event", "Gone Event", "Parti")])
        mod = self.lib.add(self.folder("Events Copy", {"special_events.csv": copy})).mod
        plan = build_plan(self.lib, self.game)
        self.assertEqual(plan.reports[mod.id].replaced, [("special_events.csv", 2, 2)])
        files = plan_files(self.lib, plan)
        self.assertNotIn("special_events.csv", files)
        self.assertEqual(plan.loc_table[3:], ["%langRegional/special_events.csv", "%lang/events-copy__special_events.csv"])
        self.assertEqual(analyze(self.lib, self.game, plan).shown("event_name"), (mod.id, "Renamed Event"))

    def test_list_entries_in_subfolders_are_optional_packages(self):
        """WTHLM lists its optional packages as %lang/Package_X/WTHLM_x.csv; they come as modules."""
        mod = self.lib.add(self.folder("Packages", {
            "localization.blk": mod_list("PK_main.csv", "Package_Extra/PK_extra.csv", "PK_last.csv"),
            "PK_main.csv": plain_csv("<ID|readonly|noverify>;<English>\nmainmenu/play;Go\n"),
            "PK_last.csv": plain_csv("<ID|readonly|noverify>;<English>\nmainmenu/quit;Stop\n"),
        })).mod
        r = build_plan(self.lib, self.game).reports[mod.id]
        self.assertEqual(r.not_shipped, ["Package_Extra/PK_extra.csv"])
        self.assertEqual(r.gone, ["_legal.csv"])
        added = self.lib.add(self.folder("Package_Extra", {
            "PK_extra.csv": plain_csv("<ID|readonly|noverify>;<English>\nt_34_85;Extra\n")}))
        self.assertEqual(added.mod.id, mod.id)                         # a module of the mod, not a mod
        r = build_plan(self.lib, self.game).reports[mod.id]
        self.assertEqual(r.loads, ["PK_main.csv", "PK_extra.csv", "PK_last.csv"])   # where its list has it
        self.assertEqual((r.not_shipped, r.notes), ([], []))


class Installing(GameCase):
    def install(self) -> GameInstall:
        return GameInstall(self.game_root, "custom")

    def test_apply_edit_reapply_restore(self):
        inst = self.install()
        lang = inst.lang_dir
        # What a player has before: a mod pasted in by hand, an old copy of a game table,
        # and an exact copy of one (as the game once wrote out itself).
        self.folder("War Thunder/lang", {
            "localization.blk": b"locTable{\r\n}\r\n",
            "HAND_made.csv": b"<ID|readonly|noverify>;<English>\r\n",
            "units.csv": csv_bytes(UNITS[:2]),
            "menu.csv": self.game.resolve("%lang/menu.csv"),
        })
        big, small = self.add_two()
        res = install.apply(self.lib, inst, self.game, build_plan(self.lib, self.game))
        self.assertTrue(res.switched_on)
        self.assertEqual(sorted(res.backed_up), ["HAND_made.csv", "localization.blk", "units.csv"])
        self.assertEqual(res.removed, 1)                      # the exact copy of menu.csv
        self.assertTrue(any("old copy" in n for n in res.notes))
        self.assertTrue((res.backup / "config.blk").is_file())
        self.assertTrue(config.test_localization(inst.config_path.read_text("utf-8")))
        on_disk = sorted(p.name for p in lang.iterdir())
        self.assertEqual(on_disk, sorted(["AB_01_units.csv", "AB_02_more.csv", "short-names__AB_02_more.csv",
                                          "SN_names.csv", "localization.blk", install.MANIFEST]))
        st = install.status(inst)
        self.assertTrue(st["managed"] and not st["stale"] and st["switch_on"])

        # The player types into a file in the game folder, then applies again.
        (lang / "SN_names.csv").write_bytes(plain_csv("<ID|readonly|noverify>;<English>\nt_34_85;Mine\n"))
        self.lib.set_enabled(big.id, False)
        res = install.apply(self.lib, inst, self.game, build_plan(self.lib, self.game))
        self.assertEqual(res.taken_back, ["SN_names.csv -> Short Names"])
        self.assertIn(b"Mine", self.lib.read_file(small, "SN_names.csv"))
        self.assertIn(b"Mine", (lang / "SN_names.csv").read_bytes())
        self.assertFalse((lang / "AB_01_units.csv").exists())

        # A game update makes the applied list stale.
        os.utime(self.game_root / "lang.vromfs.bin", (1, 1))
        self.assertTrue(install.status(inst)["stale"])

        res = install.restore(self.lib, inst)
        self.assertEqual(sorted(res.restored), ["HAND_made.csv", "localization.blk", "units.csv"])
        self.assertFalse((lang / install.MANIFEST).exists())
        self.assertFalse(res.switched_off)                    # the hand-made mod needs it

    def test_restore_without_bringing_back_switches_off(self):
        inst = self.install()
        self.add_two()
        install.apply(self.lib, inst, self.game, build_plan(self.lib, self.game))
        res = install.restore(self.lib, inst, bring_back=False)
        self.assertTrue(res.switched_off)
        self.assertFalse(inst.lang_dir.exists())
        self.assertFalse(config.test_localization(inst.config_path.read_text("utf-8")))


def wt_release(version: str, packages: dict[str, str], main: str = "Main") -> dict[str, bytes]:
    """A WTHLM-like download: the mod in lang/, optional packages in Packages/<name>/, which its own list
    names as %lang/<name>/<file>. ``packages``: name -> the text its one file gives t_34_85."""
    listed = ["WT_a.csv"] + [f"{name}/WT_{name[-1].lower()}.csv" for name in packages] + ["WT_last.csv"]
    files = {f"WT_{version}/lang/localization.blk": mod_list(*listed),
             f"WT_{version}/lang/WT_a.csv": plain_csv(f"<ID|readonly|noverify>;<English>\nt_34_85;{main}\n"),
             f"WT_{version}/lang/WT_last.csv": plain_csv("<ID|readonly|noverify>;<English>\nmainmenu/quit;Out\n"),
             f"WT_{version}/Extra/notes.csv": plain_csv("<ID|readonly|noverify>;<English>\nx;y\n")}
    for name, text in packages.items():
        files[f"WT_{version}/Packages/{name}/WT_{name[-1].lower()}.csv"] = plain_csv(
            f"<ID|readonly|noverify>;<English>\nt_34_85;{text}\n")
    return files


class ModulesInDownloads(GameCase):
    def test_packages_in_the_download_come_as_modules_switched_off(self):
        mod = self.lib.add(self.zip("WT_1.0.zip", wt_release("1.0", {"Package_X": "X", "Package_Y": "Y"}))).mod
        self.assertEqual(mod.modules, ["Package_X", "Package_Y"])
        self.assertEqual((mod.off, mod.bundled), (["Package_X", "Package_Y"], ["Package_X", "Package_Y"]))
        self.assertTrue(any("Extra" in n for n in mod.notes))                 # not named by its list
        r = build_plan(self.lib, self.game).reports[mod.id]
        self.assertEqual(r.loads, ["WT_a.csv", "WT_last.csv"])
        self.assertEqual((r.not_shipped, r.gone), ([], ["_legal.csv"]))       # switched off is not missing

        self.lib.set_module(mod.id, "Package_X", True)
        plan = build_plan(self.lib, self.game)
        self.assertEqual(plan.reports[mod.id].loads, ["WT_a.csv", "WT_x.csv", "WT_last.csv"])
        self.assertEqual(analyze(self.lib, self.game, plan).shown("t_34_85"), (mod.id, "X"))

        # The next version drops Y, brings Z and changes X: X stays on, Z waits switched off, Y goes.
        self.lib.add(self.zip("WT_2.0.zip", wt_release("2.0", {"Package_X": "X2", "Package_Z": "Z"})))
        self.assertEqual((mod.modules, mod.off, mod.bundled),
                         (["Package_X", "Package_Z"], ["Package_Z"], ["Package_X", "Package_Z"]))
        self.assertNotIn("WT_y.csv", mod.files)
        self.assertEqual(self.lib.read_file(mod, "WT_x.csv"), plain_csv("<ID|readonly|noverify>;<English>\nt_34_85;X2\n"))
        with self.assertRaises(LibraryError):
            self.lib.set_module(mod.id, "Package_Y", True)

    def test_module_switches_go_with_profiles(self):
        from langmod.core import profiles
        mod = self.lib.add(self.zip("WT_1.0.zip", wt_release("1.0", {"Package_X": "X", "Package_Y": "Y"}))).mod
        profiles.save_as(self.lib, "Plain")
        profiles.save_as(self.lib, "With X")
        self.lib.set_module(mod.id, "Package_X", True)                 # the profile in use follows
        profiles.switch(self.lib, "Plain")
        self.assertEqual(mod.off, ["Package_X", "Package_Y"])
        profiles.switch(self.lib, "With X")
        self.assertEqual(mod.off, ["Package_Y"])
        path = profiles.export(self.lib, "With X", self.tmp / "x.langmod-profile")
        doc = profiles.read_file(path)
        self.assertEqual(doc["mods"][0]["modules"], {"Package_X": True, "Package_Y": False})
        profiles.take(self.lib, doc)
        self.assertEqual(mod.off, ["Package_Y"])

    def test_a_module_added_on_its_own_loads_straight_away(self):
        mod = self.lib.add(self.zip("WT_1.0.zip", wt_release("1.0", {"Package_X": "X"}))).mod
        self.lib.add(self.folder("Package_X", {"WT_x.csv": plain_csv("<ID|readonly|noverify>;<English>\nt_34_85;Own\n")}))
        self.assertEqual(mod.off, [])
        self.assertIn("WT_x.csv", build_plan(self.lib, self.game).reports[mod.id].loads)


class Readmes(GameCase):
    def test_the_read_me_beside_the_mod_is_kept_with_it(self):
        """IFN1's Nexus zip: "IFN1 Langmod V74.1/readme.txt" beside its lang folder. A package's own read me
        further down does not stand in for the mod's."""
        files = wt_release("1.0", {"Package_X": "X"})
        files["WT_1.0/Packages/Package_X/README.md"] = b"# Package X"
        files["WT_1.0/readme.txt"] = "WT read me: https://example.com/wt".encode()
        mod = self.lib.add(self.zip("WT_1.0.zip", files)).mod
        self.assertEqual(mod.readme, "readme.txt")
        path = self.lib.readme_path(mod)
        self.assertEqual(path.read_text(), "WT read me: https://example.com/wt")
        self.assertNotIn("readme.txt", mod.files)                      # never among the files for the game
        self.assertNotIn("readme.txt", {p.dest for p in build_plan(self.lib, self.game).placements})
        files = wt_release("2.0", {"Package_X": "X"})
        files["WT_2.0/README.md"] = b"# WT 2.0"
        self.lib.add(self.zip("WT_2.0.zip", files))
        self.assertEqual((mod.readme, self.lib.readme_path(mod).read_text()), ("README.md", "# WT 2.0"))
        self.assertFalse(path.exists())
        self.lib.add(self.zip("WT_3.0.zip", wt_release("3.0", {"Package_X": "X"})))    # none this time
        self.assertEqual(mod.readme, "README.md")
        token = self.lib.remove(mod.id)
        self.assertIsNotNone(self.lib.readme_path(self.lib.unremove(token)))


class HandInstalls(GameCase):
    """A mod someone pasted into lang/ themselves, before the manager ever applied there."""

    def setUp(self):
        super().setUp()
        self.inst = GameInstall(self.game_root, "custom")
        self.folder("War Thunder/lang", {
            "localization.blk": mod_list("HM_a.csv", "Package_P/HM_p.csv"),
            "HM_a.csv": plain_csv("<ID|readonly|noverify>;<English>\nt_34_85;Hand made\n"),
            "Package_P/HM_p.csv": plain_csv("<ID|readonly|noverify>;<English>\nf_16a_0;Hand package\n"),
            "readme.txt": b"notes",
        })

    def test_it_joins_the_mods_first_and_keeps_working(self):
        big, small = self.add_two()
        hand = install.keep_hand_install(self.lib, self.inst)
        self.assertTrue(hand.taken)
        self.assertEqual([m.id for m in self.lib.mods], [hand.mod.id, big.id, small.id])   # everything goes on top
        self.assertEqual((hand.mod.name, hand.mod.modules, hand.mod.off), ("HM", ["Package_P"], []))
        self.assertTrue(hand.holds("HM_a.csv") and hand.holds("Package_P/") and not hand.holds("readme.txt"))
        plan = build_plan(self.lib, self.game)
        self.assertEqual(plan.reports[hand.mod.id].loads, ["HM_a.csv", "HM_p.csv"])
        res = install.apply(self.lib, self.inst, self.game, plan)
        lang = self.inst.lang_dir
        self.assertTrue((lang / "HM_p.csv").is_file())
        self.assertFalse((lang / "Package_P").exists())                   # to the backup, not left unloaded
        self.assertIn("Package_P/", res.backed_up)
        self.assertTrue((res.backup / "lang" / "Package_P" / "HM_p.csv").is_file())
        self.assertIsNone(install.keep_hand_install(self.lib, self.inst))  # the folder is the manager's now
        install.restore(self.lib, self.inst)
        self.assertTrue((lang / "Package_P" / "HM_p.csv").is_file())
        self.assertTrue((lang / "HM_a.csv").is_file())

    def test_the_mods_own_copy_is_never_written_over(self):
        mine = self.lib.add(self.folder("HM", {
            "localization.blk": mod_list("HM_a.csv"),
            "HM_a.csv": plain_csv("<ID|readonly|noverify>;<English>\nt_34_85;Newer\n")})).mod
        hand = install.keep_hand_install(self.lib, self.inst)
        self.assertEqual((hand.mod, hand.taken, hand.same), (mine, False, False))
        self.assertEqual(len(self.lib.mods), 1)
        self.assertIn(b"Newer", self.lib.read_file(mine, "HM_a.csv"))

    def test_said_no_to_it_stays_out(self):
        self.add_two()
        hand = install.hand_install(self.lib, self.inst)
        install.decline(self.lib, self.inst, None)                         # changing one's mind works both ways
        self.assertFalse(install.hand_install(self.lib, self.inst).declined)
        install.decline(self.lib, self.inst, hand)
        again = install.keep_hand_install(self.lib, self.inst)
        self.assertTrue(again.declined and not again.taken)
        self.assertEqual(len(self.lib.mods), 2)
        res = install.apply(self.lib, self.inst, self.game, build_plan(self.lib, self.game))
        self.assertIn("HM_a.csv", res.backed_up)
        self.assertIn("Package_P/", res.backed_up)

    def test_modules_pasted_in_loose_survive_its_next_version(self):
        """IFN1's modules from Nexus go straight into lang/, beside the mod. Taken in, they are the mod's own
        files; its next lang.zip does not bring them, but its list still names them: they stay, as modules."""
        lang = self.inst.lang_dir
        (lang / "localization.blk").write_bytes(mod_list("HM_a.csv", "HM_80_module.csv"))
        (lang / "HM_80_module.csv").write_bytes(plain_csv("<ID|readonly|noverify>;<English>\nt_34_85;Module\n"))
        mod = install.keep_hand_install(self.lib, self.inst).mod
        res = self.lib.add(self.zip("lang.zip", {
            "lang/localization.blk": mod_list("HM_a.csv", "HM_80_module.csv"),
            "lang/HM_a.csv": plain_csv("<ID|readonly|noverify>;<English>\nt_34_85;Newer\n")}))
        self.assertEqual((res.action, res.mod), ("updated", mod))
        self.assertIn("HM_80_module.csv", mod.files)
        self.assertIn("HM_80_module", mod.modules)
        self.assertNotIn("HM_80_module", mod.off)
        self.assertTrue(any("still names" in n for n in res.notes))
        self.assertEqual(build_plan(self.lib, self.game).reports[mod.id].loads, ["HM_a.csv", "HM_80_module.csv"])

    def test_the_command_line_takes_it_in_before_applying(self):
        import contextlib
        import io
        from langmod import cli
        out = io.StringIO()
        with contextlib.redirect_stdout(out), unittest.mock.patch.object(cli, "game_running", return_value=False):
            self.assertEqual(cli.main(["--home", str(self.tmp / "home"), "--game", str(self.game_root), "apply"]), 0)
        self.assertIn("put there by hand", out.getvalue())
        self.assertEqual([m.name for m in Library(self.tmp / "home").mods], ["HM"])
        self.assertTrue((self.inst.lang_dir / "HM_p.csv").is_file())


class Robustness(GameCase):
    """Things that went wrong in a bug hunt, kept from going wrong again."""

    def test_library_json_from_a_newer_version_or_damaged(self):
        home = self.tmp / "home2"
        home.mkdir()
        state = {"mods": [{"id": "x", "name": "X", "from_the_future": 1,
                           "files": {"a.csv": {"name": "a.csv", "origin": "main", "sha1": "1", "shipped": "1",
                                               "also_new": True}}}],
                 "settings": {"prefs": {"theme": "aurora"}}}
        (home / "library.json").write_text(__import__("json").dumps(state), "utf-8")
        lib = Library(home)
        self.assertEqual([m.id for m in lib.mods], ["x"])          # unknown fields passed over
        self.assertEqual(lib.load_problem, "")
        state["mods"][0].update(name="WTHLM_1.19.00", version="1.20.00")
        (home / "library.json").write_text(__import__("json").dumps(state), "utf-8")
        mod = Library(home).mods[0]                                 # named before versions came off names
        self.assertEqual((mod.id, mod.name, mod.label), ("x", "WTHLM", "WTHLM 1.20.00"))
        (home / "library.json").write_text("{ half written", "utf-8")
        lib = Library(home)
        self.assertEqual(lib.mods, [])
        self.assertIn("could not be read", lib.load_problem)
        broken = list(home.glob("library.broken-*.json"))
        self.assertEqual(len(broken), 1)                            # set aside, not lost
        lib.save()
        self.assertEqual(broken[0].read_text("utf-8"), "{ half written")

    def test_names_windows_cannot_store_or_that_differ_only_in_case(self):
        pkg = read_package(self.zip("odd.zip", {
            "lang/AB_units.csv": plain_csv("<ID|readonly|noverify>;<English>\nk;v\n"),
            "lang/ab_UNITS.csv": plain_csv("<ID|readonly|noverify>;<English>\nk;other\n"),
            "lang/AB:more.csv": plain_csv("<ID|readonly|noverify>;<English>\nk;v\n"),
            "lang/CON.csv": plain_csv("<ID|readonly|noverify>;<English>\nk;v\n"),
        }))
        self.assertEqual(list(pkg.files), ["AB_units.csv"])
        self.assertEqual(len(pkg.notes), 3)
        with self.assertRaises(LibraryError):                      # nothing usable left: not a mod
            read_package(self.zip("colon.zip", {"lang/AB:units.csv": plain_csv("<ID|readonly|noverify>;<English>\nk;v\n")}))

    def test_a_damaged_zip_is_said_to_be_damaged(self):
        """Found by fuzzing: a broken deflate stream raised zlib's own error, not a message."""
        path = self.tmp / "mod.zip"
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("lang/AB_a.csv", plain_csv("<ID|readonly|noverify>;<English>\n"
                                                   + "".join(f"k{i};text {i * 7919 % 1000}\n" for i in range(400))))
        data = bytearray(path.read_bytes())
        start = data.index(b"AB_a.csv") + len("AB_a.csv")
        data[start + 30:start + 90] = b"\xff" * 60               # into the compressed data
        path.write_bytes(bytes(data))
        with self.assertRaises(LibraryError) as caught:
            read_package(path)
        self.assertIn("damaged", str(caught.exception))

    def test_a_header_cell_over_two_lines(self):
        table = read_table(b'"<ID|readonly|noverify>";"<Eng\r\nlish>"\r\n"k";"v"\r\n')
        self.assertEqual(len(table.columns), 2)

    def test_a_folder_far_too_big_to_be_a_mod(self):
        big = self.folder("Everything", {f"d{i}/f{j}.txt": b"x" for i in range(3) for j in range(4)})
        (big / "d0" / "AB_units.csv").write_bytes(plain_csv("<ID|readonly|noverify>;<English>\nk;v\n"))
        with unittest.mock.patch.object(library_module, "MAX_WALK", 8):
            with self.assertRaises(LibraryError):
                read_package(big)
        self.assertEqual(list(read_package(big).files), ["AB_units.csv"])

    def test_apply_again_leaves_unchanged_files_alone(self):
        inst = GameInstall(self.game_root, "custom")
        big, small = self.add_two()
        first = install.apply(self.lib, inst, self.game, build_plan(self.lib, self.game))
        self.assertEqual(first.changed, first.written)
        for p in inst.lang_dir.iterdir():
            os.utime(p, (1000, 1000))
        again = install.apply(self.lib, inst, self.game, build_plan(self.lib, self.game))
        self.assertEqual((again.written, again.changed), (first.written, 0))
        self.assertTrue(all(p.stat().st_mtime == 1000 for p in inst.lang_dir.iterdir()
                            if p.name != install.MANIFEST))
        self.lib.update_file(small, "SN_names.csv", plain_csv("<ID|readonly|noverify>;<English>\nt_34_85;New\n"))
        third = install.apply(self.lib, inst, self.game, build_plan(self.lib, self.game))
        self.assertEqual(third.changed, 1)
        self.assertIn(b"New", (inst.lang_dir / "SN_names.csv").read_bytes())
        self.assertFalse(install.pending(self.lib, build_plan(self.lib, self.game),
                                         install.read_manifest(inst.lang_dir)))

    def test_game_stamp_is_the_one_it_was_read_at(self):
        before = self.game.stamp
        os.utime(self.game_root / "lang.vromfs.bin", (5, 5))
        self.assertEqual(self.game.stamp, before)                 # still names what it read
        self.assertNotEqual(lang_stamp(self.game_root), before)   # which is how a reload is noticed


class Launching(GameCase):
    def run_cli(self, *args: str) -> str:
        import contextlib
        import io
        from langmod import cli
        out = io.StringIO()
        with unittest.mock.patch.object(cli, "start_game", return_value="steam://rungameid/236390") as start, \
                unittest.mock.patch.object(cli, "game_running", return_value=False), \
                contextlib.redirect_stdout(out):
            code = cli.main(["--home", str(self.tmp / "home"), "--game", str(self.game_root), *args])
        self.assertEqual(code, 0)
        self.starts = start.call_count
        return out.getvalue()

    def test_launch_applies_only_when_something_changed(self):
        self.add_two()
        self.assertIn("applied 2 mod(s)", self.run_cli("launch"))
        self.assertEqual(self.starts, 1)
        self.assertIn("up to date", self.run_cli("launch"))
        os.utime(self.game_root / "lang.vromfs.bin", (1, 1))          # the game updated
        self.assertIn("applied", self.run_cli("launch"))
        self.run_cli("disable", "short-names")                        # the mods changed
        self.assertIn("applied 1 mod(s)", self.run_cli("launch"))
        self.assertIn("up to date", self.run_cli("launch"))

    def test_plan_and_check_name_the_game_and_the_picks(self):
        """Picks are not mods: the command line once fell over looking them up as mods."""
        self.add_two()
        self.run_cli("pick", "t_34_85", "game")
        self.assertIn("your picks: loads 1 file(s)", self.run_cli("plan"))
        self.assertIn("-> the game wins  (picked)", self.run_cli("check"))

    def through_steam(self, game: Path) -> tuple[int, str, str]:
        """``launch %command%`` as Steam runs it, the command a Python that notes it ran and
        answers 3, and nothing in the command's folder for a game."""
        import contextlib
        import io
        from langmod import cli
        out, err = io.StringIO(), io.StringIO()
        command = [sys.executable, "-c", "import sys; open(sys.argv[1], 'w').write('ran'); sys.exit(3)",
                   str(self.tmp / "ran")]
        with unittest.mock.patch.object(cli, "start_game") as start, \
                unittest.mock.patch.object(cli, "game_running", return_value=False), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["--home", str(self.tmp / "home"), "--game", str(game), "launch", *command])
        self.assertEqual(start.call_count, 0)                      # Steam's own command, not ours
        self.assertEqual((self.tmp / "ran").read_text(), "ran")
        (self.tmp / "ran").unlink()
        return code, out.getvalue(), err.getvalue()

    def test_from_steam_brings_the_mods_up_to_date_then_runs_steams_command(self):
        self.add_two()
        code, out, _ = self.through_steam(self.game_root)
        self.assertEqual(code, 3)                                  # the game's, handed back to Steam
        self.assertIn("applied 2 mod(s)", out)
        self.assertIn("up to date", self.through_steam(self.game_root)[1])

    def test_from_steam_the_game_starts_whatever_goes_wrong(self):
        code, _, err = self.through_steam(self.tmp / "not a game")
        self.assertEqual(code, 3)
        self.assertIn("starting the game as it is", err)
        self.assertIn("not brought up to date", (self.tmp / "home" / "errors.log").read_text("utf-8"))

    def test_from_steam_the_install_is_the_one_the_command_starts(self):
        from langmod import cli
        for program in ("launcher.exe", "win64/aces.exe"):
            self.assertEqual(cli._install_of(str(self.game_root / program)).root, self.game_root.resolve())
        self.assertIsNone(cli._install_of(sys.executable))


class SteamOptions(Temp):
    def config(self, account: str, options: str) -> None:
        escaped = options.replace("\\", "\\\\").replace('"', '\\"')
        cfg = self.tmp / "Steam" / "userdata" / account / "config" / "localconfig.vdf"
        cfg.parent.mkdir(parents=True)
        cfg.write_text('"UserLocalConfigStore"\n{\n\t"Software"\n\t{\n\t\t"Valve"\n\t\t{\n\t\t\t"Steam"\n\t\t\t{\n'
                       '\t\t\t\t"apps"\n\t\t\t\t{\n\t\t\t\t\t"730"\n\t\t\t\t\t{\n\t\t\t\t\t\t"LaunchOptions"\t\t"-novid"\n'
                       '\t\t\t\t\t}\n\t\t\t\t\t"236390"\n\t\t\t\t\t{\n\t\t\t\t\t\t"LastPlayed"\t\t"1758000000"\n'
                       '\t\t\t\t\t\t"cloud"\n\t\t\t\t\t\t{\n\t\t\t\t\t\t\t"LaunchOptions"\t\t"not this one"\n'
                       f'\t\t\t\t\t\t}}\n\t\t\t\t\t\t"LaunchOptions"\t\t"{escaped}"\n\t\t\t\t\t}}\n\t\t\t\t}}\n'
                       '\t\t\t}\n\t\t}\n\t}\n}\n', encoding="utf-8")

    def test_reads_war_thunders_launch_options_and_tells_whose_they_are(self):
        from langmod.core import steam
        with unittest.mock.patch.dict(os.environ, {"LANGMOD_STEAM_DIR": str(self.tmp / "Steam")}):
            self.assertEqual(steam.state(), "")                    # no Steam account here yet
            self.config("111", steam.option())
            self.assertEqual(steam.launch_options(), [steam.option()])
            self.assertEqual(steam.state(), "here")
            shutil.rmtree(self.tmp / "Steam")
            self.config("111", '"C:\\Old place\\Langmod Manager.exe" launch %command% -windowed')
            self.assertEqual(steam.state(), "elsewhere")
            self.config("222", "-dx12")
            self.assertEqual(sorted(steam.launch_options())[0], "\"C:\\Old place\\Langmod Manager.exe\" launch "
                                                                 "%command% -windowed")
            self.assertEqual(steam.state(), "elsewhere")

    def test_started_by_steam_the_game_is_not_sent_back_through_steam(self):
        """Launch options without %command% run 'launch' alone: through Steam again, it would loop."""
        from langmod.core import game as game_module
        root = build(self.tmp / "War Thunder")
        install = GameInstall(root, "steam")
        opener = "startfile" if sys.platform == "win32" else "Popen"
        target = game_module.os if opener == "startfile" else game_module.subprocess
        env = {k: v for k, v in os.environ.items() if k not in ("SteamGameId", "SteamAppId")}
        with unittest.mock.patch.object(target, opener, create=True), \
                unittest.mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(game_module.start_game(install), "steam://rungameid/236390")
            os.environ["SteamGameId"] = "236390"
            self.assertNotIn("steam://", game_module.start_game(install))

    def test_the_line_names_this_copy_and_steams_command(self):
        from langmod.core import steam
        line = steam.option()
        self.assertTrue(line.endswith(" launch %command%"))
        self.assertIn("launcher.py", line)                          # from source: Steam starts it in the game's folder


class PrefsAndBackups(GameCase):
    def test_prefs_defaults_types_and_reset(self):
        from langmod.core.prefs import DEFAULTS, Prefs
        prefs = Prefs(self.lib)
        self.assertEqual(prefs.get("theme"), "standard")
        prefs.set("theme", "sakura")
        prefs.set("shortcuts", {"manager:desktop": "x.lnk"})
        self.lib.settings["prefs"]["keep_backups"] = "lots"          # a hand-edited file
        self.assertEqual(Prefs(Library(self.tmp / "home")).get("theme"), "sakura")
        self.assertEqual(prefs.get("keep_backups"), DEFAULTS["keep_backups"])
        with self.assertRaises(KeyError):
            prefs.set("no_such_thing", 1)
        prefs.reset()
        self.assertEqual(prefs.get("theme"), "standard")
        self.assertEqual(prefs.get("shortcuts"), {"manager:desktop": "x.lnk"})   # kept through a reset

    def test_backups_pruned_but_the_first_kept(self):
        inst = GameInstall(self.game_root, "custom")
        self.add_two()
        lang = inst.lang_dir
        firsts = []
        for i in range(5):
            lang.mkdir(exist_ok=True)
            (lang / f"stray{i}.csv").write_bytes(b"x")
            res = install.apply(self.lib, inst, self.game, build_plan(self.lib, self.game), keep_backups=2)
            firsts.append(res.backup)
            if i < 4:
                import time
                time.sleep(1.05)          # backups are named by the second
        left = sorted(d.name for d in self.lib.backup_dir(inst.root).iterdir())
        self.assertEqual(len(left), 3)                                  # the first, and the newest two
        self.assertIn(firsts[0].name, left)
        self.assertIn(firsts[-1].name, left)


@unittest.skipUnless(sys.platform == "win32", "shortcuts are a Windows thing")
class Shortcuts(Temp):
    def setUp(self):
        super().setUp()
        self.env = unittest.mock.patch.dict(os.environ, {"LANGMOD_DESKTOP_DIR": str(self.tmp / "Desktop"),
                                                         "LANGMOD_PROGRAMS_DIR": str(self.tmp / "Programs")})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.lib = Library(self.tmp / "home")

    def test_make_follow_theme_and_drop(self):
        from langmod.core import shortcuts
        from langmod.core.prefs import Prefs
        lnk = shortcuts.make(self.lib, "manager", "desktop")
        self.assertEqual(lnk, self.tmp / "Desktop" / "Langmod Manager.lnk")
        target, args, icon = shortcuts.read(lnk)
        self.assertEqual(Path(target).name.lower(), "pythonw.exe")
        self.assertEqual(args, "-m langmod")
        self.assertTrue(icon.endswith("icon-standard.ico,0"))
        self.assertTrue(Path(icon.rsplit(",", 1)[0]).is_file())
        Prefs(self.lib).set("theme", "astral")
        self.assertEqual(shortcuts.follow_theme(self.lib), [lnk])
        self.assertIn("icon-astral.ico", shortcuts.read(lnk)[2])
        Prefs(self.lib).set("icons_follow_theme", False)
        shortcuts.follow_theme(self.lib)
        self.assertIn("icon-standard.ico", shortcuts.read(lnk)[2])
        self.assertTrue(shortcuts.exists(self.lib, "manager", "desktop"))
        shortcuts.drop(self.lib, "manager", "desktop")
        self.assertFalse(lnk.exists())
        self.assertEqual(Prefs(self.lib).get("shortcuts"), {})

    def test_cli_shortcut(self):
        import contextlib
        import io
        from langmod import cli
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli.main(["--home", str(self.tmp / "home"), "shortcut", "create", "--kind", "play", "--where", "programs"])
        lnk = self.tmp / "Programs" / "War Thunder with mods.lnk"
        self.assertTrue(lnk.is_file())
        from langmod.core import shortcuts
        self.assertEqual(shortcuts.read(lnk)[1], "-m langmod launch")
        with contextlib.redirect_stdout(out):
            cli.main(["--home", str(self.tmp / "home"), "shortcut", "remove", "--kind", "play", "--where", "programs"])
        self.assertFalse(lnk.exists())


# -- the real thing, when it is on this machine ---------------------------------

WT = Path(r"C:\Program Files (x86)\Steam\steamapps\common\War Thunder")
DOWNLOADS = Path.home() / "Downloads"
IFN1_ZIPS = [DOWNLOADS / n for n in ("lang.zip", "lang (1).zip", "lang (2).zip")]
IFN1_FULL = DOWNLOADS / "IFN1 Re-Name Mod-2162-74-1-1773898746.zip"


@unittest.skipUnless((WT / "lang.vromfs.bin").is_file() and all(p.is_file() for p in IFN1_ZIPS),
                     "needs War Thunder and the IFN1 downloads")
class Ifn1Stress(Temp):
    """IFN1, three releases of it, against the installed game. Read-only on the game."""

    @classmethod
    def setUpClass(cls):
        cls.game = GameLang(WT)

    def test_game_list_reads(self):
        refs = self.game.loc_table()
        self.assertIn("%lang/units.csv", refs)
        self.assertGreater(len(refs), 40)
        self.assertTrue(self.game.regional_files())

    def test_every_release_imports_plans_and_analyzes(self):
        lib = Library(self.tmp / "home")
        for i, z in enumerate(IFN1_ZIPS):
            res = lib.add(z)
            self.assertEqual(res.action, "added" if i == 0 else "updated")
            plan = build_plan(lib, self.game)
            r = plan.reports[res.mod.id]
            self.assertGreater(len(r.loads), 50)
            self.assertEqual(r.unlisted, [])
            self.assertEqual(r.replaced, [])
            root, problems = blk.parse_text(plan.localization)
            self.assertEqual(problems, [])
            # The game's list, less the loading tips IFN1 comments out to use only its own.
            own = [ref for ref in self.game.loc_table() if ref != "%lang/encyclopedia_tips.csv"]
            self.assertEqual(r.left_out, ["encyclopedia_tips.csv"])
            self.assertEqual(root.block("locTable").values("file")[:len(own)], own)
        self.assertEqual(len(lib.mods), 1)
        an = analyze(lib, self.game, plan)
        s = an.stats[lib.mods[0].id]
        self.assertGreater(s.changes, 10000)
        self.assertGreater(s.noise, 100)                      # IFN1's comment rows are counted, not choked on
        self.assertEqual(an.conflicts, [])

    def test_ifn1_with_a_second_mod(self):
        lib = Library(self.tmp / "home")
        ifn = lib.add(IFN1_ZIPS[-1]).mod
        other = lib.add(self.folder("Short Names", {
            "SN_names.csv": plain_csv("<ID|readonly|noverify>;<English>\nj_16_0;J-16 (SN)\n")})).mod
        plan = build_plan(lib, self.game)
        an = analyze(lib, self.game, plan)
        keys = {c.key: c for c in an.conflicts}
        self.assertIn("j_16_0", keys)
        self.assertEqual(keys["j_16_0"].winner, other.id)
        self.assertEqual(plan.loc_table[-1], "%lang/SN_names.csv")
        self.assertIn(ifn.id, plan.reports)

    @unittest.skipUnless(IFN1_FULL.is_file(), "needs the full IFN1 package")
    def test_full_package_then_monthly_zip_is_an_update(self):
        lib = Library(self.tmp / "home")
        res = lib.add(IFN1_FULL)
        self.assertEqual((res.mod.name, res.mod.version), ("IFN1 Re-Name Mod", "74.1"))
        self.assertEqual(lib.add(IFN1_ZIPS[-1]).action, "updated")
        self.assertEqual(lib.mods[0].name, "IFN1 Re-Name Mod")


if __name__ == "__main__":
    unittest.main()
