"""Settings: every choice, grouped, each with a line saying what it does.

Pages down the side, cards of rows, a name on the left and its control on the right, and a plain line
under it. Every change applies at once; there is nothing to confirm.
"""
from __future__ import annotations

import math
from pathlib import Path

from PySide6.QtCore import QEasingCurve, QEvent, QRectF, QSize, Qt, QTimer, QUrl, QVariantAnimation, Signal
from PySide6.QtGui import QDesktopServices, QIcon, QPainter, QPainterPath, QPen, QPixmap, QColor
from PySide6.QtWidgets import (QApplication, QBoxLayout, QColorDialog, QComboBox, QDialog, QFileDialog, QFrame,
                               QGraphicsOpacityEffect, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QSlider,
                               QListWidget, QMessageBox, QScrollArea, QStackedWidget, QVBoxLayout, QWidget)

from .. import __version__, i18n
from ..core import selfupdate, shortcuts, sources, steam
from ..i18n import tr
from ..core.game import find_installs, install_at
from ..core.library import Library
from ..core.prefs import Prefs
from . import scenes, system
from .icons import icon
from .picture import accent_from
from .themes import DEFAULT_ACCENT, ORDER, readable_accent, resolve, tokens
from .widgets import GlowButton, SegmentSwitch, alpha, card, chip

NAME_ROOM = 150
CARD_MARGIN = 16


def _card(title: str) -> tuple[QFrame, QVBoxLayout]:
    c = card()
    box = QVBoxLayout(c)
    box.setContentsMargins(CARD_MARGIN, 12, CARD_MARGIN, 14)
    box.setSpacing(10)
    box.addWidget(QLabel(title.upper(), objectName="sectionTitle"))
    return c, box


