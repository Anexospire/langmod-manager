"""What Windows says about how the player wants things to look, and Qt's own words in their language."""
from __future__ import annotations

import os
import sys
from pathlib import Path

_translator = None
QT_CATALOGS = {"de": "qtbase_de", "fr": "qtbase_fr", "pl": "qtbase_pl", "ru": "qtbase_ru", "zh": "qtbase_zh_CN"}

SPI_GETCLIENTAREAANIMATION = 0x1042


def animations_on() -> bool:
    """Windows' Animation effects (Settings, Accessibility, Visual effects). ``LANGMOD_ANIMATIONS=0`` or
    ``1`` overrides; on other systems, and if Windows cannot be asked, yes."""
    forced = os.environ.get("LANGMOD_ANIMATIONS")
    if forced in ("0", "1"):
        return forced == "1"
    if sys.platform != "win32":
        return True
    try:
        import ctypes
        from ctypes import wintypes
        value = wintypes.BOOL(1)
        if ctypes.windll.user32.SystemParametersInfoW(SPI_GETCLIENTAREAANIMATION, 0, ctypes.byref(value), 0):
            return bool(value.value)
    except Exception:
        pass
    return True


def qt_translations(code: str) -> bool:
    """Qt's own words - Yes, No, Cancel, the file and colour dialogs, a text box's menu - in the manager's
    language. Qt ships them; the standalone build keeps these five."""
    global _translator
    from PySide6.QtCore import QCoreApplication, QLibraryInfo, QTranslator
    app = QCoreApplication.instance()
    if app is None:
        return False
    if _translator is not None:
        app.removeTranslator(_translator)
        _translator = None
    name = QT_CATALOGS.get(code)
    if not name:
        return False
    import PySide6
    translator = QTranslator(app)
    for folder in (QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath),
                   str(Path(PySide6.__file__).resolve().parent / "translations")):
        if translator.load(name, folder):
            app.installTranslator(translator)
            _translator = translator
            return True
    return False


def translated() -> bool:
    """Whether Qt's own words are in a language other than English now."""
    return _translator is not None
