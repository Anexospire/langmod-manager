"""Start the window."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from .. import __version__, i18n
from ..core import selfupdate
from ..core.library import data_dir
from ..core.report import open_log
from . import system


class _ErrorLog:
    """Where errors go when there is no console (the standalone build, ``pythonw``): a file a
    player can send along with a report. Opened at the first error, so a run without one
    leaves nothing behind."""

    def __init__(self) -> None:
        self.file = None

    def write(self, text: str) -> int:
        try:
            if self.file is None:
                self.file = open_log(data_dir())
            self.file.write(text)
            self.file.flush()
        except OSError:
            pass                    # nowhere to write it: the error is lost, as it would have been
        return len(text)

    def flush(self) -> None:
        pass


def run() -> int:
    if sys.stderr is None:
        sys.stderr = _ErrorLog()
    if sys.platform == "win32":
        try:
            import ctypes
            # Its own taskbar button and icon, not Python's.
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("LangmodManager")
        except Exception:
            pass
    if selfupdate.standalone():
        selfupdate.tidy(Path(sys.executable).parent, data_dir())     # what an update left behind
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("Langmod Manager")
    app.setApplicationVersion(__version__)
    from .main_window import MainWindow
    win = MainWindow()
    win.show()
    forced = os.environ.get("LANGMOD_LANGUAGE", "")
    if forced not in ("", "en", i18n.PSEUDO) and (i18n.language() != forced or not system.translated()):
        # Only when a language is forced, as tools/release.py does to check a build has them all.
        print(f"the {forced} catalogs could not be loaded", file=sys.stderr)
    if os.environ.get("LANGMOD_QUIT_AFTER"):
        # tools/release.py's check of a build: open, run for a moment, close.
        QTimer.singleShot(int(os.environ["LANGMOD_QUIT_AFTER"]), lambda: app.exit(0))
    return app.exec()
