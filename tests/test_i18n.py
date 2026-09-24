"""The manager in other languages: complete catalogs, counted text, and every screen in each language.

Window tests run offscreen in a temporary home against the fake game, as test_ui does.
"""
from __future__ import annotations

import os
import re
import shutil
import sys
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_SANDBOX = Path(tempfile.mkdtemp(prefix="langmod-i18n-"))
os.environ["LANGMOD_HOME"] = str(_SANDBOX / "home")
os.environ["LANGMOD_DESKTOP_DIR"] = str(_SANDBOX / "Desktop")
os.environ["LANGMOD_PROGRAMS_DIR"] = str(_SANDBOX / "Programs")
os.environ["LANGMOD_STEAM_DIR"] = str(_SANDBOX / "Steam")
os.environ["LANGMOD_SELF_REPO"] = ""
os.environ["LANGMOD_ANIMATIONS"] = "1"

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtWidgets import QAbstractButton, QApplication, QLabel, QMessageBox, QWidget  # noqa: E402

import i18n as catalogs  # noqa: E402  (tools/i18n.py)
from fakegame import build  # noqa: E402
from langmod import i18n  # noqa: E402
from langmod.core.game import GameInstall  # noqa: E402
from langmod.core.library import Library  # noqa: E402
from langmod.core.prefs import Prefs  # noqa: E402
from langmod.ui import system  # noqa: E402

app = QApplication.instance() or QApplication([])
LEFTOVER = re.compile(r"\{[a-z_]+\}")


