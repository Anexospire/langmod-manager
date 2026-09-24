"""The looks: colour tokens, the scene behind the window, and the style sheet.

Standard front is the plain look, amber on grey, with a dark and a light face.
The others each bring a scene, drawn behind the whole window by
:mod:`.scenes`, and panels that let a little of it through.

Colours are ``#RRGGBB`` or ``#AARRGGBB``. QColor reads both; the style sheet
gets them as ``rgba()``, which is the form it understands. Keys that start
with ``_`` are not colours but what a scene needs (Your picture's file).
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QGuiApplication, QIcon, QPalette
from PySide6.QtWidgets import QApplication

from .. import i18n
from ..core.shortcuts import RESOURCES
from ..i18n import N_


def a(hex_colour: str, alpha: float) -> str:
    """``#RRGGBB`` at an opacity, as ``#AARRGGBB``."""
    return f"#{round(alpha * 255):02x}{hex_colour.lstrip('#')}"


def css(colour: str) -> str:
    c = QColor(colour)
    return f"rgba({c.red()}, {c.green()}, {c.blue()}, {c.alpha()})"


def solid(colour: str) -> str:
    """The colour without its transparency, for HTML, which has no alpha."""
    return QColor(colour).name()


STANDARD_DARK = {
    "grad_top": "#15171b", "grad_bottom": "#15171b", "bg": "#15171b",
    "panel": "#1c1f25", "panel_solid": "#1c1f25", "topbar": "#1c1f25", "input": "#15171b",
    "raised": "#242830", "border": "#2f343d",
    "text": "#e6e8ec", "muted": "#8b919c", "faint": "#5c6370",
    "accent": "#e5a52a", "accent_text": "#1a1400", "accent_soft": "#3a3120",
    "ok": "#4cc38a", "warn": "#e5a52a", "err": "#e5484d", "sel": "#33507a",
    "ok_soft": "#173527", "err_soft": "#3d1d20",
}
STANDARD_LIGHT = {
    "grad_top": "#f3f4f6", "grad_bottom": "#f3f4f6", "bg": "#f3f4f6",
    "panel": "#ffffff", "panel_solid": "#ffffff", "topbar": "#ffffff", "input": "#f3f4f6",
    "raised": "#eceef2", "border": "#d8dbe2",
    "text": "#1d2025", "muted": "#646b78", "faint": "#9aa1ad",
    "accent": "#b8801a", "accent_text": "#ffffff", "accent_soft": "#f5e8cc",
    "ok": "#1f9d61", "warn": "#b8801a", "err": "#d1373c", "sel": "#cfe0f7",
    "ok_soft": "#dcf5e4", "err_soft": "#fbdfe0",
}
ASTRAL = {
    "grad_top": "#0d1636", "grad_bottom": "#03050c", "bg": "#070b18",
    "panel": a("#0d1430", 0.80), "panel_solid": "#0e1530", "topbar": a("#0a1027", 0.72),
    "input": a("#050814", 0.65),
    "raised": a("#1b2548", 0.85), "border": a("#40528c", 0.50),
    "text": "#e3e9fb", "muted": "#93a0c6", "faint": "#5f6b93",
    "accent": "#8fbaff", "accent_text": "#07102a", "accent_soft": a("#8fbaff", 0.17),
    "ok": "#62d6a8", "warn": "#f2c56b", "err": "#ff7088", "sel": a("#3b5fb0", 0.60),
    "ok_soft": a("#62d6a8", 0.14), "err_soft": a("#ff7088", 0.15),
    # the scene
    "nebula_a": "#6a4bc4", "nebula_b": "#2a6ad0", "star": "#eef3ff",
}
SAKURA = {
    "grad_top": "#ffd2e1", "grad_bottom": "#fff7fa", "bg": "#fff0f5",
    "panel": a("#ffffff", 0.80), "panel_solid": "#fffafc", "topbar": a("#fff8fb", 0.74),
    "input": a("#ffffff", 0.85),
    "raised": a("#ffe4ee", 0.92), "border": a("#e3a0ba", 0.55),
    "text": "#3b2430", "muted": "#8c6474", "faint": "#bf98a8",
    "accent": "#d94f86", "accent_text": "#ffffff", "accent_soft": a("#d94f86", 0.14),
    "ok": "#2f9d6a", "warn": "#c98424", "err": "#d23b55", "sel": a("#f3a2c2", 0.55),
    "ok_soft": a("#2f9d6a", 0.12), "err_soft": a("#d23b55", 0.12),
    "petal_a": "#f7a3c1", "petal_b": "#fbc4d6", "petal_c": "#f28bb1", "branch": "#6f3b50",
}
FROST = {
    "grad_top": "#c3d8ef", "grad_bottom": "#f2f8ff", "bg": "#e8f1fb",
    "panel": a("#ffffff", 0.80), "panel_solid": "#f8fbff", "topbar": a("#f7fbff", 0.74),
    "input": a("#ffffff", 0.85),
    "raised": a("#e2edf9", 0.92), "border": a("#8fb3da", 0.55),
    "text": "#1c2a3a", "muted": "#566c84", "faint": "#91a5ba",
    "accent": "#2f7fd1", "accent_text": "#ffffff", "accent_soft": a("#2f7fd1", 0.13),
    "ok": "#1f9d6f", "warn": "#b8801a", "err": "#cf3c4a", "sel": a("#97c0ea", 0.60),
    "ok_soft": a("#1f9d6f", 0.12), "err_soft": a("#cf3c4a", 0.12),
}
EMBER = {
    "grad_top": "#0e0807", "grad_bottom": "#2e130a", "bg": "#140c0a",
    "panel": a("#1c110f", 0.80), "panel_solid": "#1d1210", "topbar": a("#140c0a", 0.74),
    "input": a("#0c0706", 0.65),
    "raised": a("#2d1b15", 0.88), "border": a("#7a4330", 0.50),
    "text": "#f4e7e0", "muted": "#b7988a", "faint": "#7d6157",
    "accent": "#ff8a3d", "accent_text": "#1c0a02", "accent_soft": a("#ff8a3d", 0.17),
    "ok": "#6fd49a", "warn": "#ffc15e", "err": "#ff5f57", "sel": a("#a4482a", 0.60),
    "ok_soft": a("#6fd49a", 0.14), "err_soft": a("#ff5f57", 0.15),
    "spark_hot": "#ffe7a8", "spark_warm": "#ff9a45", "spark_cool": "#c2381c",
}
AURORA = {
    "grad_top": "#010409", "grad_bottom": "#061a22", "bg": "#030a10",
    # A little more see-through than the others: the lights are the point.
    "panel": a("#06121a", 0.70), "panel_solid": "#07131b", "topbar": a("#040c13", 0.62),
    "input": a("#02070c", 0.65),
    "raised": a("#0f2530", 0.86), "border": a("#2b6d70", 0.45),
    "text": "#e0f5f1", "muted": "#8db5b0", "faint": "#557b79",
    "accent": "#4df0b0", "accent_text": "#02201a", "accent_soft": a("#4df0b0", 0.15),
    "ok": "#7de39d", "warn": "#f2d06b", "err": "#ff6f96", "sel": a("#237a73", 0.55),
    "ok_soft": a("#7de39d", 0.13), "err_soft": a("#ff6f96", 0.15),
    # the scene
    "star": "#eaf6ff", "ridge": "#0a1b24", "land": "#020609", "water": "#03101a",
}
FACTORY = {
    "grad_top": "#1d242b", "grad_bottom": "#101317", "bg": "#15191e",
    "panel": a("#161b21", 0.82), "panel_solid": "#171c22", "topbar": a("#11151a", 0.76),
    "input": a("#0b0e11", 0.65),
    "raised": a("#242b33", 0.88), "border": a("#4c5763", 0.55),
    "text": "#e9ecef", "muted": "#9ba5af", "faint": "#67727d",
    "accent": "#f5c542", "accent_text": "#1a1400", "accent_soft": a("#f5c542", 0.15),
    "ok": "#5fd08a", "warn": "#ff9f43", "err": "#ff5d5d", "sel": a("#4f6477", 0.65),
    "ok_soft": a("#5fd08a", 0.13), "err_soft": a("#ff5d5d", 0.15),
    # the scene
    "steel_dark": "#262d35", "steel": "#46515c", "steel_light": "#7c8995",
    "brass_dark": "#4e3a1f", "brass": "#8c6a37", "brass_light": "#caa061",
    "hazard": "#f5c542", "rubber": "#121518",
}
SPACE = {
    "grad_top": "#0a0620", "grad_bottom": "#020108", "bg": "#06040f",
    # See-through enough for the ship to show behind the cards.
    "panel": a("#0b0820", 0.74), "panel_solid": "#0c0922", "topbar": a("#080616", 0.66),
    "input": a("#040310", 0.65),
    "raised": a("#18133a", 0.86), "border": a("#4b3f8c", 0.50),
    "text": "#ece8ff", "muted": "#a59dca", "faint": "#6b6390",
    "accent": "#b18cff", "accent_text": "#14072e", "accent_soft": a("#b18cff", 0.16),
    "ok": "#6fe0b0", "warn": "#ffc46b", "err": "#ff6f8e", "sel": a("#5a45b0", 0.60),
    "ok_soft": a("#6fe0b0", 0.13), "err_soft": a("#ff6f8e", 0.15),
    # the scene
    "nebula_a": "#7b3fe0", "nebula_b": "#d8457f", "nebula_c": "#2aa6c9", "star": "#f4f1ff",
    "hull_light": "#e3e8f2", "hull": "#98a2b8", "hull_dark": "#353b4f", "engine": "#6fe3ff",
    "rock": "#6e645c",
}
WARFARE = {
    "grad_top": "#262c35", "grad_bottom": "#5b6470", "bg": "#171b21",
    "panel": a("#151a20", 0.80), "panel_solid": "#171c22", "topbar": a("#12161b", 0.72),
    "input": a("#0c0f13", 0.65),
    "raised": a("#232a32", 0.88), "border": a("#5a6470", 0.50),
    "text": "#e9ecef", "muted": "#a7afb8", "faint": "#6d7681",
    "accent": "#a8c070", "accent_text": "#141a08", "accent_soft": a("#a8c070", 0.16),
    "ok": "#8fd07a", "warn": "#f0b050", "err": "#ff6a55", "sel": a("#5d6b3a", 0.62),
    "ok_soft": a("#8fd07a", 0.13), "err_soft": a("#ff6a55", 0.15),
    # the scene
    "sky_top": "#262c35", "sky_mid": "#4c5563", "sky_low": "#8a919a", "city": "#737b86", "fire": "#ff8a3c",
    "grass": "#55523a", "camo_green": "#4f5f37", "camo_brown": "#6a4829", "camo_black": "#24251f", "wheel": "#59623f",
}