class _Row(QWidget):
    """A setting's name with its control beside it, or under it when there is no room."""

    def __init__(self, title: str, control: QWidget):
        super().__init__()
        self.name = QLabel(title)
        self.name.setWordWrap(True)
        self.control = control
        self.line = QBoxLayout(QBoxLayout.Direction.LeftToRight, self)
        self.line.setContentsMargins(0, 0, 0, 0)
        self.line.setSpacing(12)
        self.line.addWidget(self.name, 1)
        self.line.addWidget(control, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

    def minimumSizeHint(self) -> QSize:
        return QSize(self.control.sizeHint().width(), super().minimumSizeHint().height())

    def resizeEvent(self, e) -> None:
        stack = self.width() < self.control.sizeHint().width() + 12 + NAME_ROOM
        if stack != (self.line.direction() == QBoxLayout.Direction.TopToBottom):
            self.line.setDirection(QBoxLayout.Direction.TopToBottom if stack else QBoxLayout.Direction.LeftToRight)
            self.line.setAlignment(self.control, Qt.AlignmentFlag.AlignLeft if stack
                                   else Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.updateGeometry()
        super().resizeEvent(e)


def _row(box: QVBoxLayout, title: str, control: QWidget, hint: str = "") -> QWidget:
    holder = QWidget()
    stack = QVBoxLayout(holder)
    stack.setContentsMargins(0, 0, 0, 0)
    stack.setSpacing(3)
    stack.addWidget(_Row(title, control))
    if hint:
        note = QLabel(hint, objectName="faint")
        note.setWordWrap(True)
        stack.addWidget(note)
    box.addWidget(holder)
    return holder


def _switch(choices, current, on_change) -> SegmentSwitch:
    sw = SegmentSwitch([(str(v), label) for v, label in choices])
    kind = type(choices[0][0])
    sw.set_current(str(current), animate=False)
    sw.selected.connect(lambda key: on_change(kind(key)))
    return sw


def _toggle(on: bool, on_change) -> SegmentSwitch:
    sw = SegmentSwitch([("1", tr("On")), ("0", tr("Off"))])
    sw.set_current("1" if on else "0", animate=False)
    sw.selected.connect(lambda key: on_change(key == "1"))
    return sw


def _swatch(colour: str) -> QIcon:
    pm = QPixmap(36, 36)
    pm.setDevicePixelRatio(2.0)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(QPen(QColor(255, 255, 255, 90), 1))
    p.setBrush(QColor(colour))
    p.drawRoundedRect(QRectF(1, 1, 16, 16), 4, 4)
    p.end()
    return QIcon(pm)


def version_note() -> str:
    """The footnote under the pages: which Langmod Manager this is, and whether it is an early-access build."""
    return "\n".join([tr("Langmod Manager {version}", version=__version__)]
                     + ([tr("Early access")] if __version__.endswith("ea") else []))


def picture_config(prefs: Prefs) -> dict:
    return {"path": prefs.get("picture"), "accent": prefs.get("picture_accent"), "dim": prefs.get("picture_dim")}


def _elided(text: str, width: int = 300) -> QLabel:
    lab = QLabel(objectName="faint")
    lab.setToolTip(text)
    lab.setMaximumWidth(width)
    lab.setText(lab.fontMetrics().elidedText(text, Qt.TextElideMode.ElideMiddle, width))
    return lab


class ThemeCard(QWidget):
    """One theme to pick: a still of it, its name and a line about it."""

    picked = Signal(str)
    PREVIEW = QSize(196, 112)

    def __init__(self, key: str, standard_mode: str, picture: dict | None = None):
        super().__init__()
        self.key = key
        self.selected = False
        self.hover = False
        self.mode = standard_mode
        self.chosen = picture
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setFixedSize(self.PREVIEW.width() + 16, self.PREVIEW.height() + 64)
        self.set_mode(standard_mode)

    def set_mode(self, standard_mode: str) -> None:
        self.mode = standard_mode
        theme, t = resolve(self.key, standard_mode, self.chosen)
        self.theme, self.t = theme, t
        self.picture = scenes.preview(theme, t, self.PREVIEW)
        self.update()

    def set_picture(self, picture: dict) -> None:
        """Your picture's choices changed: draw its still anew."""
        self.chosen = picture
        self.set_mode(self.mode)

    def enterEvent(self, e) -> None:
        self.hover = True
        self.update()

    def leaveEvent(self, e) -> None:
        self.hover = False
        self.update()

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self.picked.emit(self.key)

    def paintEvent(self, e) -> None:
        t = tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        outer = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        if self.selected or self.hover:
            p.setPen(QPen(QColor(t["accent"]) if self.selected else alpha(t["accent"], 0.45),
                          2.0 if self.selected else 1.2))
            p.setBrush(alpha(t["accent"], 0.10 if self.selected else 0.05))
            p.drawRoundedRect(outer, 10, 10)
        p.drawPixmap(8, 8, self.picture)
        p.setPen(QPen(QColor(t["border"]), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        path = QPainterPath()
        path.addRoundedRect(QRectF(8, 8, self.PREVIEW.width(), self.PREVIEW.height()), 8, 8)
        p.drawPath(path)
        f = p.font()
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor(t["accent"] if self.selected else t["text"]))
        y = self.PREVIEW.height() + 14
        p.drawText(QRectF(10, y, self.width() - 20, 18), Qt.AlignmentFlag.AlignLeft,
                   p.fontMetrics().elidedText(tr(self.theme.name), Qt.TextElideMode.ElideRight, self.width() - 20))
        f.setBold(False)
        f.setPixelSize(11)
        p.setFont(f)
        p.setPen(QColor(t["muted"]))
        p.drawText(QRectF(10, y + 19, self.width() - 20, 30),
                   Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextWordWrap, tr(self.theme.blurb))
        p.end()


class PageDots(QWidget):
    """A dot for each page, the one showing drawn long. It follows the slide as it happens,
    so the long dot glides from one page to the next with the cards."""

    clicked = Signal(int)
    GAP = 18

    def __init__(self, count: int):
        super().__init__()
        self.count = count
        self.position = 0.0
        self.setFixedSize(max(1, count - 1) * self.GAP + 40, 20)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def _first(self) -> float:
        return (self.width() - (self.count - 1) * self.GAP) / 2

    def set_position(self, position: float) -> None:
        self.position = position
        self.update()

    def mousePressEvent(self, e) -> None:
        i = round((e.position().x() - self._first()) / self.GAP)
        self.clicked.emit(max(0, min(self.count - 1, i)))

    def paintEvent(self, e) -> None:
        t = tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        y, x0 = self.height() / 2, self._first()
        for i in range(self.count):
            near = max(0.0, 1.0 - abs(i - self.position))
            p.setBrush(alpha(t["muted"], 0.35 + 0.25 * near))
            p.drawEllipse(QRectF(x0 + i * self.GAP - 3, y - 3, 6, 6))
        # The long dot stretches as it travels between two pages, and settles back at a page.
        between = abs(self.position - round(self.position))
        wide = 18 + 10 * math.sin(math.pi * min(1.0, between * 2) / 2)
        cx = x0 + self.position * self.GAP
        p.setBrush(QColor(t["accent"]))
        p.drawRoundedRect(QRectF(cx - wide / 2, y - 3.5, wide, 7), 3.5, 3.5)
        p.end()


class ThemePager(QWidget):
    """The themes six to a page, three across and two down, sliding from page to page.

    The cards glide sideways with an easing that starts quick and settles softly, the
    page leaving fades as the one arriving comes up, and the page dots follow along.
    The arrows, the dots, a sideways scroll (a touchpad swipe) and the arrow keys all
    turn the page.
    """

    PER_PAGE = 6
    COLUMNS = 3
    GAP = 8
    SLIDE_MS = 460

    page_changed = Signal(int)

    def __init__(self, cards: list[ThemeCard], start: int = 0):
        super().__init__()
        self.cards = cards
        self.count = max(1, math.ceil(len(cards) / self.PER_PAGE))
        self.page = max(0, min(start, self.count - 1))
        self.position = float(self.page)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        cw, ch = cards[0].width(), cards[0].height()
        rows = math.ceil(self.PER_PAGE / self.COLUMNS)
        self.page_w = self.COLUMNS * cw + (self.COLUMNS - 1) * self.GAP
        self.page_h = rows * ch + (rows - 1) * self.GAP
        self._wheel = 0

        self.viewport = QWidget()
        self.viewport.setFixedHeight(self.page_h)
        self.viewport.setMinimumWidth(self.page_w)
        self.strip = QWidget(self.viewport)
        self.page_widgets: list[QWidget] = []
        self.fades: list[QGraphicsOpacityEffect] = []
        for n in range(self.count):
            page = QWidget(self.strip)
            grid = QGridLayout(page)
            grid.setContentsMargins(0, 0, 0, 0)
            grid.setSpacing(self.GAP)
            for c in range(self.COLUMNS):
                grid.setColumnMinimumWidth(c, cw)
            for r in range(rows):
                grid.setRowMinimumHeight(r, ch)
            for i, card in enumerate(cards[n * self.PER_PAGE:(n + 1) * self.PER_PAGE]):
                grid.addWidget(card, i // self.COLUMNS, i % self.COLUMNS,
                               Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            fade = QGraphicsOpacityEffect(page)
            page.setGraphicsEffect(fade)
            self.page_widgets.append(page)
            self.fades.append(fade)

        self.prev = GlowButton(radius=8, objectName="flat")
        self.next = GlowButton(radius=8, objectName="flat")
        for b, tip in ((self.prev, tr("The page before")), (self.next, tr("The next page"))):
            b.setIconSize(QSize(18, 18))
            b.setFixedSize(34, 30)
            b.setToolTip(tip)
            # Never focused themselves: an arrow switched off at the last page while it had the focus
            # would hand it on down the page, and the scroll area would scroll all the way down after it.
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.prev.clicked.connect(lambda: self._turn(self.page - 1))
        self.next.clicked.connect(lambda: self._turn(self.page + 1))
        self.dots = PageDots(self.count)
        self.dots.clicked.connect(self._turn)
        self.label = QLabel("", objectName="faint")

        nav = QHBoxLayout()
        nav.setContentsMargins(0, 0, 0, 0)
        nav.addStretch(1)
        nav.addWidget(self.prev)
        nav.addWidget(self.dots)
        nav.addWidget(self.next)
        nav.addSpacing(6)
        nav.addWidget(self.label)
        nav.addStretch(1)
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(6)
        box.addWidget(self.viewport)
        box.addLayout(nav)
        self.prev.setVisible(self.count > 1)
        self.next.setVisible(self.count > 1)
        self.dots.setVisible(self.count > 1)

        self.slide = QVariantAnimation(self)
        self.slide.setDuration(self.SLIDE_MS)
        self.slide.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.slide.valueChanged.connect(lambda v: self._place(float(v)))
        self._tint()
        self._sync_controls()
        self._place(self.position)

    def _tint(self) -> None:
        self.prev.setIcon(icon("left", tokens()["text"], 18))
        self.next.setIcon(icon("right", tokens()["text"], 18))

    def changeEvent(self, e) -> None:
        super().changeEvent(e)
        if e.type() == QEvent.Type.StyleChange:          # the theme changed under it
            self._tint()

    def page_of(self, key: str) -> int:
        keys = [c.key for c in self.cards]
        return keys.index(key) // self.PER_PAGE if key in keys else 0

    def _turn(self, page: int) -> None:
        """An arrow or a dot was clicked: the pager takes the focus, so the arrow keys go on from there."""
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        self.go(page)

    def go(self, page: int, animate: bool = True) -> None:
        page = max(0, min(self.count - 1, page))
        if page == self.page and self.slide.state() != QVariantAnimation.State.Running:
            return
        self.page = page
        self._sync_controls()
        self.slide.stop()
        if animate and self.isVisible() and system.animations_on():
            self.slide.setStartValue(self.position)
            self.slide.setEndValue(float(page))
            self.slide.start()
        else:
            self._place(float(page))
        self.page_changed.emit(page)

    def _sync_controls(self) -> None:
        self.prev.setEnabled(self.page > 0)
        self.next.setEnabled(self.page < self.count - 1)
        first = self.page * self.PER_PAGE
        shown = self.cards[first:first + self.PER_PAGE]
        self.label.setText(tr("{page} of {pages}", page=self.page + 1, pages=self.count) if self.count > 1 else "")
        self.label.setToolTip(i18n.join([tr(c.theme.name) for c in shown]))

    def _place(self, position: float) -> None:
        self.position = position
        width = max(1, self.viewport.width())
        self.strip.setGeometry(round(-position * width), 0, width * self.count, self.page_h)
        for n, (page, fade) in enumerate(zip(self.page_widgets, self.fades)):
            page.setGeometry(n * width + (width - self.page_w) // 2, 0, self.page_w, self.page_h)
            away = min(1.0, abs(n - position))
            fade.setOpacity(1.0 - 0.75 * away)
            page.setVisible(away < 1.0)              # a page wholly out of view costs nothing
        self.dots.set_position(position)

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._place(self.position)

    def showEvent(self, e) -> None:
        super().showEvent(e)
        self._place(self.position)

    def wheelEvent(self, e) -> None:
        d = e.angleDelta()
        if abs(d.x()) <= abs(d.y()):
            e.ignore()                   # up and down still scroll the page of settings
            return
        self._wheel += d.x()
        if abs(self._wheel) >= 120:
            self.go(self.page + (1 if self._wheel < 0 else -1))
            self._wheel = 0
        e.accept()

    def keyPressEvent(self, e) -> None:
        if e.key() in (Qt.Key.Key_Left, Qt.Key.Key_PageUp):
            self.go(self.page - 1)
        elif e.key() in (Qt.Key.Key_Right, Qt.Key.Key_PageDown):
            self.go(self.page + 1)
        else:
            super().keyPressEvent(e)


class FpsSlider(QWidget):
    """Frames a second, 10 to 60 in steps of 5, with the number beside it.

    It says every value as it moves, so the background can follow straight away, and
    says it once more, as settled, when the handle is let go (or a moment after the
    keys or the wheel stop), which is when it is worth saving.
    """

    moved = Signal(int)
    settled = Signal(int)
    STEP = 5

    def __init__(self, value: int):
        super().__init__()
        low, high = scenes.FPS_RANGE
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(low, high)
        self.slider.setSingleStep(self.STEP)
        self.slider.setPageStep(self.STEP * 2)
        self.slider.setMinimumWidth(200)
        self.slider.setCursor(Qt.CursorShape.PointingHandCursor)
        self.label = QLabel("", objectName="muted")
        self.label.setMinimumWidth(52)
        self.label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        box = QHBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(10)
        box.addWidget(self.slider, 1)
        box.addWidget(self.label)
        self.quiet = QTimer(self)
        self.quiet.setSingleShot(True)
        self.quiet.setInterval(400)
        self.quiet.timeout.connect(lambda: self.settled.emit(self.value()))
        self.slider.setValue(self._snap(value))
        self._show(self.slider.value())
        self.slider.valueChanged.connect(self._changed)
        self.slider.sliderReleased.connect(lambda: self.settled.emit(self.value()))

    def _snap(self, v: int) -> int:
        low, high = scenes.FPS_RANGE
        return max(low, min(high, round(v / self.STEP) * self.STEP))

    def value(self) -> int:
        return self.slider.value()

    def set_value(self, v: int) -> None:
        self.slider.setValue(self._snap(v))

    def _show(self, v: int) -> None:
        self.label.setText(tr("{fps} fps", fps=v))

    def flush(self) -> None:
        """Save now what is waiting for the keys or the wheel to stop: Settings is closing, maybe
        with the app, and the pause would never end."""
        if self.quiet.isActive():
            self.quiet.stop()
            self.settled.emit(self.value())

    def hideEvent(self, e) -> None:
        self.flush()
        super().hideEvent(e)

    def _changed(self, v: int) -> None:
        snapped = self._snap(v)
        if snapped != v:
            self.slider.setValue(snapped)            # comes back here with the snapped value
            return
        self._show(v)
        self.moved.emit(v)
        if not self.slider.isSliderDown():
            self.quiet.start()                       # keys or the wheel: save once they stop


class ShortcutControl(QWidget):
    """Whether a shortcut is there, and the button that makes or removes it."""

    changed = Signal()

    def __init__(self, lib: Library, kind: str, where: str):
        super().__init__()
        self.lib, self.kind, self.where = lib, kind, where
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.state = QLabel(objectName="faint")
        self.button = GlowButton()
        self.button.setMinimumWidth(92)
        self.button.clicked.connect(self._toggle)
        row.addWidget(self.state)
        row.addWidget(self.button)
        self.refresh()

    def refresh(self) -> None:
        there = shortcuts.exists(self.lib, self.kind, self.where)
        self.state.setText(tr("there") if there else tr("not made"))
        self.button.setText(tr("Remove") if there else tr("Create"))

    def _toggle(self) -> None:
        try:
            if shortcuts.exists(self.lib, self.kind, self.where):
                shortcuts.drop(self.lib, self.kind, self.where)
            else:
                shortcuts.make(self.lib, self.kind, self.where)
        except shortcuts.ShortcutError as exc:
            QMessageBox.warning(self, tr("Shortcut"), str(exc))
        self.refresh()
        self.changed.emit()


class SettingsDialog(QDialog):
    """Everything there is to change, and what each thing does, said plainly."""

    look_changed = Signal()
    game_changed = Signal()
    fps_changed = Signal(int)
    language_changed = Signal()

    def __init__(self, lib: Library, parent=None, page: str = "look"):
        super().__init__(parent)
        self.lib = lib
        self.prefs = Prefs(lib)
        self.setWindowTitle(tr("Settings"))
        self.setMinimumSize(910, 600)          # room for a page of themes, three across
        self.resize(940, 700)
        self._build()
        keys = [key for key, _name, _build in self._pages()]
        self.sidebar.setCurrentRow(keys.index(page.lower()) if page.lower() in keys else 0)

    def current_page(self) -> str:
        return self._pages()[max(0, self.sidebar.currentRow())][0]

    def _pages(self) -> list:
        return [("look", tr("Look"), self._page_look), ("game", tr("Game"), self._page_game),
                ("applying", tr("Applying"), self._page_apply), ("updates", tr("Updates"), self._page_updates),
                ("shortcuts", tr("Shortcuts"), self._page_shortcuts), ("folders", tr("Folders"), self._page_folders)]

    # -- layout -----------------------------------------------------------------------
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 14)
        root.setSpacing(10)
        head = QHBoxLayout()
        head.addWidget(QLabel(tr("Settings"), objectName="appTitle"))
        head.addWidget(QLabel(tr("every change applies at once"), objectName="faint"))
        head.addStretch(1)
        root.addLayout(head)

        body = QHBoxLayout()
        body.setSpacing(14)
        self.pages = QStackedWidget()
        self.sidebar = QListWidget(objectName="sidebar")
        self.sidebar.setFixedWidth(150)
        self.sidebar.setFrameShape(QFrame.Shape.NoFrame)
        self.sidebar.currentRowChanged.connect(self.pages.setCurrentIndex)
        side = QVBoxLayout()
        side.setSpacing(6)
        side.addWidget(self.sidebar, 1)
        self.version_note = QLabel(version_note(), objectName="faint")
        self.version_note.setWordWrap(True)
        self.version_note.setFixedWidth(150)
        self.version_note.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        side.addWidget(self.version_note)
        body.addLayout(side)
        body.addWidget(self.pages, 1)
        root.addLayout(body, 1)
        for _key, name, build in self._pages():
            self.sidebar.addItem(name)
            self.pages.addWidget(self._scrolled(build()))

        buttons = QHBoxLayout()
        reset = GlowButton(tr("Put everything back"))
        reset.setToolTip(tr("Every setting on every page returns to how a fresh copy starts. Your mods stay."))
        reset.clicked.connect(self._reset)
        buttons.addWidget(reset)
        buttons.addStretch(1)
        close = GlowButton(tr("Close"), objectName="primary", light=True)
        close.clicked.connect(self.accept)
        buttons.addWidget(close)
        root.addLayout(buttons)

    @staticmethod
    def _scrolled(page: QWidget) -> QScrollArea:
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.Shape.NoFrame)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        area.setWidget(page)
        return area

    @staticmethod
    def _page() -> tuple[QWidget, QVBoxLayout]:
        page = QWidget(objectName="transparent")
        box = QVBoxLayout(page)
        box.setContentsMargins(0, 0, 10, 0)
        box.setSpacing(12)
        return page, box

    # -- pages ------------------------------------------------------------------------
    def _page_look(self) -> QWidget:
        page, box = self._page()
        c, rows = _card(tr("Language"))
        self.language_box = QComboBox()
        windows = i18n.LANGUAGES[i18n.system_language()]
        self.language_box.addItem(tr("Like Windows ({language})", language=windows), "system")
        for code, name in i18n.LANGUAGES.items():
            self.language_box.addItem(name, code)
        chosen = self.prefs.get("ui_language")
        self.language_box.setCurrentIndex(max(0, self.language_box.findData(chosen)))
        self.language_box.setMinimumWidth(220)
        self.language_box.activated.connect(lambda _i: self._set_language(self.language_box.currentData()))
        _row(rows, tr("Language of the manager"), self.language_box,
             tr("The window, its messages and the tour. The game's own language is set in the game. Mods "
                "and their text are not translated."))
        box.addWidget(c)

        c, rows = _card(tr("Theme"))
        self.theme_cards: dict[str, ThemeCard] = {}
        mode = self.prefs.get("standard_mode")
        for key in ORDER:
            tc = ThemeCard(key, mode, picture_config(self.prefs) if key == "picture" else None)
            tc.selected = key == self.prefs.get("theme")
            tc.picked.connect(self._pick_theme)
            self.theme_cards[key] = tc
        self.theme_pager = ThemePager(list(self.theme_cards.values()))
        self.theme_pager.go(self.theme_pager.page_of(self.prefs.get("theme")), animate=False)
        rows.addWidget(self.theme_pager)
        self.mode_switch = _switch((("system", tr("Like Windows")), ("dark", tr("Dark")), ("light", tr("Light"))),
                                   mode, self._set_mode)
        _row(rows, tr("Standard front colours"), self.mode_switch,
             tr("Standard front comes in dark and light. Like Windows turns it dark when Windows is."))
        box.addWidget(c)

        c, rows = _card(tr("Your picture"))
        self.picture_card = c
        ctrl = QWidget()
        line = QHBoxLayout(ctrl)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(8)
        self.picture_label = QLabel(objectName="faint")
        self.picture_label.setFixedHeight(40)
        choose = GlowButton(tr("Choose…"))
        choose.setIcon(icon("picture", tokens()["muted"], 15))
        choose.clicked.connect(self._choose_picture)
        line.addWidget(self.picture_label)
        line.addWidget(choose)
        _row(rows, tr("Picture"), ctrl, tr("A screenshot of your favourite tank, say. A copy is kept in the "
                                           "manager's folder, so the original can move or go."))
        ctrl = QWidget()
        line = QHBoxLayout(ctrl)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(8)
        self.accent_btn = GlowButton(tr("Choose…"))
        self.accent_btn.clicked.connect(self._choose_accent)
        self.accent_auto = GlowButton(tr("From the picture"))
        self.accent_auto.setToolTip(tr("Take the colour most of the picture's vivid parts share"))
        self.accent_auto.clicked.connect(lambda: self._set_picture("picture_accent", ""))
        line.addWidget(self.accent_btn)
        line.addWidget(self.accent_auto)
        _row(rows, tr("Accent colour"), ctrl, tr("The colour of buttons, ticks and highlights. From the picture "
                                                 "unless you choose one; it is made light enough to read either way."))
        self.dim_switch = _switch((("light", tr("A little")), ("medium", tr("Some")), ("strong", tr("A lot"))),
                                  self.prefs.get("picture_dim"), lambda v: self._set_picture("picture_dim", v))
        _row(rows, tr("Darken it"), self.dim_switch,
             tr("How far the picture is darkened, so the text over it reads well."))
        self._show_picture_rows()
        box.addWidget(c)

        c, rows = _card(tr("Background"))
        self.motion_switch = _toggle(self.prefs.get("motion"), lambda on: self._set("motion", on))
        _row(rows, tr("Animated background"), self.motion_switch,
             tr("The scene behind the window moves. Off keeps the picture, standing still. Standard front has none."))
        self.follow_windows = _toggle(self.prefs.get("motion_follows_windows"),
                                      lambda on: self._set("motion_follows_windows", on))
        _row(rows, tr("Still when Windows says so"), self.follow_windows,
             tr("Follows Windows' animation effects (Settings, Accessibility, Visual effects), which are on. Off "
                "here, the background moves whatever Windows says.") if system.animations_on() else
             tr("Follows Windows' animation effects (Settings, Accessibility, Visual effects), which are off, so "
                "the background stands still. Off here, it moves whatever Windows says."))
        self.amount_switch = _switch((("few", tr("A few")), ("some", tr("Some")), ("many", tr("Lots"))),
                                     self.prefs.get("amount"), lambda v: self._set("amount", v))
        _row(rows, tr("How much of it"), self.amount_switch,
             tr("How many things move in it: stars, petals, snow, sparks, dust, rocks, shells. Fewer is calmer, "
                "and lighter on an old PC."))
        self.fps_slider = FpsSlider(self.prefs.get("fps"))
        self.fps_slider.moved.connect(self.fps_changed.emit)
        self.fps_slider.settled.connect(lambda v: self.prefs.set("fps", v))
        _row(rows, tr("Frames a second"), self.fps_slider,
             tr("How smoothly it moves. 30 is smooth; 60 is smoother and takes about twice the work; fewer is "
                "lighter on an old PC. On a very big window, if drawing it gets too costly, it eases off below "
                "this by itself."))
        box.addWidget(c)
        box.addStretch(1)
        return page

    def _page_game(self) -> QWidget:
        page, box = self._page()
        c, rows = _card(tr("War Thunder"))
        found = {i.channel: i for i in reversed(find_installs())}
        chosen = self.prefs.get("folders")
        self.folder_labels: dict[str, QLabel] = {}
        for channel, label in (("live", tr("Live game")), ("dev", tr("Dev server"))):
            here = chosen.get(channel) or (str(found[channel].root) if channel in found else "")
            ctrl = QWidget()
            line = QHBoxLayout(ctrl)
            line.setContentsMargins(0, 0, 0, 0)
            line.setSpacing(8)
            path_label = _elided(here or tr("not found"), 260)
            self.folder_labels[channel] = path_label
            choose = GlowButton(tr("Choose…"))
            choose.clicked.connect(lambda _=False, ch=channel: self._choose_folder(ch))
            line.addWidget(path_label)
            line.addWidget(choose)
            if chosen.get(channel):
                auto = GlowButton(tr("Find by itself"))
                auto.setToolTip(tr("Forget the folder you chose and look in the usual places again"))
                auto.clicked.connect(lambda _=False, ch=channel: self._forget_folder(ch))
                line.addWidget(auto)
            _row(rows, label, ctrl)
        note = QLabel(tr("The manager looks where Steam and the Gaijin launcher put the game. If yours is "
                         "somewhere else, choose the folder that has config.blk and lang.vromfs.bin in it."),
                      objectName="faint")
        note.setWordWrap(True)
        rows.addWidget(note)
        box.addWidget(c)

        c, rows = _card(tr("Custom localization"))
        _row(rows, tr("Switch it on when applying"),
             _toggle(self.prefs.get("switch_on"), lambda on: self._set("switch_on", on)),
             tr("The game only reads the lang folder with this on. It is the same switch as “Custom "
                "localization” on the “Main” page of the game's own options, and the only line of config.blk "
                "the manager touches."))
        box.addWidget(c)
        box.addStretch(1)
        return page

    def _page_apply(self) -> QWidget:
        page, box = self._page()
        c, rows = _card(tr("Game updates"))
        _row(rows, tr("When War Thunder has updated"),
             _switch((("tell", tr("Tell me")), ("apply", tr("Apply by itself"))), self.prefs.get("on_update"),
                     lambda v: self._set("on_update", v)),
             tr("An update can add language files the mods do not know about. Applying again rebuilds the list "
                "from the new game, so new strings show instead of their IDs. By itself means as soon as the "
                "manager opens and sees a new version. Play and the War Thunder shortcut always do."))
        box.addWidget(c)

        c, rows = _card(tr("Files already in the lang folder"))
        _row(rows, tr("Ask before moving them"),
             _toggle(self.prefs.get("confirm_move"), lambda on: self._set("confirm_move", on)),
             tr("Files the manager did not write - a mod pasted in by hand, an old copy of a game file - are "
                "moved to a backup when you apply. Restore game puts them back."))
        _row(rows, tr("Backups to keep"),
             _switch(((0, tr("All")), (10, "10"), (5, "5"), (3, "3")), self.prefs.get("keep_backups"),
                     lambda v: self._set("keep_backups", v)),
             tr("The newest ones are kept. The very first, what the folder held before the manager ever "
                "touched it, is always kept."))
        box.addWidget(c)
        box.addStretch(1)
        return page

    def _page_updates(self) -> QWidget:
        page, box = self._page()
        c, rows = _card(tr("Checking"))
        _row(rows, tr("Look for new versions"),
             _switch((("open", tr("When I open it")), ("daily", tr("Once a day")), ("never", tr("Never"))),
                     self.prefs.get("update_check"), lambda v: self._set("update_check", v)),
             tr("Only mods that follow a page are checked: IFN1, the Localization Overhaul Project and WTHLM are "
                "found by themselves, any other mod follows the page you give it. When I open it means at most "
                "once an hour."))
        _row(rows, tr("When there is one"),
             _switch((("ask", tr("Tell me")), ("auto", tr("Install it"))), self.prefs.get("update_install"),
                     lambda v: self._set("update_install", v)),
             tr("Install it downloads the new version and adds it just as adding it by hand does: your edited "
                "files and optional modules stay. Play and the War Thunder shortcut then fetch updates too, "
                "before the game starts."))
        _row(rows, tr("Then apply"),
             _toggle(self.prefs.get("update_apply"), lambda on: self._set("update_apply", on)),
             tr("After an update, write it into the game straight away, if your mods were applied before."))
        box.addWidget(c)

        if selfupdate.available():
            c, rows = _card(tr("Langmod Manager itself"))
            _row(rows, tr("Look for new versions of it"),
                 _toggle(self.prefs.get("self_check"), lambda on: self._set("self_check", on)),
                 tr("Once a day. A new version shows at the top of the window, to install with one click; your "
                    "mods and settings stay as they are."))
            self.self_check_btn = GlowButton(tr("Check now"))
            self.self_check_btn.clicked.connect(self._check_self)
            self.self_state = _row(rows, self._self_state(), self.self_check_btn).findChild(_Row).name
            box.addWidget(c)

        c, rows = _card(tr("Where updates come from"))
        if not sources.NEXUS:
            note = QLabel(tr("<b>WT Live</b> and <b>GitHub</b>: nothing to set up and no account needed.")
                          + "<br>" + tr("<b>Nexus Mods</b> (where Vortex gets its mods) is not checked by this copy: "
                                        "Nexus lets a program that is shared round use its API only once it is "
                                        "registered with them. Many mods are on WT Live or GitHub as well; follow "
                                        "that page instead. IFN1 does so by itself."), objectName="hint")
            note.setWordWrap(True)
            rows.addWidget(note)
            box.addWidget(c)
            box.addStretch(1)
            return page
        note = QLabel(tr("<b>WT Live</b> and <b>GitHub</b>: nothing to set up and no account needed.")
                      + "<br>" + tr("<b>Nexus Mods</b> (where Vortex gets its mods): paste your own API key below, "
                                    "from your Nexus account's API page. The manager can then see new versions, but "
                                    "Nexus only lets Premium members download from a program; for everyone else it "
                                    "opens the download page, and you add the file with Update. The key is for your "
                                    "own use; share the manager with others and it would need registering with "
                                    "Nexus first."), objectName="hint")
        note.setWordWrap(True)
        rows.addWidget(note)
        key = QLineEdit()
        key.setEchoMode(QLineEdit.EchoMode.Password)
        key.setPlaceholderText(tr("your personal API key (optional)"))
        key.setText(self.prefs.get("nexus_key"))
        key.setMinimumWidth(260)
        key.editingFinished.connect(lambda: self._set("nexus_key", key.text().strip()))
        self.nexus_key = key
        _row(rows, tr("Nexus Mods API key"), key)
        page_btn = GlowButton(tr("Open the API key page"))
        page_btn.setIcon(icon("link", tokens()["muted"], 15))
        page_btn.clicked.connect(lambda: QDesktopServices.openUrl(
            QUrl("https://www.nexusmods.com/users/myaccount?tab=api")))
        _row(rows, tr("Where to get one"), page_btn)
        box.addWidget(c)
        box.addStretch(1)
        return page

    def _page_shortcuts(self) -> QWidget:
        page, box = self._page()
        self.shortcut_controls: list[ShortcutControl] = []
        for kind, title, what in (
                ("manager", "Langmod Manager", tr("Opens this window.")),
                ("play", tr("War Thunder with mods"),
                 tr("Applies your mods if the game or the mods changed since last time, then starts War Thunder. "
                    "Use it instead of the game's own icon and updates never catch you out."))):
            c, rows = _card(title)
            note = QLabel(what, objectName="hint")
            note.setWordWrap(True)
            rows.addWidget(note)
            for where, label in (("desktop", tr("On the desktop")), ("programs", tr("In the Start menu"))):
                ctrl = ShortcutControl(self.lib, kind, where)
                self.shortcut_controls.append(ctrl)
                _row(rows, label, ctrl)
            box.addWidget(c)
        box.addWidget(self._steam_card())
        c, rows = _card(tr("Icon"))
        _row(rows, tr("Shortcut icons follow the theme"),
             _toggle(self.prefs.get("icons_follow_theme"), self._set_follow),
             tr("Each theme has its own icon. With this on, the shortcuts change with the theme; with it off, they "
                "keep the Standard front one."))
        box.addWidget(c)
        box.addStretch(1)
        return page

    def _steam_card(self) -> QWidget:
        c, rows = _card(tr("Starting from Steam"))
        note = QLabel(tr("Steam starts War Thunder by itself, so after a game update your mods wait until the "
                         "manager is opened. Give the game this line in Steam, and Steam starts it through the "
                         "manager, which brings the mods up to date first."), objectName="hint")
        note.setWordWrap(True)
        rows.addWidget(note)
        ctrl = QWidget()
        line = QHBoxLayout(ctrl)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(8)
        self.steam_line = QLineEdit(steam.option())
        self.steam_line.setReadOnly(True)
        self.steam_line.setCursorPosition(0)
        self.steam_line.setMinimumWidth(440)
        copy = GlowButton(tr("Copy"))
        copy.clicked.connect(lambda: (QApplication.clipboard().setText(self.steam_line.text()),
                                      copy.setText(tr("Copied"))))
        line.addWidget(self.steam_line, 1)
        line.addWidget(copy)
        _row(rows, tr("Launch options"), ctrl,
             tr("In Steam, right-click War Thunder, then Properties, General, and paste it into Launch options. "
                "To stop, empty that box again, and do so before deleting the manager, or Steam cannot start "
                "the game. After moving the manager's folder, paste the line again."))
        state = steam.state()
        self.steam_state = chip(tr("Steam starts the game through the manager") if state == "here" else
                                tr("Steam points at another copy of the manager: paste the line again"),
                                "ok" if state == "here" else "warn")
        self.steam_state.setVisible(bool(state))
        rows.addWidget(self.steam_state, 0, Qt.AlignmentFlag.AlignLeft)
        return c

    def _page_folders(self) -> QWidget:
        page, box = self._page()
        c, rows = _card(tr("Where things are"))
        for title, path, hint in (
                (tr("Your mods"), self.lib.root / "mods", tr("Each mod you add is copied here, so the download can "
                                                             "go. Its folder can also be opened from its menu.")),
                (tr("Backups"), self.lib.root / "backups", tr("What Apply moved out of the game's lang folder, and "
                                                              "copies of config.blk from before it changed it."))):
            ctrl = QWidget()
            line = QHBoxLayout(ctrl)
            line.setContentsMargins(0, 0, 0, 0)
            line.setSpacing(8)
            line.addWidget(_elided(str(path), 280))
            open_btn = GlowButton(tr("Open"))
            open_btn.setIcon(icon("folder", tokens()["muted"], 16))
            open_btn.clicked.connect(lambda _=False, p=path: self._open(p))
            line.addWidget(open_btn)
            _row(rows, title, ctrl, hint)
        box.addWidget(c)
        c, rows = _card(tr("About"))
        about = QLabel(tr("Langmod Manager {version}. Loads several War Thunder language mods at once and keeps "
                          "them working through game updates.", version=__version__),
                       objectName="hint")
        about.setWordWrap(True)
        rows.addWidget(about)
        small = QLabel(tr("Free software under the MIT licence. It is built with Qt and PySide6, used under the "
                          "GNU LGPL version 3; their licences, and where to get their source, are in “Third-party "
                          "notices.txt” beside the program. Not made by, endorsed by or connected with Gaijin "
                          "Entertainment, the makers of War Thunder."), objectName="faint")
        small.setWordWrap(True)
        rows.addWidget(small)
        tour = GlowButton(tr("Show the tour again"))
        tour.setIcon(icon("help", tokens()["muted"], 16))
        tour.clicked.connect(self._tour)
        _row(rows, tr("The guided tour"), tour, tr("Walks through every button of the main window, one at a time. "
                                                   "F1 or the ? button at the top start it too."))
        self.report_btn = GlowButton(tr("Copy a bug report"))
        self.report_btn.setIcon(icon("report", tokens()["muted"], 16))
        self.report_btn.clicked.connect(self._copy_report)
        _row(rows, tr("Something wrong?"), self.report_btn,
             tr("Puts what someone helping you needs on the clipboard: the versions, the game, your mods in order "
                "and the last errors. No user name or other personal details. Paste it into your message."))
        box.addWidget(c)
        box.addStretch(1)
        return page

    def _self_state(self, error: str = "") -> str:
        if error:
            return tr("Could not check: {error}", error=error)
        new = selfupdate.known(self.prefs)
        if new is not None:
            return tr("Langmod Manager {version} is out; this is {current}.", version=new.version, current=__version__)
        if not self.prefs.get("self_last"):
            return tr("This is {version}. Not checked yet.", version=__version__)
        return tr("This is {version}, the newest.", version=__version__)

    def _check_self(self) -> None:
        parent = self.parent()
        if parent is None or not hasattr(parent, "look_for_new_self"):
            return
        self.self_check_btn.setEnabled(False)
        self.self_state.setText(tr("Checking…"))

        def then(error: str) -> None:
            try:
                self.self_check_btn.setEnabled(True)
                self.self_state.setText(self._self_state(error))
            except RuntimeError:
                pass                                   # Settings was closed meanwhile

        parent.look_for_new_self(now=True, then=then)

    def _tour(self) -> None:
        parent = self.parent()
        self.accept()
        if parent is not None and hasattr(parent, "start_tour"):
            from PySide6.QtCore import QTimer
            QTimer.singleShot(0, parent.start_tour)

    def _copy_report(self) -> None:
        parent = self.parent()
        if parent is not None and hasattr(parent, "copy_bug_report"):
            parent.copy_bug_report()
            self.report_btn.setText(tr("Copied: paste it into your message"))

    # -- changes ------------------------------------------------------------------------
    def _set_language(self, code: str) -> None:
        """A new language for the manager: the window is made again in it, and Settings with it."""
        if code == self.prefs.get("ui_language"):
            return
        self.prefs.set("ui_language", code)
        self.language_changed.emit()

    def _set(self, key: str, value) -> None:
        self.prefs.set(key, value)
        if key in ("motion", "amount", "motion_follows_windows"):
            self.look_changed.emit()

    def _pick_theme(self, key: str) -> None:
        if key == "picture" and not Path(self.prefs.get("picture") or "").is_file():
            if not self._choose_picture(apply=False):
                return                               # Your picture with no picture: nothing to show
        self.prefs.set("theme", key)
        for k, tc in self.theme_cards.items():
            tc.selected = k == key
            tc.update()
        self._show_picture_rows()
        self.look_changed.emit()
        self._follow_icons()

    def _show_picture_rows(self) -> None:
        path = self.prefs.get("picture")
        self.picture_card.setVisible(self.prefs.get("theme") == "picture")
        thumb = QPixmap(path) if path and Path(path).is_file() else QPixmap()
        if thumb.isNull():
            self.picture_label.setPixmap(QPixmap())
            self.picture_label.setText(tr("none chosen yet") if not path else tr("gone: choose another"))
        else:
            dpr = self.devicePixelRatioF()
            thumb = thumb.scaledToHeight(round(40 * dpr), Qt.TransformationMode.SmoothTransformation)
            thumb.setDevicePixelRatio(dpr)
            self.picture_label.setPixmap(thumb)
        self.picture_label.setToolTip(path)
        accent = self.prefs.get("picture_accent")
        shown = readable_accent(accent) if accent else (readable_accent(accent_from(path)) if path and accent_from(path)
                                                        else DEFAULT_ACCENT)
        self.accent_btn.setIcon(_swatch(shown))
        self.accent_btn.setText(tr("Chosen") if accent else tr("From the picture"))
        self.accent_auto.setVisible(bool(accent))
        if "picture" in getattr(self, "theme_cards", {}):
            self.theme_cards["picture"].set_picture(picture_config(self.prefs))

    def _choose_picture(self, apply: bool = True) -> bool:
        parent = self.parent()
        if parent is None or not hasattr(parent, "choose_picture") or not parent.choose_picture(self):
            return False
        self._show_picture_rows()
        if apply:
            self.look_changed.emit()
        return True

    def _choose_accent(self) -> None:
        now = QColor(self.prefs.get("picture_accent") or accent_from(self.prefs.get("picture")) or DEFAULT_ACCENT)
        colour = QColorDialog.getColor(now, self, tr("Accent colour"))
        if colour.isValid():
            self._set_picture("picture_accent", colour.name())

    def _set_picture(self, key: str, value) -> None:
        self.prefs.set(key, value)
        self._show_picture_rows()
        if self.prefs.get("theme") == "picture":
            self.look_changed.emit()

    def _set_mode(self, mode: str) -> None:
        self.prefs.set("standard_mode", mode)
        self.theme_cards["standard"].set_mode(mode)
        if self.prefs.get("theme") == "standard":
            self.look_changed.emit()

    def _set_follow(self, on: bool) -> None:
        self.prefs.set("icons_follow_theme", on)
        self._follow_icons()

    def _follow_icons(self) -> None:
        parent = self.parent()
        if parent is not None and hasattr(parent, "follow_shortcut_icons"):
            parent.follow_shortcut_icons()

    def _choose_folder(self, channel: str) -> None:
        path = QFileDialog.getExistingDirectory(self, tr("The War Thunder folder"))
        if not path:
            return
        if install_at(path) is None:
            QMessageBox.warning(self, tr("Not the game folder"),
                                tr("That folder has no lang.vromfs.bin. Choose the folder War Thunder is installed "
                                   "in; it also holds config.blk and aces.vromfs.bin."))
            return
        folders = self.prefs.get("folders")
        folders[channel] = str(Path(path))
        self.prefs.set("folders", folders)
        self.folder_labels[channel].setText(str(Path(path)))
        self.game_changed.emit()

    def _forget_folder(self, channel: str) -> None:
        folders = self.prefs.get("folders")
        folders.pop(channel, None)
        self.prefs.set("folders", folders)
        self.folder_labels[channel].setText(tr("found again on the next look"))
        self.game_changed.emit()

    @staticmethod
    def _open(path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _reset(self) -> None:
        if QMessageBox.question(self, tr("Put everything back"),
                                tr("Every setting goes back to how a fresh copy starts. Your mods, backups and "
                                   "shortcuts stay. Go ahead?")) != QMessageBox.StandardButton.Yes:
            return
        language = i18n.choose(self.prefs.get("ui_language"))
        self.prefs.reset()
        self.look_changed.emit()
        self.game_changed.emit()
        if i18n.choose(self.prefs.get("ui_language")) != language:
            self.language_changed.emit()             # the window, and this with it, are made again in it
            return
        current = self.sidebar.currentRow()
        # Rebuild the pages so every control shows the value it now has.
        while self.pages.count():
            w = self.pages.widget(0)
            self.pages.removeWidget(w)
            w.deleteLater()
        self.sidebar.blockSignals(True)
        self.sidebar.clear()
        self.sidebar.blockSignals(False)
        for _key, name, build in self._pages():
            self.sidebar.addItem(name)
            self.pages.addWidget(self._scrolled(build()))
        self.sidebar.setCurrentRow(current)