class Catalogs(unittest.TestCase):
    def tearDown(self):
        i18n.set_language("en")

    def test_every_language_has_every_text(self):
        strings, plurals, problems = catalogs.wanted()
        self.assertEqual(problems, [])
        self.assertGreater(len(strings), 500)
        for code in i18n.LANGUAGES:
            if code == "en":
                continue
            with self.subTest(language=code):
                found = catalogs.check(code)
                self.assertEqual(found["missing"], [], f"{code} lacks texts")
                self.assertEqual(found["wrong"], [], f"{code} has texts that do not fit")
                self.assertEqual(found["spare"], [], f"{code} has texts nothing asks for")

    def test_counted_text(self):
        forms = {"en": {1: 0, 2: 1, 5: 1, 21: 1}, "de": {1: 0, 0: 1, 2: 1},
                 "fr": {0: 0, 1: 0, 2: 1, 100: 1},
                 "ru": {1: 0, 21: 0, 2: 1, 4: 1, 22: 1, 5: 2, 11: 2, 12: 2, 14: 2, 111: 2, 0: 2},
                 "pl": {1: 0, 21: 2, 2: 1, 4: 1, 22: 1, 5: 2, 12: 2, 0: 2, 112: 2},
                 "zh": {1: 0, 2: 0, 5: 0}}
        for code, cases in forms.items():
            for n, want in cases.items():
                self.assertEqual(i18n.plural_form(n, code), want, f"{code} {n}")
        i18n.set_language("ru")
        self.assertEqual(i18n.ntr(1, "{n} file", "{n} files"), "1 файл")
        self.assertEqual(i18n.ntr(3, "{n} file", "{n} files"), "3 файла")
        self.assertEqual(i18n.ntr(25, "{n} file", "{n} files"), "25 файлов")
        i18n.set_language("pl")
        self.assertEqual(i18n.ntr(22, "{n} file", "{n} files"), "22 pliki")
        self.assertEqual(i18n.ntr(12, "{n} file", "{n} files"), "12 plików")
        i18n.set_language("zh")
        self.assertEqual(i18n.ntr(7, "{n} file", "{n} files"), "7 个文件")

    def test_numbers_dates_and_lists(self):
        day = time.mktime((2026, 9, 17, 12, 0, 0, 0, 0, -1))
        for code, big, small, date, dec in (("en", "24,222", "4,222", "17 Sep 2026", "1.5"),
                                            ("de", "24.222", "4.222", "17.09.2026", "1,5"),
                                            ("fr", "24 222", "4 222", "17/09/2026", "1,5"),
                                            ("pl", "24 222", "4222", "17.09.2026", "1,5"),
                                            ("ru", "24 222", "4222", "17.09.2026", "1,5"),
                                            ("zh", "24,222", "4,222", "2026年9月17日", "1.5")):
            i18n.set_language(code)
            self.assertEqual((i18n.num(24222), i18n.num(4222), i18n.date(day), i18n.dec(1.5)),
                             (big, small, date, dec), code)
        self.assertEqual(i18n.join(["a", "b"]), "a、b")
        self.assertEqual(i18n.sentences("一。", "二。"), "一。二。")
        i18n.set_language("de")
        self.assertEqual(i18n.sentences("Eins.", "Zwei."), "Eins. Zwei.")

    def test_choosing_the_language(self):
        with unittest.mock.patch.dict(os.environ, {"LANGMOD_LANGUAGE": ""}):
            self.assertEqual(i18n.choose("pl"), "pl")
            with unittest.mock.patch.object(i18n, "system_language", return_value="ru"):
                self.assertEqual(i18n.choose("system"), "ru")
        with unittest.mock.patch.dict(os.environ, {"LANGMOD_LANGUAGE": "zh"}):
            self.assertEqual(i18n.choose("pl"), "zh")          # tests and screenshots force one
        self.assertEqual(i18n.set_language("xx"), "en")
        self.assertEqual(i18n.tr("Apply to game"), "Apply to game")
        i18n.set_language("fr")
        self.assertEqual(i18n.tr("Apply to game"), "Appliquer au jeu")
        self.assertEqual(i18n.tr("Removed {mod}. Its files leave the game at the next Apply.", mod="X"),
                         "X retiré. Ses fichiers quittent le jeu à la prochaine application.")
        self.assertIn(" :", i18n.tr("Something went wrong: {error}", error="e"))  # French spacing
        self.assertEqual(i18n.game_language("Russian"), "russe")

    def test_qt_speaks_the_language_too(self):
        from PySide6.QtCore import QCoreApplication
        for code in ("de", "fr", "pl", "ru", "zh"):
            self.assertTrue(system.qt_translations(code), code)
        self.assertEqual(QCoreApplication.translate("QPlatformTheme", "Cancel"), "取消")
        system.qt_translations("en")
        self.assertEqual(QCoreApplication.translate("QPlatformTheme", "Cancel"), "Cancel")