PICTURE = {
    "grad_top": "#15181e", "grad_bottom": "#0a0b0e", "bg": "#0e1014",
    "panel": a("#101318", 0.76), "panel_solid": "#12151b", "topbar": a("#0c0e12", 0.70),
    "input": a("#07080b", 0.60),
    "raised": a("#262b36", 0.85), "border": a("#8a93a6", 0.28),
    "text": "#eceef2", "muted": "#a6adba", "faint": "#6e7584",
    "accent": "#7fb2ff", "accent_text": "#08101f", "accent_soft": a("#7fb2ff", 0.17),
    "ok": "#5fd39c", "warn": "#f0bf5a", "err": "#ff6f7d", "sel": a("#7fb2ff", 0.32),
    "ok_soft": a("#5fd39c", 0.14), "err_soft": a("#ff6f7d", 0.15),
}
DEFAULT_ACCENT = PICTURE["accent"]


def readable_accent(colour: str) -> str:
    """A colour taken to accent the dark panels: light enough to read, vivid enough to see."""
    c = QColor(colour)
    if not c.isValid():
        return DEFAULT_ACCENT
    h, s, l, _a = c.getHslF()
    if s < 0.08:                                   # grey: stays grey, only lighter
        return QColor.fromHslF(max(h, 0.0), s, 0.74).name()
    return QColor.fromHslF(max(h, 0.0), min(0.9, max(0.5, s)), min(0.78, max(0.64, l))).name()


