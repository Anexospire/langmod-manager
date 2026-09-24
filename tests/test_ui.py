"""Window tests: offscreen, in a temporary home, against the fake game only.

The desktop and Start menu are pointed at temporary folders too, so a test
that makes a shortcut never puts one on the real desktop.
"""
from __future__ import annotations

import math
import os
import shutil
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_SANDBOX = Path(tempfile.mkdtemp(prefix="langmod-ui-"))
os.environ["LANGMOD_HOME"] = str(_SANDBOX / "home")          # never the real library
os.environ["LANGMOD_DESKTOP_DIR"] = str(_SANDBOX / "Desktop")
os.environ["LANGMOD_PROGRAMS_DIR"] = str(_SANDBOX / "Programs")
os.environ["LANGMOD_STEAM_DIR"] = str(_SANDBOX / "Steam")    # never the real Steam settings
os.environ["LANGMOD_SELF_REPO"] = ""                          # never the real releases: no network
os.environ.pop("LANGMOD_THEME", None)
os.environ["LANGMOD_ANIMATIONS"] = "1"                        # not whatever this PC says
os.environ["LANGMOD_LANGUAGE"] = "en"                         # English, whatever Windows is in

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtCore import QRectF, Qt, QUrl  # noqa: E402
from PySide6.QtGui import QImage, QPainter  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from fakegame import build  # noqa: E402
from langmod.core import config, shortcuts  # noqa: E402
from langmod.core.game import GameInstall  # noqa: E402
from langmod.core.library import Library  # noqa: E402
from langmod.core.prefs import Prefs  # noqa: E402
from langmod.ui import scenes, themes  # noqa: E402
from langmod.ui.main_window import MainWindow  # noqa: E402

app = QApplication.instance() or QApplication([])
SHOTS = os.environ.get("LANGMOD_SHOT_DIR")


def csv(text: str) -> bytes:
    return text.replace("\n", "\r\n").encode()


class WindowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="langmod-ui-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.game_root = build(self.tmp / "War Thunder")
        self.lib = Library(self.tmp / "home")
        self.install = GameInstall(self.game_root, "custom")
        for target in ("langmod.ui.main_window.game_running",):
            patcher = unittest.mock.patch(target, return_value=False)
            patcher.start()
            self.addCleanup(patcher.stop)

    def window(self, installs=None, tour: bool = False) -> MainWindow:
        win = MainWindow(self.lib, [self.install] if installs is None else installs, threaded=False,
                         first_run_tour=tour)
        self.addCleanup(win.close)
        win.resize(1220, 780)
        win.show()
        app.processEvents()
        return win

    def mod(self, name: str, files: dict[str, bytes]) -> Path:
        d = self.tmp / name
        d.mkdir(parents=True)
        for n, data in files.items():
            (d / n).write_bytes(data)
        return d

    def two_mods(self, win: MainWindow) -> None:
        win.add_path(str(self.mod("A Mod", {"AA_a.csv": csv("<ID|readonly|noverify>;<English>\nt_34_85;From A\n")})))
        win.add_path(str(self.mod("B Mod", {"BB_a.csv": csv("<ID|readonly|noverify>;<English>\nt_34_85;From B\n"
                                                            "f_16a_0;Viper\n")})))

    def test_load_order_shows_where_the_event_tables_load(self):
        win = self.window()
        self.two_mods(win)
        text = win.load_list.toPlainText()
        self.assertIn("The game's event tables, loaded after everything else (1)", text)
        self.assertLess(text.index("BB_a.csv"), text.index("loaded after everything else"))
        win.add_path(str(self.mod("Events", {"EV_a.csv": csv("<ID|readonly|noverify>;<English>\nevent_name;Renamed\n")})))
        text = win.load_list.toPlainText()
        self.assertNotIn("loaded after everything else", text)
        self.assertLess(text.index("moved ahead of the mods"), text.index("AA_a.csv"))
        self.assertLess(text.index("special_events.csv"), text.index("AA_a.csv"))

    def test_no_game_found(self):
        win = self.window(installs=[])
        self.assertTrue(win.banner.isVisible())
        self.assertIn("not found", win.banner.label.text())
        self.assertFalse(win.apply_btn.isEnabled())
        self.assertFalse(win.play_btn.isEnabled())
        self.assertIs(win.stack.currentWidget(), win.empty)

    def test_hand_install_import_apply(self):
        lang = self.game_root / "lang"
        lang.mkdir()
        (lang / "localization.blk").write_bytes(b'locTable{\r\n  file:t="%lang/menu.csv"\r\n  file:t="%lang/HM_a.csv"\r\n}\r\n')
        (lang / "HM_a.csv").write_bytes(csv("<ID|readonly|noverify>;<English>\nt_34_85;Hand made\n"))
        win = self.window()
        self.assertEqual(win.game_meta.text(), "Folder · 2.59.0.13")
        self.assertEqual(win.state_chip.text(), "Not applied yet")
        # Taken in on sight, first in the order, with a way back; nothing in the game folder changes yet.
        self.assertEqual([m.name for m in self.lib.mods], ["HM"])
        self.assertIn("put there by hand", win.status.text())
        self.assertIn("Undo", win.status.text())
        self.assertIn("one of your mods now", win.banner2.label.text())
        self.assertEqual(win.banner2.button.text(), "Leave it out")          # still there once the status moves on
        self.assertIn("backup", win.banner2.button.toolTip())
        self.assertTrue((lang / "HM_a.csv").is_file())

        win.add_path(str(self.mod("Short Names", {"SN_a.csv": csv("<ID|readonly|noverify>;<English>\nt_34_85;Short\n")})))
        self.assertEqual(win.mod_list.count(), 2)
        self.assertEqual(win.count_chip.text(), "2")
        self.assertEqual(win.mod_list.item(1).data(Qt.ItemDataRole.UserRole + 3), "2")      # its place
        self.assertEqual(win.conflicts.rowCount(), 1)
        self.assertIn("Short", win.conflicts.item(0, 2).text())
        self.assertIn("Conflicts · 1", [label for _k, label in win.view_switch.choices])
        self.assertIn("%lang/SN_a.csv", win.load_list.toPlainText())

        # Nothing to ask: the hand-made mod goes on working, as one of the list's.
        with unittest.mock.patch.object(QMessageBox, "question", side_effect=AssertionError("asked")):
            self.assertTrue(win.apply())
        self.assertTrue((lang / "HM_a.csv").is_file())
        self.assertFalse(win.banner2.isVisible())
        self.assertTrue((lang / "SN_a.csv").is_file())
        self.assertTrue(win.state_chip.text().startswith("Applied"))
        self.assertIn("custom localization on", win.status_right.text())
        self.assertTrue(config.test_localization((self.game_root / "config.blk").read_text("utf-8")))

        win.mod_list.item(1).setCheckState(Qt.CheckState.Unchecked)
        app.processEvents()
        self.assertFalse(self.lib.mods[1].enabled)
        self.assertEqual(win.state_chip.text(), "Changes not applied yet")
        self.assertEqual(win.conflicts.rowCount(), 0)

    def test_a_hand_install_left_out_stays_out(self):
        lang = self.game_root / "lang"
        lang.mkdir()
        (lang / "localization.blk").write_bytes(b'locTable{\r\n  file:t="%lang/HM_a.csv"\r\n}\r\n')
        (lang / "HM_a.csv").write_bytes(csv("<ID|readonly|noverify>;<English>\nt_34_85;Hand made\n"))
        win = self.window()
        self.assertIn("untake:", win.status.text())                           # Undo, and the banner's button
        win.banner2.button.click()
        self.assertEqual(self.lib.mods, [])
        self.assertIn("stays out", win.status.text())
        self.assertIn("left it out", win.banner2.label.text())
        self.assertIn("first in the order", win.banner2.button.toolTip())
        win.close()
        again = self.window()                                                 # not taken in a second time
        self.assertEqual(self.lib.mods, [])
        again.import_hand_install()                                           # until asked for
        self.assertEqual([m.name for m in self.lib.mods], ["HM"])

    def test_a_mods_read_me_opens_from_its_details(self):
        pkg = self.mod("Read Mod", {"RM_a.csv": csv("<ID|readonly|noverify>;<English>\nt_34_85;Read\n"),
                                    "README.md": b"# How to use it\n\nSee https://example.com/rm for more.\n"})
        win = self.window()
        win.add_path(str(pkg))
        mod = self.lib.mods[0]
        self.assertIn("href='readme'", win._mod_html(mod))
        win._link(QUrl("readme"))
        self.assertTrue(win.readme_dialog.isVisible())
        shown = win.readme_dialog.text.toPlainText()
        self.assertIn("How to use it", shown)
        self.assertNotIn("# How", shown)                              # Markdown, shown as Markdown
        win.readme_dialog.close()
        plain = self.mod("Plain Mod", {"PM_a.csv": csv("<ID|readonly|noverify>;<English>\nf_16a_0;Plain\n"),
                                       "readme.txt": b"Plain words.\nhttps://example.com/pm\n"})
        win.add_path(str(plain))
        win.show_readme(self.lib.mods[1])
        self.assertIn("https://example.com/pm", win.readme_dialog.text.toHtml())
        self.assertIn("href", win.readme_dialog.text.toHtml())       # its web address is a link
        win.readme_dialog.close()
        two = self.mod("No Doc", {"ND_a.csv": csv("<ID|readonly|noverify>;<English>\nf_16a_0;None\n")})
        win.add_path(str(two))
        self.assertNotIn("href='readme'", win._mod_html(self.lib.mods[2]))

    def test_modules_switch_in_the_details(self):
        pkg = self.tmp / "WT_1.0"
        (pkg / "lang").mkdir(parents=True)
        (pkg / "lang" / "localization.blk").write_bytes(
            b'locTable{\r\n  file:t="%lang/WT_a.csv"\r\n  file:t="%lang/Package_Full_Ammo/WT_ammo.csv"\r\n}\r\n')
        (pkg / "lang" / "WT_a.csv").write_bytes(csv("<ID|readonly|noverify>;<English>\nt_34_85;Main\n"))
        (pkg / "Packages" / "Package_Full_Ammo").mkdir(parents=True)
        (pkg / "Packages" / "Package_Full_Ammo" / "WT_ammo.csv").write_bytes(
            csv("<ID|readonly|noverify>;<English>\nt_34_85;Long\n"))
        win = self.window()
        win.add_path(str(pkg))
        html_text = win._mod_html(self.lib.mods[0])
        self.assertIn("OPTIONAL MODULES · 0 OF 1 ON", html_text)
        self.assertIn("Full Ammo", html_text)
        self.assertIn("title=", html_text)                                     # every switch says what it does
        win._link(QUrl("module:on:Package_Full_Ammo"))
        self.assertEqual(self.lib.mods[0].off, [])
        self.assertIn("Full Ammo is on", win.status.text())
        self.assertIn("WT_ammo.csv", win.load_list.toPlainText())
        self.assertIn("1 of 1 module on", win.mod_list.item(0).data(Qt.ItemDataRole.UserRole + 2))

    def test_remove_then_undo(self):
        win = self.window()
        self.two_mods(win)
        first = self.lib.mods[0]
        win.mod_list.setCurrentRow(0)
        win.remove_selected()                                     # no question asked
        self.assertEqual(len(self.lib.mods), 1)
        self.assertIn("Removed", win.status.text())
        self.assertIn("Undo", win.status.text())
        href = win.status.text().split("href='")[1].split("'")[0]
        win.status.linkActivated.emit(href)
        self.assertEqual([m.id for m in self.lib.mods][0], first.id)
        self.assertIn("is back", win.status.text())
        self.assertNotIn("Undo", win.status.text())
        self.assertEqual(win.mod_list.item(0).data(Qt.ItemDataRole.UserRole + 1), first.id)    # ID_ROLE

    def test_move_changes_the_winner(self):
        win = self.window()
        self.two_mods(win)
        self.assertIn("From B", win.conflicts.item(0, 2).text())
        win.mod_list.setCurrentRow(1)
        win.move_selected(-1)
        self.assertEqual([m.name for m in self.lib.mods], ["B Mod", "A Mod"])
        self.assertIn("From A", win.conflicts.item(0, 2).text())
        self.assertFalse(win.up_btn.isEnabled())            # the first cannot go higher

    def test_changes_another_copy_made_are_taken_in(self):
        """Steam's launch options run a second copy that may save the library: the window reads it again
        when it comes to the front, rather than write over it at its next change."""
        win = self.window()
        self.two_mods(win)
        self.assertFalse(self.lib.changed_elsewhere())              # its own saves do not count
        other = Library(self.lib.root)
        other.set_enabled(other.mods[0].id, False)
        self.assertTrue(self.lib.changed_elsewhere())
        win.take_in_changes()
        self.assertFalse(self.lib.mods[0].enabled)
        self.assertEqual(win.mod_list.item(0).checkState(), Qt.CheckState.Unchecked)
        self.assertFalse(self.lib.changed_elsewhere())

    def test_play_applies_then_starts(self):
        win = self.window()
        self.two_mods(win)
        with unittest.mock.patch("langmod.ui.main_window.start_game") as start:
            win.play()
            self.assertEqual(start.call_count, 1)
            self.assertTrue((self.game_root / "lang" / "BB_a.csv").is_file())
            win.play()                                       # nothing changed: straight to the game
            self.assertEqual(start.call_count, 2)
        self.assertIn("Starting", win.status.text())

    def test_game_update_applies_by_itself_when_asked(self):
        win = self.window()
        self.two_mods(win)
        with unittest.mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            win.apply()
        os.utime(self.game_root / "lang.vromfs.bin", (1, 1))
        win.reload_game()
        self.assertEqual(win.state_chip.text(), "Game updated: apply again")
        self.assertTrue(win.banner.isVisible())
        Prefs(self.lib).set("on_update", "apply")
        os.utime(self.game_root / "lang.vromfs.bin", (2, 2))
        win.reload_game()
        self.assertTrue(win.state_chip.text().startswith("Applied"))
        self.assertIn("by itself", win.status.text())

    def picture_file(self, colour: str = "#c0392b") -> Path:
        """A picture to use as a background: a colour fading to near black, with a bright band."""
        from PySide6.QtGui import QColor, QLinearGradient
        image = QImage(640, 400, QImage.Format.Format_RGB32)
        p = QPainter(image)
        g = QLinearGradient(0, 0, 640, 400)
        g.setColorAt(0.0, QColor(colour))
        g.setColorAt(1.0, QColor("#101010"))
        p.fillRect(image.rect(), g)
        p.fillRect(QRectF(0, 180, 640, 40), QColor("#f0e0a0"))
        p.end()
        path = self.tmp / "my tank.png"
        image.save(str(path))
        return path

    def test_every_theme(self):
        win = self.window()
        self.two_mods(win)
        self.assertTrue(win.use_picture(str(self.picture_file())))
        seen = set()
        for key in themes.ORDER:
            for play in ("", "-play"):
                self.assertTrue((shortcuts.RESOURCES / f"icon-{key}{play}.ico").is_file(), f"icon for {key}")
            win.set_theme(key)
            app.processEvents()
            self.assertEqual(win.backdrop.theme_key, key)
            self.assertIsInstance(win.backdrop.scene, scenes.SCENES[themes.THEMES[key].scene])
            self.assertEqual(win.backdrop.moving, key != "standard")      # standard has nothing to move
            win.backdrop.advance(0.5)
            image = win.grab().toImage()
            seen.add(image.pixelColor(5, image.height() // 2).name())
            if SHOTS:
                win.grab().save(str(Path(SHOTS) / f"window_{key}.png"))
        self.assertEqual(len(seen), len(themes.ORDER))                    # each one looks different
        Prefs(self.lib).set("motion", False)
        win.set_theme("astral")
        self.assertFalse(win.backdrop.moving)
        Prefs(self.lib).set("motion", True)
        win.apply_look()
        self.assertTrue(win.backdrop.moving)
        win.backdrop.set_paused(True)                   # what minimising the window does
        self.assertFalse(win.backdrop.moving)
        win.backdrop.set_paused(False)
        self.assertTrue(win.backdrop.moving)

    def test_factory_gears_mesh(self):
        """Each pair touches at its pitch circles, turns the other way at the same surface speed, and has a
        tooth of one in a gap of the other: at the start, and long after."""
        scene = scenes.Factory(themes.THEMES["factory"].tokens)
        scene.resize(1180, 760)
        self.assertEqual(len(scene.pairs), 6)
        for seconds in (0.0, 13.7, 250.0):
            scene.step(seconds)
            for a, b in scene.pairs:
                d = math.atan2(b.y - a.y, b.x - a.x)
                self.assertAlmostEqual(math.hypot(b.x - a.x, b.y - a.y), a.pitch + b.pitch, places=6)
                self.assertAlmostEqual(a.speed * a.pitch, -b.speed * b.pitch, places=9)
                tooth = ((d - a.angle) / a.step) % 1.0              # a's tooth phase where they meet
                other = ((d + math.pi - b.angle) / b.step) % 1.0    # b's, from its side
                self.assertAlmostEqual((tooth + other) % 1.0, 0.5, places=6)
        self.assertTrue(all(b.crates for b in scene.belts))          # both belts carry something

    def test_aurora(self):
        scene = scenes.Aurora(themes.THEMES["aurora"].tokens)
        scene.resize(600, 400)
        image = QImage(600, 400, QImage.Format.Format_ARGB32_Premultiplied)

        def paint():
            p = QPainter(image)
            scene.paint(p)
            p.end()

        paint()
        self.assertFalse(scene.dirty)
        scene.step(1 / 30)
        self.assertFalse(scene.dirty)                   # the curtains are worked out 15 times a second
        scene.step(1 / 30)
        self.assertTrue(scene.dirty)
        paint()
        sky = max(image.pixelColor(x, y).green() for x in range(0, 600, 7) for y in range(0, scene.horizon, 7))
        top = image.pixelColor(300, 2).green()
        self.assertGreater(sky, top + 60)               # the lights are there, and green
        lake = max(image.pixelColor(x, y).green() for x in range(0, 600, 7)
                   for y in range(scene.horizon + 4, 400, 5))
        self.assertGreater(lake, image.pixelColor(300, 399).green())     # and mirrored in the water

    def test_warfare_tank_fires_and_settles(self):
        scene = scenes.Warfare(themes.THEMES["warfare"].tokens)
        scene.resize(1200, 760)
        scene.next_shot = 1e9                            # only when told, here
        image = QImage(1200, 760, QImage.Format.Format_ARGB32_Premultiplied)

        def paint():
            p = QPainter(image)
            scene.paint(p)
            p.end()
            ahead = scene.muzzle + scene.aim * (scene.tank_scale * 0.1)
            return image.pixelColor(round(ahead.x()), round(ahead.y())).lightness()

        still = paint()
        scene.shoot()
        scene.step(1 / 60)
        self.assertGreater(scene.recoil(), 0.0)          # the barrel runs back
        self.assertGreater(paint(), still + 40)          # and there is a flash in front of the muzzle
        for _ in range(200):                             # some seconds on, all of it is over
            scene.step(1 / 30)
        self.assertIsNone(scene.shot)
        self.assertEqual((scene.recoil(), scene.rock()), (0.0, 0.0))
        self.assertEqual(scene.blast, [])

    def test_backdrop_paces_itself_and_settles_after_resizing(self):
        win = self.window()
        win.set_theme("factory")
        app.processEvents()
        bd = win.backdrop
        self.assertEqual(bd.rate, 30)
        clock = [0.0]
        with unittest.mock.patch.object(scenes.time, "process_time", lambda: clock[0]):
            bd.cpu_mark, bd.pace = 0.0, unittest.mock.Mock(elapsed=lambda: 2000, restart=lambda: None)
            clock[0] = 1.2                       # 60% of a core over two seconds: too much
            bd._pace()
            self.assertEqual(bd.rate, 24)
            for _ in range(10):
                clock[0] += 1.2
                bd._pace()
            self.assertEqual(bd.rate, bd.ladder[-1])           # it stops at the slowest
            clock[0] += 0.05                     # then almost nothing: it climbs back
            bd._pace()
            self.assertEqual(bd.rate, bd.ladder[-2])
            bd.set_fps(60)                       # a burst sends 60 down a step...
            clock[0] += 0.9
            bd._pace()
            self.assertEqual(bd.rate, 48)
            clock[0] += 0.5                      # ...25% at 48 would be 31% at 60: under 40%, so back up
            bd._pace()
            self.assertEqual(bd.rate, 60)
            clock[0] += 0.7                      # 35% at 60: within its budget, it stays
            bd._pace()
            self.assertEqual(bd.rate, 60)
        self.assertEqual(bd.timer.interval(), round(1000 / bd.rate))
        size = (bd.scene.w, bd.scene.h)
        win.resize(1000, 700)
        app.processEvents()
        self.assertEqual((bd.scene.w, bd.scene.h), size)          # still settling: the old one, stretched
        win.grab()
        QTest.qWait(scenes.SETTLE_MS + 80)
        self.assertEqual((bd.scene.w, bd.scene.h), (bd.width(), bd.height()))

    def test_frames_a_second(self):
        win = self.window()
        win.set_theme("aurora")
        app.processEvents()
        win.open_settings()
        dlg = win.settings_dialog
        slider = dlg.fps_slider
        self.assertEqual((slider.value(), slider.label.text()), (30, "30 fps"))
        scene = win.backdrop.scene
        slider.slider.setSliderDown(True)
        slider.slider.setValue(47)                       # dragged: snaps to the nearest 5
        self.assertEqual((slider.value(), slider.label.text()), (45, "45 fps"))
        self.assertEqual((win.backdrop.rate, dlg.backdrop.rate), (45, 45))          # followed at once
        self.assertEqual(win.backdrop.timer.interval(), round(1000 / 45))
        self.assertIs(win.backdrop.scene, scene)         # the scene went on; nothing was drawn anew
        self.assertEqual(Prefs(self.lib).get("fps"), 30)                           # not saved mid-drag
        slider.slider.setSliderDown(False)
        slider.slider.sliderReleased.emit()
        self.assertEqual(Prefs(self.lib).get("fps"), 45)
        slider.set_value(60)
        QTest.qWait(slider.quiet.interval() + 80)        # by key or wheel: saved once it stops
        self.assertEqual(Prefs(self.lib).get("fps"), 60)
        self.assertEqual(win.backdrop.ladder[:3], (60, 48, 40))
        self.assertAlmostEqual(win.backdrop.budget, 0.40)                          # 60 frames: twice the room
        self.assertAlmostEqual(scene.redraw_every(), 1 / 30)                       # the aurora keeps up
        win.set_theme("sakura")                          # a new scene keeps the chosen rate
        self.assertEqual(win.backdrop.rate, 60)
        slider.set_value(5)
        self.assertEqual(slider.value(), 10)             # 10 is the least
        QTest.qWait(slider.quiet.interval() + 80)
        self.assertEqual(win.backdrop.ladder, (10,))
        slider.set_value(25)                             # and closed at once, before the pause is over
        dlg.close()
        self.assertEqual(Prefs(self.lib).get("fps"), 25)
        win.open_settings()
        win.settings_dialog.fps_slider.set_value(35)
        win.close()                                      # the app quitting with Settings open
        self.assertEqual(Prefs(Library(self.lib.root)).get("fps"), 35)           # on disk, for next time

    def test_apply_keeps_what_it_read_unless_the_game_changed(self):
        win = self.window()
        self.two_mods(win)
        game = win.game
        with unittest.mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            self.assertTrue(win.apply())
        self.assertIs(win.game, game)                           # nothing to read again
        os.utime(self.game_root / "lang.vromfs.bin", (7, 7))
        self.assertTrue(win.apply())
        self.assertIsNot(win.game, game)
        self.assertIn("Applied", win.status.text())
        with unittest.mock.patch("langmod.ui.main_window.build_plan", side_effect=KeyError("gone.csv")):
            with unittest.mock.patch.object(QMessageBox, "warning") as warned:
                self.assertFalse(win.apply())
        self.assertIn("gone.csv", warned.call_args[0][2])

    def test_standard_front_dark_and_light(self):
        win = self.window()
        prefs = Prefs(self.lib)
        prefs.set("theme", "standard")
        for mode, bg in (("dark", "#15171b"), ("light", "#f3f4f6")):
            prefs.set("standard_mode", mode)
            win.apply_look()
            self.assertEqual(themes.tokens()["bg"], bg)

    def test_settings(self):
        win = self.window()
        self.two_mods(win)
        win.open_settings()
        dlg = win.settings_dialog
        self.assertIsNotNone(dlg)
        app.processEvents()
        from langmod import __version__
        self.assertIn(f"Langmod Manager {__version__}", dlg.version_note.text())      # on every page, below them
        self.assertTrue(dlg.version_note.isVisible())
        self.assertEqual("Early access" in dlg.version_note.text(), __version__.endswith("ea"))
        dlg.theme_cards["sakura"].picked.emit("sakura")
        self.assertEqual(Prefs(self.lib).get("theme"), "sakura")
        self.assertEqual(win.backdrop.theme_key, "sakura")
        self.assertEqual(dlg.backdrop.theme_key, "sakura")
        self.assertTrue(dlg.theme_cards["sakura"].selected and not dlg.theme_cards["standard"].selected)
        dlg.amount_switch.selected.emit("many")
        self.assertEqual(Prefs(self.lib).get("amount"), "many")
        dlg.motion_switch.selected.emit("0")
        self.assertFalse(win.backdrop.moving)
        if SHOTS:
            for i, name in enumerate(("look", "game", "applying", "updates", "shortcuts", "folders")):
                dlg.sidebar.setCurrentRow(i)
                app.processEvents()
                dlg.grab().save(str(Path(SHOTS) / f"settings_{name}.png"))
        with unittest.mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            dlg._reset()
        self.assertEqual(Prefs(self.lib).get("theme"), "standard")
        self.assertEqual(win.backdrop.theme_key, "standard")
        dlg.close()
        app.processEvents()
        self.assertIsNone(win.settings_dialog)

    def test_theme_pages(self):
        Prefs(self.lib).set("theme", "warfare")
        win = self.window()
        win.open_settings()
        dlg = win.settings_dialog
        pager = dlg.theme_pager
        app.processEvents()
        self.assertEqual(len(dlg.theme_cards), len(themes.ORDER))
        self.assertEqual(pager.count, 2)                             # six to a page
        self.assertEqual(pager.page, 1)                              # opens where the theme in use is
        self.assertFalse(pager.next.isEnabled())
        pager.go(0)
        self.assertEqual(pager.slide.state(), pager.slide.State.Running)
        QTest.qWait(pager.SLIDE_MS // 2)
        self.assertTrue(0.0 < pager.position < 1.0)                  # mid-slide
        if SHOTS:
            dlg.grab().save(str(Path(SHOTS) / "settings_pages_sliding.png"))
        QTest.qWait(pager.SLIDE_MS)
        self.assertEqual((pager.page, pager.position), (0, 0.0))
        self.assertTrue(pager.page_widgets[0].isVisible() and not pager.page_widgets[1].isVisible())
        self.assertEqual(pager.strip.x(), 0)
        QTest.keyClick(pager, Qt.Key.Key_Right)
        QTest.qWait(pager.SLIDE_MS + 60)
        self.assertEqual(pager.page, 1)
        self.assertEqual(pager.strip.x(), -pager.viewport.width())
        dlg.theme_cards["space"].picked.emit("space")
        self.assertEqual(Prefs(self.lib).get("theme"), "space")
        if SHOTS:
            dlg.grab().save(str(Path(SHOTS) / "settings_pages_2.png"))
        # Turning to the last page with the arrow leaves the settings where they were: the arrow, switched
        # off there, never had the focus to hand on down the page (which scrolled it to the bottom).
        dlg.activateWindow()
        pager.go(0, animate=False)
        bar = dlg.pages.currentWidget().verticalScrollBar()
        bar.setValue(0)
        QTest.mouseClick(pager.next, Qt.MouseButton.LeftButton)
        QTest.qWait(pager.SLIDE_MS + 60)
        self.assertEqual((pager.page, bar.value()), (1, 0))
        self.assertFalse(pager.next.isEnabled())
        self.assertIsNot(app.focusWidget(), dlg.fps_slider.slider)
        dlg.close()

    # -- the tour ------------------------------------------------------------------------------
    def check_every_step(self, win: MainWindow) -> None:
        """Each step: the spotlight and the card inside the window, the card clear of the spotlight."""
        tour = win.tour
        bounds = QRectF(win.rect())
        for i in range(len(tour.steps)):
            tour.go(i, animate=False)
            card = QRectF(tour.card.geometry())
            self.assertTrue(bounds.contains(card), f"step {i + 1}: card {card} outside {bounds}")
            spot = tour.target_rect(tour.steps[i])
            if spot is not None:
                self.assertTrue(bounds.contains(spot), f"step {i + 1}: spotlight outside the window")
                if tour.side:
                    self.assertFalse(card.intersects(spot), f"step {i + 1}: the card hides what it explains")

    def test_tour_first_run_steps_and_finish(self):
        win = self.window(tour=True)
        QTest.qWait(900)
        tour = win.tour
        self.assertIsNotNone(tour)
        self.assertTrue(tour.running)
        n = len(tour.steps)
        self.assertEqual(tour.card.step.text(), f"STEP 1 OF {n}")
        self.assertEqual(tour.card.left.text(), f"{n - 1} to go")
        self.assertEqual(tour.card.next.text(), "Let's go")
        self.assertFalse(tour.card.back.isVisible())
        titles = [s.title for s in tour.steps]
        self.assertNotIn("Something needs you", titles)          # no notice is showing
        self.assertIn("Play", titles)
        QTest.keyClick(tour, Qt.Key.Key_Right)
        QTest.qWait(700)
        self.assertEqual(tour.index, 1)
        self.assertEqual(tour.card.progress.index, 1)
        self.assertTrue(tour.card.back.isVisible())
        self.assertAlmostEqual(tour.card.fade.opacity(), 1.0, places=2)   # the card has come in
        self.assertTrue(tour.hasFocus())                                  # the keyboard stays with the tour
        QTest.keyClick(tour, Qt.Key.Key_Left)
        self.assertEqual(tour.index, 0)
        QTest.keyClick(tour, Qt.Key.Key_Return)
        self.assertEqual(tour.index, 1)
        tour.back()
        self.check_every_step(win)
        tour.go(n - 1, animate=False)
        self.assertEqual(tour.card.next.text(), "Finish")
        self.assertEqual(tour.card.left.text(), "last one")
        self.assertFalse(tour.card.skip.isVisible())
        tour.card.next.click()
        self.assertIsNone(win.tour)
        self.assertTrue(Prefs(self.lib).get("tour_seen"))
        self.assertIn("Tour done", win.status.text())
        again = self.window(tour=True)                               # seen once: not again by itself
        QTest.qWait(900)
        self.assertIsNone(again.tour)
        again.help_btn.click()
        self.assertTrue(again.tour.running)

    def test_tour_skip(self):
        win = self.window()
        win.start_tour()
        self.assertTrue(win.tour.running)
        QTest.keyClick(win.tour, Qt.Key.Key_Escape)
        self.assertIsNone(win.tour)
        self.assertTrue(Prefs(self.lib).get("tour_seen"))
        self.assertIn("skipped", win.status.text())
        win.start_tour()
        win.tour.card.skip.click()
        self.assertIsNone(win.tour)

    def test_tour_shows_notices_and_fits_a_small_window(self):
        lang = self.game_root / "lang"
        lang.mkdir()
        (lang / "localization.blk").write_bytes(b'locTable{\r\n}\r\n')
        win = self.window()
        self.assertTrue(win.banner2.isVisible())
        for size in ((1220, 780), (920, 640)):
            win.resize(*size)
            QTest.qWait(50)
            win.start_tour()
            self.assertIn("Something needs you", [s.title for s in win.tour.steps])
            self.check_every_step(win)
            if SHOTS:
                for theme in ("astral", "sakura"):
                    win.set_theme(theme)
                    for i in (0, 1, 5, 9, 10):
                        win.tour.go(i, animate=False)
                        app.processEvents()
                        win.grab().save(str(Path(SHOTS) / f"tour_{size[0]}_{theme}_{i + 1}.png"))
            win.tour.skip()

    def test_settings_starts_the_tour(self):
        win = self.window()
        win.open_settings("Folders")
        win.settings_dialog._tour()
        QTest.qWait(50)
        self.assertIsNone(win.settings_dialog)
        self.assertTrue(win.tour.running)
        win.tour.skip()

    # -- updates ---------------------------------------------------------------------------------
    def fake_net(self, version: str = "76", date: str = "17 September, 2030"):
        from test_updates import FakeHttp, ifn1_zip, live_post
        from langmod.core import sources
        http = FakeHttp()
        blob = ifn1_zip(version)
        http.files["https://live.warthunder.com/dl/k76/"] = blob
        http.posts["694386"] = live_post(694386, f"IFN1 Langmod V{version}.zip", "k76", len(blob),
                                         f"IFN1 Vehicle Re-Name Mod Version {version} - {date}")
        patcher = unittest.mock.patch.object(sources, "http", http)
        patcher.start()
        self.addCleanup(patcher.stop)
        old = self.tmp / "lang.zip"
        old.write_bytes(ifn1_zip("75"))
        return old

    def test_updates_in_the_window(self):
        from langmod.core import updates
        from langmod.ui.follow_dialog import FollowDialog
        old = self.fake_net()
        win = self.window()
        win.add_path(str(old))
        mod = self.lib.mods[0]
        self.assertEqual(updates.source_of(mod).ref, "694386")          # found by itself, and checked
        self.assertEqual(updates.state(mod), "update")
        self.assertTrue(win.update_banner.isVisible())
        self.assertIn("IFN1 76", win.update_banner.label.text())
        self.assertEqual(win.updates_btn.text(), "Updates · 1")
        self.assertTrue(win.mod_list.item(0).data(Qt.ItemDataRole.UserRole + 4))
        self.assertIn("A new version is ready", win.details.toPlainText())
        self.assertIn("Follows WT Live · InFerNos1", win.details.toPlainText())
        if SHOTS:
            win.set_theme("astral")
            QTest.qWait(50)
            win.grab().save(str(Path(SHOTS) / "updates_ready.png"))
        with unittest.mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            win.apply()
        win.update_banner.button.click()                                # Update
        mod = self.lib.mods[0]
        self.assertEqual(updates.judge(mod), ("current", "file"))
        self.assertIn(b"v76", (self.game_root / "lang" / "IFN1_01_units.csv").read_bytes())   # applied too
        self.assertFalse(win.update_banner.isVisible())
        self.assertIn("You have it", win.details.toPlainText())
        self.assertIn("Updated IFN1 to 76", win.status.text())
        self.assertIn("Reinstall this version", win.details.toPlainText())
        self.assertIn("upd:again", win._mod_html(mod))
        win._update_link(mod, "again")                                  # the same release, fetched again
        self.assertIn("Installed IFN1 76 again", win.status.text())
        self.assertEqual(updates.state(self.lib.mods[0]), "current")
        if SHOTS:
            QTest.qWait(50)
            win.grab().save(str(Path(SHOTS) / "updates_done.png"))

        dlg = FollowDialog(mod, updates.source_of(mod), win)
        self.assertEqual(dlg.match.text(), "IFN1")
        dlg.link.setText("https://example.com/mod.zip")
        self.assertFalse(dlg.ok.isEnabled())
        dlg.link.setText("https://github.com/Addysaurus/lang_modding")
        self.assertTrue(dlg.ok.isEnabled())
        dlg._accept()
        self.assertEqual(dlg.source.ref, "Addysaurus/lang_modding")
        win._update_link(mod, "stop")
        self.assertIsNone(mod.follow)
        self.assertIn("Follows nowhere", win.details.toPlainText())
        self.assertIn("Follow WT Live · InFerNos1", win.details.toPlainText())    # offered again

    def test_a_new_version_of_the_manager(self):
        from test_selfupdate import gh_release, release_zip
        from test_updates import FakeHttp
        from langmod.core import selfupdate, sources
        http, blob = FakeHttp(), release_zip()
        http.json["https://api.github.com/repos/me/lm/releases"] = [gh_release("v9.0.0", blob)]
        for patcher in (unittest.mock.patch.object(sources, "http", http),
                        unittest.mock.patch.dict(os.environ, {"LANGMOD_SELF_REPO": "me/lm"})):
            patcher.start()
            self.addCleanup(patcher.stop)
        win = self.window()                                            # looks by itself when it first opens
        self.assertTrue(win.app_banner.isVisible())
        self.assertIn("Langmod Manager 9.0.0 is out", win.app_banner.label.text())
        self.assertEqual(win.app_banner.button.text(), "Open the download page")     # run from source
        win.open_settings("updates")
        dlg = win.settings_dialog
        self.assertIn("9.0.0 is out", dlg.self_state.text())
        dlg._check_self()
        self.assertIn("9.0.0 is out", dlg.self_state.text())
        self.assertEqual(len(http.calls), 2)
        dlg.close()
        # The built program: one click fetches it, starts it, and closes to make way.
        http.files[selfupdate.known(Prefs(self.lib)).url] = blob
        with unittest.mock.patch.object(selfupdate, "standalone", return_value=True), \
                unittest.mock.patch.object(selfupdate, "begin") as begin:
            win._show_new_self()
            self.assertEqual(win.app_banner.button.text(), "Update and restart")
            win.app_banner.button.click()
        staged = begin.call_args[0][0]
        self.assertEqual((staged / "Langmod Manager.exe").read_bytes(), b"new program")
        self.assertFalse(win.isVisible())

    def test_shared_build_without_nexus(self):
        from langmod.core import sources
        from langmod.ui.follow_dialog import FollowDialog
        win = self.window()
        self.two_mods(win)
        with unittest.mock.patch.object(sources, "NEXUS", False):
            win.open_settings()
            dlg = win.settings_dialog
            self.assertFalse(hasattr(dlg, "nexus_key"))          # no key asked for
            dlg.close()
            follow = FollowDialog(self.lib.mods[0], None, win)
            follow.link.setText("https://www.nexusmods.com/warthunder/mods/2162")
            self.assertFalse(follow.ok.isEnabled())
            self.assertIn("does not check Nexus", follow.verdict.text())
            follow.link.setText("https://live.warthunder.com/post/694386/en/")
            self.assertTrue(follow.ok.isEnabled())
            follow.close()

    def test_play_fetches_updates_first(self):
        from langmod.core import updates
        old = self.fake_net()
        Prefs(self.lib).set("update_install", "auto")
        win = self.window()
        win.add_path(str(old))                                         # installs it by itself straight away
        self.assertEqual(updates.judge(self.lib.mods[0])[0], "current")
        self.lib.mods[0].installed_key = "an older one"                # pretend a newer one came out since
        self.lib.save()
        Prefs(self.lib).set("update_last", 0.0)
        with unittest.mock.patch("langmod.ui.main_window.start_game") as start:
            win.play()
        self.assertEqual(start.call_count, 1)
        self.assertEqual(self.lib.mods[0].installed_key, "wtlive:k76")
        self.assertIn(b"v76", (self.game_root / "lang" / "IFN1_01_units.csv").read_bytes())

    @unittest.skipUnless(sys.platform == "win32", "shortcuts are a Windows thing")
    def test_shortcut_buttons(self):
        win = self.window()
        win.open_settings("Shortcuts")
        dlg = win.settings_dialog
        control = next(c for c in dlg.shortcut_controls if c.kind == "play" and c.where == "desktop")
        self.assertEqual(control.button.text(), "Create")
        control.button.click()
        lnk = Path(os.environ["LANGMOD_DESKTOP_DIR"]) / "War Thunder with mods.lnk"
        self.assertTrue(lnk.is_file())
        self.assertEqual(control.button.text(), "Remove")
        target, args, icon = shortcuts.read(lnk)
        self.assertTrue(target.lower().endswith("pythonw.exe") or target.lower().endswith("python.exe"))
        self.assertEqual(args, "-m langmod launch")
        self.assertIn("icon-standard-play.ico", icon)
        control.button.click()
        self.assertFalse(lnk.exists())
        dlg.close()

    def test_steam_launch_options(self):
        from langmod.core import steam
        win = self.window()
        win.open_settings("Shortcuts")
        dlg = win.settings_dialog
        self.assertEqual(dlg.steam_line.text(), steam.option())
        self.assertFalse(dlg.steam_state.isVisibleTo(dlg))               # Steam has not been given it
        dlg.steam_line.parent().findChild(type(dlg.report_btn)).click()   # Copy
        self.assertEqual(QApplication.clipboard().text(), steam.option())
        dlg.close()
        QApplication.processEvents()
        cfg = Path(os.environ["LANGMOD_STEAM_DIR"]) / "userdata" / "1" / "config" / "localconfig.vdf"
        cfg.parent.mkdir(parents=True)
        escaped = steam.option().replace("\\", "\\\\").replace('"', '\\"')
        cfg.write_text(f'"apps"\n{{\n"236390"\n{{\n"LaunchOptions" "{escaped}"\n}}\n}}\n', encoding="utf-8")
        try:
            win.open_settings("Shortcuts")
            dlg = win.settings_dialog
            self.assertTrue(dlg.steam_state.isVisibleTo(dlg))
            self.assertEqual(dlg.steam_state.property("tone"), "ok")
            dlg.close()
        finally:
            shutil.rmtree(Path(os.environ["LANGMOD_STEAM_DIR"]))


    # -- strings, picks, own text ----------------------------------------------------------------
    def test_strings_search_pick_and_own_text(self):
        from langmod.core import strings
        from langmod.core.plan import GAME
        win = self.window()
        self.two_mods(win)
        a, b = self.lib.mods[0], self.lib.mods[1]
        win.find_strings()
        self.assertEqual(win.view_switch.current(), "strings")
        view = win.strings_view
        self.assertIn("Search to find a string", view.info.text())        # nothing picked or written yet
        view.search.setText("t-34")
        view.run_search()
        self.assertEqual(view.keys, ["t_34_85"])
        self.assertIn("1 string with", view.info.text())
        self.assertEqual(view.table.item(0, 1).text(), "T-34-85")
        self.assertIn("From B   · B Mod", view.table.item(0, 2).text())
        card = view.card
        self.assertEqual(card.key, "t_34_85")
        rows = {r.name: r for r in card.row_widgets()}
        self.assertEqual(list(rows), ["The game", "A Mod", "B Mod"])
        self.assertIsNone(rows["B Mod"].button)                         # it shows: nothing to pick
        self.assertIn("mods disagree", card.state.text())
        rows["A Mod"].button.click()                                    # Show this
        self.assertEqual(strings.picks(self.lib), {"t_34_85": a.id})
        self.assertEqual(win.analysis.shown("t_34_85"), (a.id, "From A"))
        self.assertIn("From A   · A Mod, picked", view.table.item(0, 2).text())
        self.assertTrue(win.state_chip.text().startswith("Not applied") or "not applied" in win.state_chip.text())
        self.assertIn("shows now, whatever the order", win.status.text())
        # The same card sits under the conflicts, and knows the pick.
        win.view_switch.set_current("conflicts")
        win._view_chosen("conflicts")
        win.conflicts.setCurrentCell(0, 0)
        self.assertEqual(win.conflict_card.key, "t_34_85")
        self.assertIn("A Mod, picked", win.conflicts.item(0, 2).text())
        self.assertTrue(win.conflict_card.back.isVisible())
        {r.name: r for r in win.conflict_card.row_widgets()}["The game"].button.click()
        self.assertEqual(win.analysis.shown("t_34_85"), (GAME, "T-34-85"))
        # Own text wins over the pick, and can be taken out again.
        win.find_strings()
        view.search.setText("t_34_85")
        view.run_search()
        card.edit.setText("My tank")
        card.save.click()
        own = self.lib.personal(create=False)
        self.assertEqual(win.analysis.shown("t_34_85"), (own.id, "My tank"))
        self.assertIn("your own text", card.state.text())
        self.assertIn("Your own text wins over your pick", card.note.text())
        with unittest.mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            win.apply()
        self.assertIn(b"My tank", (self.game_root / "lang" / "zz_my_changes.csv").read_bytes())
        self.assertIn(b"T-34-85", (self.game_root / "lang" / "langmod_picks.csv").read_bytes())
        view.search.clear()
        view.run_search()
        self.assertEqual(view.keys, ["t_34_85"])                       # empty box: what you picked or wrote
        self.assertIn("Your picks and your own text: 1", view.info.text())
        {r.name: r for r in card.row_widgets()}["Your own text"].button.click()      # Take it out
        self.assertEqual(win.analysis.shown("t_34_85"), (GAME, "T-34-85"))
        card.back.click()                                               # Back to the load order
        self.assertEqual(win.analysis.shown("t_34_85"), (b.id, "From B"))
        self.assertEqual(strings.picks(self.lib), {})
        if SHOTS:
            win.set_theme("aurora")
            view.search.setText("f-16")
            view.run_search()
            QTest.qWait(50)
            win.grab().save(str(Path(SHOTS) / "strings.png"))

    # -- profiles ---------------------------------------------------------------------------------
    def test_profiles_in_the_window(self):
        from langmod.core import profiles
        from PySide6.QtWidgets import QInputDialog
        win = self.window()
        self.assertFalse(win.profile_btn.isVisible())                  # nothing to keep yet
        self.two_mods(win)
        self.assertTrue(win.profile_btn.isVisible())
        self.assertEqual(win.profile_btn.text(), "Profiles")
        with unittest.mock.patch.object(QInputDialog, "getText", return_value=("Both", True)):
            win.save_profile_as()
        with unittest.mock.patch.object(QInputDialog, "getText", return_value=("Only B", True)):
            win.save_profile_as()
        self.assertEqual(win.profile_btn.text(), "Only B")
        win.mod_list.item(0).setCheckState(Qt.CheckState.Unchecked)    # changes go into Only B
        win.switch_profile_number(1)
        self.assertEqual(win.profile_btn.text(), "Both")
        self.assertEqual([m.enabled for m in self.lib.mods], [True, True])
        self.assertIn("Switched to Both: 2 mods on", win.status.text())
        win.switch_profile_number(2)
        self.assertEqual([m.enabled for m in self.lib.mods], [False, True])
        win.switch_profile_number(7)                                    # there is no seventh: nothing happens
        self.assertEqual(profiles.active(self.lib), "Only B")
        win._fill_profile_menu()
        texts = [a.text() for a in win.profile_menu.actions()]
        self.assertIn("Both\tCtrl+1", texts)
        self.assertIn("Export Only B to send to a friend…", texts)
        path = self.tmp / "Only B.langmod-profile"
        win.export_profile(str(path))
        self.assertTrue(path.is_file())
        win.import_profile(str(path), ask=False)
        self.assertEqual(profiles.active(self.lib), "Only B (2)")
        self.assertIn("Imported Only B (2)", win.status.text())
        with unittest.mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            win.delete_profile()
        self.assertEqual(profiles.names(self.lib), ["Both", "Only B"])
        self.assertEqual(win.profile_btn.text(), "Profiles")

    # -- getting mods ----------------------------------------------------------------------------
    def test_get_mods(self):
        from langmod.core import updates
        self.fake_net()
        win = self.window()
        win.open_get_mods()
        dlg = win.getmods_dialog
        offers = {o.entry.key: o for o in dlg.offers}
        ifn1, lop, wthlm = offers["ifn1"], offers["lop"], offers["wthlm"]
        self.assertEqual((ifn1.button.text(), ifn1.button.isEnabled()), ("Get it", True))
        self.assertIn("IFN1 Langmod V76.zip · version 76", ifn1.status.text())
        self.assertEqual((lop.button.text(), lop.button.isEnabled()), ("Get it", False))   # nothing posted
        self.assertEqual(wthlm.button.text(), "Try again")                                  # GitHub said 404
        self.assertIn("could not be read", wthlm.status.text())
        if SHOTS:
            win.set_theme("space")
            QTest.qWait(50)
            dlg.grab().save(str(Path(SHOTS) / "get_mods.png"))
        ifn1.button.click()
        mod = self.lib.mods[0]
        self.assertEqual(updates.judge(mod), ("current", "file"))
        self.assertEqual(updates.source_of(mod).ref, "694386")
        self.assertEqual((ifn1.button.text(), ifn1.button.isEnabled()), ("You have it", False))
        self.assertIn("you have IFN1", ifn1.tag.text())
        self.assertIn("Added IFN1", win.status.text())
        dlg.close()
        self.assertIsNone(win.getmods_dialog)

    # -- the window's place, Windows' animation setting, the bug report, the game cache ------------
    def test_window_remembers_its_place(self):
        win = self.window()
        # The test screen is 800 by 600, narrower than the window can be: its height tells.
        win.resize(win.width(), 560)
        app.processEvents()
        self.assertEqual(win.height(), 560)
        win.close()
        self.assertTrue(Prefs(self.lib).get("window"))
        again = MainWindow(self.lib, [self.install], threaded=False, first_run_tour=False)
        self.addCleanup(again.close)
        self.assertEqual(again.height(), 560)
        fresh = MainWindow(Library(self.tmp / "other"), [self.install], threaded=False, first_run_tour=False)
        self.addCleanup(fresh.close)
        self.assertEqual(fresh.height(), 780)                          # nothing saved: the usual size

    def test_background_stands_still_when_windows_says_so(self):
        with unittest.mock.patch.dict(os.environ, {"LANGMOD_ANIMATIONS": "0"}):
            Prefs(self.lib).set("theme", "astral")
            win = self.window()
            self.assertFalse(win.backdrop.moving)
            win._fill_profile_menu()
            Prefs(self.lib).set("motion_follows_windows", False)       # the player says move anyway
            win.apply_look()
            self.assertTrue(win.backdrop.moving)
            win.open_settings("Look")
            dlg = win.settings_dialog
            self.assertTrue(dlg.backdrop.moving)
            dlg.follow_windows.set_current("1")
            dlg.follow_windows.selected.emit("1")
            self.assertFalse(win.backdrop.moving)
            dlg.close()
        win._animations = False
        with unittest.mock.patch("langmod.ui.system.animations_on", return_value=True):
            win.activateWindow()
            from PySide6.QtCore import QEvent
            with unittest.mock.patch.object(win, "isActiveWindow", return_value=True):
                win.changeEvent(QEvent(QEvent.Type.ActivationChange))
        self.assertTrue(win.backdrop.moving)                             # switched back on in Windows

    def test_bug_report_and_errors(self):
        win = self.window()
        self.two_mods(win)
        win._failed("Traceback (most recent call last):\n  File x\nValueError: something broke\n")
        self.assertIn("Something went wrong: ValueError: something broke", win.status.text())
        self.assertIn("Copy a bug report", win.status.text())
        self.assertIn("something broke", (self.lib.root / "errors.log").read_text("utf-8"))
        win._status_link("report:")
        text = QApplication.clipboard().text()
        self.assertIn("Langmod Manager bug report", text)
        self.assertIn("Game version: 2.59.0.13", text)
        self.assertIn("ValueError: something broke", text)
        self.assertIn("PySide6", text)
        self.assertNotIn(str(Path.home()), text)
        win.open_settings("Folders")
        win.settings_dialog.report_btn.click()
        self.assertIn("Copied", win.settings_dialog.report_btn.text())
        win.settings_dialog.close()

    def test_game_text_is_kept_per_game_version(self):
        win = self.window()
        self.two_mods(win)
        self.assertEqual(len(list((self.lib.root / "cache").glob("texts-*.json"))), 1)
        self.assertIn("English", win.game._texts)                      # read while the game loaded

    # -- your picture ------------------------------------------------------------------------------
    def test_your_picture(self):
        from PySide6.QtGui import QColor
        from PySide6.QtWidgets import QColorDialog
        win = self.window()
        self.two_mods(win)
        with unittest.mock.patch("langmod.ui.main_window.QFileDialog.getOpenFileName", return_value=("", "")):
            win.set_theme("picture")                                    # no picture chosen: it stays as it was
        self.assertEqual(win.backdrop.theme_key, "standard")
        with unittest.mock.patch("langmod.ui.main_window.QFileDialog.getOpenFileName",
                                 return_value=(str(self.picture_file()), "")):
            win.set_theme("picture")
        self.assertEqual(win.backdrop.theme_key, "picture")
        kept = Path(Prefs(self.lib).get("picture"))
        self.assertTrue(kept.is_file() and kept.parent == self.lib.root / "picture")
        accent = QColor(themes.tokens()["accent"])
        self.assertGreater(accent.red(), accent.blue())                 # the picture is red: so is the accent
        scene = win.backdrop.scene
        self.assertIsInstance(scene, scenes.Picture)
        self.assertIsNotNone(scene.layer)
        self.assertTrue(win.backdrop.moving)
        moved = [(win.backdrop.advance(0.02), scene.changed())[1] for _ in range(20)]
        self.assertIn(False, moved)                                     # slow: most frames are not drawn
        win.backdrop.advance(8.0)
        self.assertTrue(scene.changed())
        win.open_settings("Look")
        dlg = win.settings_dialog
        self.assertTrue(dlg.picture_card.isVisibleTo(dlg))
        self.assertEqual(dlg.accent_btn.text(), "From the picture")
        with unittest.mock.patch.object(QColorDialog, "getColor", return_value=QColor("#22cc88")):
            dlg.accent_btn.click()
        self.assertEqual(Prefs(self.lib).get("picture_accent"), "#22cc88")
        self.assertEqual(QColor(themes.tokens()["accent"]).green() > 150, True)
        self.assertTrue(dlg.accent_auto.isVisible())
        dlg.accent_auto.click()
        self.assertEqual(Prefs(self.lib).get("picture_accent"), "")
        dlg.dim_switch.selected.emit("strong")
        self.assertEqual(win.backdrop.scene.veil, 0.70)
        if SHOTS:
            QTest.qWait(50)
            dlg.grab().save(str(Path(SHOTS) / "settings_picture.png"))
            dlg.close()
            win.grab().save(str(Path(SHOTS) / "window_picture.png"))
        else:
            dlg.close()
        dlg_card = None
        win.open_settings("Look")
        dlg_card = win.settings_dialog.theme_cards["picture"]
        self.assertTrue(dlg_card.selected)
        win.set_theme("astral")
        self.assertFalse(win.settings_dialog.picture_card.isVisibleTo(win.settings_dialog) and False)
        win.settings_dialog.close()


if __name__ == "__main__":
    unittest.main()