class Screens(unittest.TestCase):
    """Every screen, in every language: it opens, and nothing on it is a {name} left unfilled."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="langmod-i18n-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.addCleanup(i18n.set_language, "en")
        self.addCleanup(system.qt_translations, "en")
        self.game_root = build(self.tmp / "War Thunder")
        for target in ("langmod.ui.main_window.game_running",):
            patcher = unittest.mock.patch(target, return_value=False)
            patcher.start()
            self.addCleanup(patcher.stop)

    def texts(self, root: QWidget) -> list[str]:
        out = [root.windowTitle(), root.toolTip()]
        for w in root.findChildren(QWidget):
            out.append(w.toolTip())
            if isinstance(w, (QLabel, QAbstractButton)):
                out.append(w.text())
        return [t for t in out if t]

    def check(self, root: QWidget, where: str) -> None:
        for text in self.texts(root):
            self.assertIsNone(LEFTOVER.search(text), f"{where}: {text!r}")

    def test_every_screen_in_every_language(self):
        from langmod.ui.main_window import MainWindow
        from langmod.ui.tour import steps_for
        for code in ("de", "fr", "pl", "ru", "zh"):
            with self.subTest(language=code), unittest.mock.patch.dict(os.environ, {"LANGMOD_LANGUAGE": code}):
                lib = Library(self.tmp / f"home-{code}")
                win = MainWindow(lib, [GameInstall(self.game_root, "custom")], threaded=False,
                                 first_run_tour=False)
                self.addCleanup(win.close)
                win.resize(1220, 780)
                win.show()
                app.processEvents()
                self.assertEqual(i18n.language(), code)
                self.assertEqual(win.apply_btn.text(), i18n.tr("Apply to game"))
                self.assertNotEqual(win.apply_btn.text(), "Apply to game")
                d = self.tmp / f"mod-{code}"
                d.mkdir()
                (d / "AA_a.csv").write_bytes(b"<ID|readonly|noverify>;<English>\r\nt_34_85;From A\r\n")
                win.add_path(str(d))
                for view in ("details", "strings", "conflicts", "order"):
                    win.view_switch.set_current(view)
                    win._view_chosen(view)
                    self.check(win, f"{code} {view}")
                win.open_settings()
                dlg = win.settings_dialog
                for i in range(dlg.sidebar.count()):
                    dlg.sidebar.setCurrentRow(i)
                    self.check(dlg, f"{code} settings page {i}")
                dlg.close()
                for step in steps_for(win):
                    self.assertIsNone(LEFTOVER.search(step.title + step.body), f"{code} tour {step.title}")
                win.start_tour()
                bounds = win.rect()
                for i in range(len(win.tour.steps)):
                    win.tour.go(i, animate=False)
                    self.assertTrue(bounds.contains(win.tour.card.geometry()), f"{code} tour card {i} off the window")
                win.tour.skip()
                win.close()

    def test_changing_the_language_makes_the_window_again(self):
        from langmod.ui.main_window import MainWindow
        with unittest.mock.patch.dict(os.environ, {"LANGMOD_LANGUAGE": ""}):
            lib = Library(self.tmp / "home")
            Prefs(lib).set("ui_language", "en")
            win = MainWindow(lib, [GameInstall(self.game_root, "custom")], threaded=False, first_run_tour=False)
            win.resize(1220, 700)
            win.show()
            win.open_settings("look")
            dlg = win.settings_dialog
            box = dlg.language_box
            self.assertEqual(box.currentData(), "en")
            self.assertEqual([box.itemData(i) for i in range(box.count())],
                             ["system", "en", "de", "fr", "pl", "ru", "zh"])
            box.setCurrentIndex(box.findData("de"))
            dlg._set_language("de")
            new = win.change_language()
            self.addCleanup(new.close)
            self.assertFalse(win.isVisible())
            self.assertTrue(new.isVisible())
            self.assertEqual(Prefs(lib).get("ui_language"), "de")
            self.assertEqual(new.apply_btn.text(), "Ins Spiel übernehmen")
            self.assertEqual(new.height(), 700)                         # where and as big as it was
            self.assertIsNotNone(new.settings_dialog)                   # Settings open again
            self.assertEqual(new.settings_dialog.current_page(), "look")
            self.assertEqual(new.settings_dialog.windowTitle(), "Einstellungen")
            new.settings_dialog.close()

    def test_putting_everything_back_brings_back_the_language_too(self):
        from langmod.ui.main_window import MainWindow
        with unittest.mock.patch.dict(os.environ, {"LANGMOD_LANGUAGE": ""}):
            other = "fr" if i18n.system_language() != "fr" else "de"   # anything but what Windows is in
            lib = Library(self.tmp / "home")
            Prefs(lib).set("ui_language", other)
            win = MainWindow(lib, [GameInstall(self.game_root, "custom")], threaded=False, first_run_tour=False)
            self.addCleanup(win.close)
            win.show()
            win.open_settings("look")
            changed = []
            win.settings_dialog.language_changed.connect(lambda: changed.append(True))
            with unittest.mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
                win.settings_dialog._reset()
            self.assertEqual(changed, [True])                       # made again in Windows' language
            win.settings_dialog.close()


if __name__ == "__main__":
    unittest.main()