def picture_tokens(accent: str = "") -> dict:
    """Your picture's colours, with an accent to go with the picture."""
    t = dict(PICTURE)
    c = QColor(readable_accent(accent) if accent else DEFAULT_ACCENT)
    light = (0.2126 * c.redF() + 0.7152 * c.greenF() + 0.0722 * c.blueF()) > 0.45
    t["accent"] = c.name()
    t["accent_text"] = "#0a0c10" if light else "#ffffff"
    t["accent_soft"] = a(c.name(), 0.17)
    t["sel"] = a(c.name(), 0.32)
    return t


@dataclass(frozen=True)
class Theme:
    key: str
    name: str
    blurb: str
    scene: str                 # none, stars, petals, snow, embers, aurora, factory, space, warfare, picture
    tokens: dict
    dark: bool


THEMES: dict[str, Theme] = {
    "standard": Theme("standard", N_("Standard front"), N_("The plain look: amber on grey, dark or light."),
                      "none", STANDARD_DARK, True),
    "astral": Theme("astral", N_("Astral"), N_("Blue-black night with twinkling stars and the odd shooting star."),
                    "stars", ASTRAL, True),
    "sakura": Theme("sakura", N_("Sakura"), N_("Pink fading to white, with cherry petals drifting down."),
                    "petals", SAKURA, False),
    "frost": Theme("frost", N_("Frost"), N_("Pale winter blue, with snow falling over far hills."),
                   "snow", FROST, False),
    "ember": Theme("ember", N_("Ember"), N_("Charcoal and red, with sparks rising from a fire below."),
                   "embers", EMBER, True),
    "aurora": Theme("aurora", N_("Aurora"), N_("Northern lights shimmering over a still lake at night."),
                    "aurora", AURORA, True),
    "factory": Theme("factory", N_("Factory"), N_("Steel and hazard yellow, with turning gears and busy belts."),
                     "factory", FACTORY, True),
    "space": Theme("space", N_("Space travel"), N_("A starship cruising through drifting debris, past stars and galaxies."),
                   "space", SPACE, True),
    "warfare": Theme("warfare", N_("Warfare"), N_("A city at dusk under a pall of smoke, and a tank on the hill before it."),
                     "warfare", WARFARE, True),
    "picture": Theme("picture", N_("Your picture"), N_("Your own picture, dimmed and slowly drifting, in its colours."),
                     "picture", PICTURE, True),
}
ORDER = tuple(THEMES)


def system_dark() -> bool:
    try:
        return QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
    except AttributeError:
        return False


def resolve(theme_key: str, standard_mode: str = "system", picture: dict | None = None) -> tuple[Theme, dict]:
    """The theme and its colours. ``LANGMOD_THEME`` overrides, for screenshots. ``picture`` is Your
    picture's choices: ``path``, ``accent`` ("" to take it from the picture) and ``dim``."""
    forced = os.environ.get("LANGMOD_THEME", "").lower()
    if forced in ("dark", "light"):
        theme_key, standard_mode = "standard", forced
    elif forced in THEMES:
        theme_key = forced
    theme = THEMES.get(theme_key, THEMES["standard"])
    if theme.key == "standard":
        dark = standard_mode == "dark" or (standard_mode == "system" and system_dark())
        return Theme(theme.key, theme.name, theme.blurb, theme.scene,
                     STANDARD_DARK if dark else STANDARD_LIGHT, dark), (STANDARD_DARK if dark else STANDARD_LIGHT)
    if theme.key == "picture":
        from .picture import accent_from
        chosen = picture or {}
        path = chosen.get("path") or ""
        t = picture_tokens(chosen.get("accent") or (accent_from(path) if path else ""))
        t["_picture"] = path
        t["_dim"] = chosen.get("dim") or "medium"
        return theme, t
    return theme, theme.tokens


_current: dict = dict(STANDARD_DARK)
_current_theme: Theme = THEMES["standard"]


def tokens() -> dict:
    return _current


def current_theme() -> Theme:
    return _current_theme


def icon_for(theme_key: str, play: bool = False) -> QIcon:
    name = f"icon-{theme_key}{'-play' if play else ''}.ico"
    path = RESOURCES / name
    if not path.is_file():
        path = RESOURCES / "icon-standard.ico"
    return QIcon(str(path))


_QSS = """
* {{ font-family: {font}; font-size: 13px; }}
QMainWindow, QDialog {{ background: {bg}; color: {text}; }}
QWidget {{ color: {text}; }}
QWidget#root {{ background: transparent; }}
QToolTip {{ background: {panel_solid}; color: {text}; border: 1px solid {border}; padding: 4px 6px; }}

QWidget#topbar {{ background: {topbar}; border-bottom: 1px solid {border}; }}
QWidget#card, QFrame#card {{ background: {panel}; border: 1px solid {border}; border-radius: 10px; }}
QWidget#panelHeader {{ background: transparent; border: none; border-bottom: 1px solid {border}; }}
QWidget#transparent {{ background: transparent; }}
QFrame#banner {{ background: {accent_soft}; border: 1px solid {glow_edge}; border-radius: 8px; }}
QFrame#tourCard {{ background: {panel_solid}; border: 1px solid {glow_edge}; border-radius: 12px; }}
QLabel#tourTitle {{ font-size: 17px; font-weight: 600; }}
QLabel#tourBody {{ color: {text}; font-size: 13px; }}
QFrame#banner[tone="err"] {{ background: {err_soft}; border-color: {err}; }}

QLabel {{ background: transparent; }}
QLabel#appTitle {{ font-size: 15px; font-weight: 600; }}
QLabel#bigTitle {{ font-size: 20px; font-weight: 600; }}
QLabel#panelTitle {{ font-size: 14px; font-weight: 600; }}
QLabel#topMeta {{ color: {muted}; font-size: 12px; }}
QLabel#sectionTitle {{ color: {muted}; font-size: 11px; font-weight: 600; letter-spacing: 1px; }}
QLabel#muted {{ color: {muted}; }}
QLabel#faint {{ color: {faint}; font-size: 12px; }}
QLabel#hint {{ color: {muted}; font-size: 13px; }}
QLabel#chip {{ background: {accent_soft}; color: {accent}; border-radius: 9px; padding: 2px 9px; font-size: 11px; font-weight: 600; }}
QLabel#chip[tone="ok"] {{ background: {ok_soft}; color: {ok}; }}
QLabel#chip[tone="warn"] {{ background: {accent_soft}; color: {warn}; }}
QLabel#chip[tone="err"] {{ background: {err_soft}; color: {err}; }}
QLabel#chip[tone="neutral"] {{ background: {raised}; color: {muted}; }}

QPushButton {{ background: {raised}; border: 1px solid {border}; border-radius: 6px; padding: 5px 12px; color: {text}; }}
QPushButton:hover {{ background: {glow}; border-color: {glow_edge}; }}
QPushButton:pressed {{ background: {border}; }}
QPushButton:disabled {{ color: {faint}; background: transparent; border-color: {border}; }}
QPushButton#primary {{ background: {accent}; color: {accent_text}; border: none; font-weight: 600; padding: 6px 16px; }}
QPushButton#primary:hover {{ background: {accent}; }}
QPushButton#primary:disabled {{ background: {accent_soft}; color: {muted}; }}
QPushButton#flat {{ background: transparent; border: none; padding: 4px; border-radius: 6px; }}
QPushButton#flat:hover {{ background: {glow}; }}
QPushButton#link {{ background: transparent; border: none; color: {muted}; padding: 4px 2px; text-align: left; }}
QPushButton#link:hover {{ color: {accent}; }}
QPushButton::menu-indicator {{ image: none; width: 0; }}

QLineEdit, QComboBox {{ background: {input}; border: 1px solid {border}; border-radius: 6px; padding: 5px 8px; selection-background-color: {sel}; }}
QLineEdit:hover, QComboBox:hover {{ border-color: {glow_edge}; }}
QLineEdit:focus {{ border-color: {accent}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background: {panel_solid}; border: 1px solid {border}; selection-background-color: {sel}; }}

QListWidget, QTableWidget, QTextBrowser, QPlainTextEdit, QScrollArea {{
    background: transparent; border: none; outline: none;
    selection-background-color: {sel}; selection-color: {text}; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QListWidget::item {{ border-radius: 6px; margin: 1px 0; }}
QListWidget::item:hover:!selected {{ background: {glow_soft}; }}
QListWidget::item:selected {{ background: {sel}; }}
QListWidget#sidebar {{ background: transparent; font-size: 13px; }}
QListWidget#sidebar::item {{ padding: 7px 10px; }}
QTableWidget {{ gridline-color: {border}; }}
QTableWidget::item {{ padding: 2px 6px; }}
QTableWidget::item:selected {{ background: {sel}; color: {text}; }}
QHeaderView::section {{ background: transparent; color: {muted}; border: none; border-bottom: 1px solid {border};
    padding: 5px 8px; font-size: 11px; font-weight: 600; }}
QPlainTextEdit#mono {{ font-family: "Cascadia Mono", Consolas, monospace; font-size: 12px; padding: 6px; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {border}; border-radius: 5px; min-height: 24px; }}
QScrollBar::handle:vertical:hover {{ background: {glow_edge}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: {border}; border-radius: 5px; min-width: 24px; }}
QScrollBar::handle:horizontal:hover {{ background: {glow_edge}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QSplitter::handle {{ background: transparent; }}
QStatusBar {{ background: {topbar}; border-top: 1px solid {border}; color: {muted}; }}
QStatusBar::item {{ border: none; }}
QStatusBar QLabel {{ color: {muted}; padding: 0 8px; }}

QSlider {{ background: transparent; min-height: 22px; }}
QSlider::groove:horizontal {{ height: 4px; background: {border}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {accent}; border-radius: 2px; }}
QSlider::add-page:horizontal {{ background: {border}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {accent}; border: 3px solid {panel_solid}; width: 12px; height: 12px;
    margin: -7px 0; border-radius: 9px; }}
QSlider::handle:horizontal:hover {{ border-color: {glow_edge}; }}
QSlider::handle:horizontal:disabled {{ background: {faint}; }}

QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator, QListWidget::indicator {{ width: 16px; height: 16px; border: 1px solid {border}; border-radius: 4px; background: {input}; }}
QCheckBox::indicator:hover, QListWidget::indicator:hover {{ border-color: {glow_edge}; }}
QCheckBox::indicator:checked, QListWidget::indicator:checked {{ background: {accent}; border-color: {accent};
    image: url({check}); }}

QMenu {{ background: {panel_solid}; border: 1px solid {border}; padding: 4px; }}
QMenu::item {{ padding: 6px 22px 6px 12px; border-radius: 4px; }}
QMenu::item:selected {{ background: {sel}; color: {text}; }}
QMenu::separator {{ height: 1px; background: {border}; margin: 4px 6px; }}
QMenu::indicator {{ width: 14px; height: 14px; }}
QMessageBox {{ background: {panel_solid}; }}
"""


def stylesheet(t: dict) -> str:
    values = {k: css(v) for k, v in t.items() if not k.startswith("_")}
    values["glow"] = css(a(solid(t["accent"]), 0.16))
    values["glow_soft"] = css(a(solid(t["accent"]), 0.08))
    values["glow_edge"] = css(a(solid(t["accent"]), 0.55))
    values["check"] = _check_image(solid(t["accent_text"]))
    # Chinese reads best in Windows' own Chinese interface font; Segoe UI has no Chinese in it.
    values["font"] = ('"Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", sans-serif' if i18n.language() == "zh"
                      else '"Segoe UI", "Inter", system-ui, sans-serif')
    return _QSS.format(**values)


def _check_image(colour: str) -> str:
    """A tick for ticked boxes, drawn once per colour into the temp folder."""
    import tempfile
    from pathlib import Path
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QPainter, QPen, QPixmap
    path = Path(tempfile.gettempdir()) / f"langmod-check-{colour.lstrip('#')}.png"
    if not path.is_file():
        pm = QPixmap(32, 32)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor(colour), 4.2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawPolyline([QPointF(8, 16.5), QPointF(13.5, 22), QPointF(24, 10)])
        p.end()
        pm.save(str(path))
    return str(path).replace("\\", "/")


def apply_look(app: QApplication, theme_key: str, standard_mode: str = "system",
               picture: dict | None = None) -> tuple[Theme, dict]:
    """Put a theme on the whole application: palette, style sheet, window icon."""
    global _current, _current_theme
    theme, t = resolve(theme_key, standard_mode, picture)
    _current, _current_theme = dict(t), theme
    app.setStyle("Fusion")
    pal = QPalette()
    for role, key in ((QPalette.Window, "bg"), (QPalette.Base, "panel_solid"), (QPalette.AlternateBase, "raised"),
                      (QPalette.Text, "text"), (QPalette.WindowText, "text"), (QPalette.ButtonText, "text"),
                      (QPalette.Button, "panel_solid"), (QPalette.Highlight, "sel"),
                      (QPalette.HighlightedText, "text"), (QPalette.PlaceholderText, "faint"),
                      (QPalette.ToolTipBase, "panel_solid"), (QPalette.ToolTipText, "text"),
                      (QPalette.Link, "accent")):
        colour = QColor(t[key])
        if key in ("sel",):
            colour.setAlpha(255)
        pal.setColor(role, colour)
    app.setPalette(pal)
    app.setStyleSheet(stylesheet(t))
    app.setWindowIcon(icon_for(theme.key))
    return theme, t
