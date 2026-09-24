"""A guided tour of the window, the way a game walks you through its screens.

The window dims except for a spotlight on one control at a time, with a
ring round it that pulses and now and then sends out a ping. A card beside
it says what the control is for, how far through the tour you are and how
many steps are left, and has Skip, Back and Next on it. The spotlight
glides from one control to the next rather than jumping, and the card slides
in after it. Arrow keys, Enter and Esc work too.

Steps whose control is not on screen (a notice that is not showing) are
left out when the tour starts, so the count it shows is the count you get.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import (QEasingCurve, QElapsedTimer, QPoint, QPointF, QPropertyAnimation, QRect, QRectF, QSize,
                            QTimer, QVariantAnimation, Qt, Signal)
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import (QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QVBoxLayout, QWidget)

from ..core import sources
from ..i18n import ntr, tr
from . import system
from .themes import current_theme, tokens
from .widgets import GlowButton, alpha

CARD_WIDTH = 372
GAP = 16            # between the spotlight and the card, room for the arrow
EDGE = 12           # the card keeps this far from the window's edges
PAD = 7             # the spotlight's margin around its control
MOVE_MS = 420
PULSE_FPS = 30
PING_EVERY = 1.8    # seconds between the rings that go out from the spotlight


@dataclass
class Step:
    title: str
    body: str
    targets: Callable[[], list[QWidget]] | None = None     # None: the card sits in the middle
    optional: bool = False                                  # left out if its control is not showing


def _showing(widgets: list[QWidget]) -> list[QWidget]:
    return [w for w in widgets if w is not None and w.isVisible() and w.width() > 0]


def steps_for(win) -> list[Step]:
    """The tour of the main window, top to bottom and left to right."""
    return [
        Step(tr("Welcome to Langmod Manager"),
             tr("A quick look at every button, one at a time: about a minute. Skip whenever you like; the "
             "? button at the top brings the tour back.")),
        Step(tr("Something needs you"),
             tr("Notices like this one appear here when there is something to do: a game update, a mod "
             "someone pasted into the game by hand, a game that was not found. The button on the right "
             "does it for you."),
             lambda: [win.banner, win.banner2], optional=True),
        Step(tr("Which game"),
             tr("Live is the game everyone plays; Dev is the dev server client, if you have it. Both are "
             "found on their own. Beside the switch: where the game came from, and its version."),
             lambda: [win.game_switch, win.game_meta]),
        Step(tr("Are your mods in?"),
             tr("Not applied yet, Applied, Changes not applied yet, or Game updated: apply again. One "
             "glance tells you whether what you see here is what the game will show."),
             lambda: [win.state_chip]),
        Step(tr("Your mods"),
             tr("Every mod you add, in the order the game loads them. Tick one to switch it on or off, "
             "drag it to move it. The number is its place: where two mods change the same string, the "
             "higher number wins."),
             lambda: [win.mod_list.parentWidget()]),
        Step(tr("Profiles"),
             tr("Named sets of mods and their order, such as historical names or IFN1 alone, each with its own "
                "picks. Switch between them here, or with Ctrl+1 to Ctrl+9. A profile can be exported to a file "
                "for a friend, whose manager fetches the mods they do not have yet."),
             lambda: _showing([win.profile_btn]), optional=True),
        Step(tr("Updates, fetched for you"),
             (tr("Mods can follow the page they are published on: a WT Live post, a GitHub repository or Nexus "
                 "Mods. IFN1 and a few others are found by themselves. The Updates button up here (there once you "
                 "have a mod) looks for new versions; a mod with one gets an UPDATE tag, and Settings can let them "
                 "install on their own.") if sources.NEXUS else
              tr("Mods can follow the page they are published on: a WT Live post or a GitHub repository. IFN1 and "
                 "a few others are found by themselves. The Updates button up here (there once you have a mod) "
                 "looks for new versions; a mod with one gets an UPDATE tag, and Settings can let them install on "
                 "their own.")),
             lambda: _showing([win.updates_btn]) or [win.add_btn.parentWidget()]),
        Step(tr("Add a mod"),
             tr("Get mods, at the top of this menu, fetches IFN1, the Localization Overhaul Project or WTHLM in "
             "one click. Or add a zip or 7z, a folder or a single .csv, just as it downloads: a newer version of a mod "
             "you have is spotted as an update, and an optional module is added to its mod. You can also drop "
             "files anywhere on the window."),
             lambda: [win.add_btn]),
        Step(tr("Update, remove, move"),
             tr("For the selected mod: Update takes a newer version or one of its modules, the bin removes "
             "it, and the arrows move it up or down the order. Your own edits are kept through updates."),
             lambda: [win.update_btn, win.remove_btn, win.up_btn, win.down_btn]),
        Step(tr("Your own strings"),
             tr("A file of your own that loads after every mod, so it wins over all of them. One line per "
             "string: its ID, a semicolon, your text. Easier still: find the string in the Strings tab and "
             "type your text there."),
             lambda: [win.mine_btn]),
        Step(tr("Details, strings, conflicts, load order"),
             tr("Details says what the selected mod changes, and what the manager fixed for this game "
                "version. Strings searches every string (Ctrl+F): type F-16 and see what the game says, what "
                "each mod says and which one shows, then pick another or type your own. Conflicts lists strings "
                "two mods both change, with the same choice. Load order is every file the game will read."),
             lambda: _showing([win.view_switch]) or [win.stack]),
        Step(tr("Apply to game"),
             tr("Puts your mods into the game's lang folder: each mod's files under their own names, and a load "
             "list rebuilt from the game's own, so new strings never show as their IDs. Then it switches on "
             "custom localization. The game's own files are never touched, so an update cannot undo anything: "
             "it only makes the list old, and applying again, or Play, rebuilds it. A mod already in that folder "
             "joins your mods first; anything else there goes to a backup."),
             lambda: [win.apply_btn]),
        Step(tr("Play"),
             tr("Applies your mods if the game or the mods changed since last time, then starts War "
             "Thunder. Use it, or the War Thunder with mods shortcut, and game updates never catch you "
             "out."),
             lambda: [win.play_btn]),
        Step(tr("Restore game"),
             tr("Takes the manager's files out of the game again, and can put back whatever was in the "
             "lang folder before you first applied."),
             lambda: [win.restore_btn]),
        Step(tr("Themes"),
             tr("Ten looks, from plain Standard front to scenes that move: stars, cherry petals, snow, "
             "sparks, northern lights, a factory, a starship, a battlefield, or a picture of your own. "
             "Settings shows them six to a page. The animation can be turned off, and stands still by "
             "itself when Windows' animation effects are off."),
             lambda: [win.theme_btn]),
        Step(tr("Settings"),
             tr("Everything else, each with a line saying what it does: when to apply after a game update, "
             "how many backups to keep, desktop shortcuts, and where things are kept."),
             lambda: [win.settings_btn]),
        Step(tr("The tour, again"),
             tr("This button starts the tour over, any time."),
             lambda: [win.help_btn]),
        Step(tr("News down here"),
             tr("What was just done shows along the bottom, and on the right, the game's language and "
             "whether custom localization is on."),
             lambda: [win.statusBar()]),
        Step(tr("That's everything"),
             tr("Add a mod to begin: IFN1's lang.zip works as it comes. Then Apply, or Play. Have fun!")),
    ]


class _Progress(QWidget):
    """The steps as a row of segments: done, this one, still to come."""

    def __init__(self):
        super().__init__()
        self.count, self.index = 1, 0
        self.setFixedHeight(6)
        self.setMinimumWidth(90)

    def set_state(self, index: int, count: int) -> None:
        self.index, self.count = index, max(count, 1)
        self.update()

    def paintEvent(self, e) -> None:
        t = tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        gap = 3.0
        w = (self.width() - gap * (self.count - 1)) / self.count
        for i in range(self.count):
            if i < self.index:
                colour = alpha(t["accent"], 0.55)
            elif i == self.index:
                colour = QColor(t["accent"])
            else:
                colour = QColor(t["border"])
            p.setBrush(colour)
            p.drawRoundedRect(QRectF(i * (w + gap), 1, w, 4), 2, 2)
        p.end()


class _Card(QFrame):
    def __init__(self, tour: "Tour"):
        super().__init__(tour, objectName="tourCard")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedWidth(CARD_WIDTH)
        box = QVBoxLayout(self)
        box.setContentsMargins(18, 14, 18, 14)
        box.setSpacing(8)
        head = QHBoxLayout()
        head.setSpacing(10)
        self.step = QLabel("", objectName="sectionTitle")
        self.progress = _Progress()
        self.skip = GlowButton(tr("Skip tour"), objectName="link")
        self.skip.setToolTip(tr("Leave the tour (Esc). The ? button at the top brings it back."))
        head.addWidget(self.step)
        head.addWidget(self.progress, 1)
        head.addWidget(self.skip)
        box.addLayout(head)
        self.title = QLabel("", objectName="tourTitle")
        self.title.setWordWrap(True)
        self.body = QLabel("", objectName="tourBody")
        self.body.setWordWrap(True)
        box.addWidget(self.title)
        box.addWidget(self.body)
        foot = QHBoxLayout()
        foot.setSpacing(8)
        self.left = QLabel("", objectName="faint")
        self.back = GlowButton(tr("Back"))
        self.next = GlowButton(tr("Next"), objectName="primary", light=True)
        self.next.setDefault(True)
        foot.addWidget(self.left)
        foot.addStretch(1)
        foot.addWidget(self.back)
        foot.addWidget(self.next)
        box.addSpacing(2)
        box.addLayout(foot)
        # The tour keeps the keyboard: a focused button would swallow the arrow keys.
        for b in (self.skip, self.back, self.next):
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.fade = QGraphicsOpacityEffect(self)
        self.fade.setOpacity(1.0)
        self.setGraphicsEffect(self.fade)

    def height_for_width(self) -> int:
        lay = self.layout()
        lay.activate()
        h = lay.totalHeightForWidth(CARD_WIDTH) if lay.hasHeightForWidth() else -1
        return max(h, self.sizeHint().height())


def lerp_rect(a: QRectF, b: QRectF, k: float) -> QRectF:
    return QRectF(a.x() + (b.x() - a.x()) * k, a.y() + (b.y() - a.y()) * k,
                  a.width() + (b.width() - a.width()) * k, a.height() + (b.height() - a.height()) * k)


def place_card(target: QRectF | None, size: QSize, bounds: QRect) -> tuple[QPoint, str]:
    """Where the card goes: below the spotlight, else above, right, left; or in the middle."""
    w, h = size.width(), size.height()
    lo_x, hi_x = bounds.left() + EDGE, bounds.right() - EDGE - w
    lo_y, hi_y = bounds.top() + EDGE, bounds.bottom() - EDGE - h

    def clamp(v, lo, hi):
        return int(max(lo, min(v, max(lo, hi))))

    if target is None or target.width() < 1:
        return QPoint(clamp(bounds.center().x() - w / 2, lo_x, hi_x),
                      clamp(bounds.center().y() - h / 2, lo_y, hi_y)), ""
    cx, cy = target.center().x(), target.center().y()
    options = (
        ("below", target.bottom() + GAP, target.bottom() + GAP + h <= bounds.bottom() - EDGE),
        ("above", target.top() - GAP - h, target.top() - GAP - h >= bounds.top() + EDGE),
        ("right", target.right() + GAP, target.right() + GAP + w <= bounds.right() - EDGE),
        ("left", target.left() - GAP - w, target.left() - GAP - w >= bounds.left() + EDGE),
    )
    for side, at, fits in options:
        if not fits:
            continue
        if side in ("below", "above"):
            return QPoint(clamp(cx - w / 2, lo_x, hi_x), int(at)), side
        return QPoint(int(at), clamp(cy - h / 2, lo_y, hi_y)), side
    # Nothing fits beside it (the control fills most of the window): sit over its middle.
    return QPoint(clamp(cx - w / 2, lo_x, hi_x), clamp(cy - h / 2, lo_y, hi_y)), ""


class Tour(QWidget):
    """The dimmed layer with its spotlight and card, over the whole window."""

    finished = Signal(bool)          # True when it ran to the end, False when skipped

    def __init__(self, window: QWidget, steps: list[Step]):
        super().__init__(window)
        self.window_ = window
        self.all_steps = steps
        self.steps: list[Step] = []
        self.index = -1
        self.side = ""
        self._from = self._to = self._spot = QRectF()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.card = _Card(self)
        self.card.skip.clicked.connect(self.skip)
        self.card.back.clicked.connect(self.back)
        self.card.next.clicked.connect(self.next)
        self.move_anim = QVariantAnimation(self)
        self.move_anim.setDuration(MOVE_MS)
        self.move_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.move_anim.setStartValue(0.0)
        self.move_anim.setEndValue(1.0)
        self.move_anim.valueChanged.connect(self._moved)
        self.move_anim.finished.connect(self._arrive)
        self.card_slide = QPropertyAnimation(self.card, b"pos", self)
        self.card_slide.setDuration(260)
        self.card_slide.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.card_fade = QPropertyAnimation(self.card.fade, b"opacity", self)
        self.card_fade.setDuration(220)
        self.pulse = QTimer(self)
        self.pulse.setTimerType(Qt.TimerType.PreciseTimer)
        self.pulse.setInterval(round(1000 / PULSE_FPS))
        self.pulse.timeout.connect(self.update)
        self.clock = QElapsedTimer()
        window.installEventFilter(self)
        self.hide()

    # -- running ----------------------------------------------------------------------
    def start(self) -> None:
        self.steps = [s for s in self.all_steps
                      if not (s.optional and not _showing(s.targets() if s.targets else []))]
        self.setGeometry(self.window_.rect())
        self.show()
        self.raise_()
        self.setFocus()
        self.clock.start()
        still = not system.animations_on()        # Windows' Animation effects off: no gliding, no pulsing
        for anim, ms in ((self.move_anim, MOVE_MS), (self.card_slide, 260), (self.card_fade, 220)):
            anim.setDuration(1 if still else ms)
        if not still:
            self.pulse.start()
        self._spot = self._from = self._to = QRectF(QPointF(self.rect().center()), QSize(0, 0))
        self.go(0, animate=True)

    @property
    def running(self) -> bool:
        return self.isVisible()

    def target_rect(self, step: Step) -> QRectF | None:
        if step.targets is None:
            return None
        widgets = _showing(step.targets())
        if not widgets:
            return None
        rect = QRect()
        for w in widgets:
            rect = rect.united(QRect(w.mapTo(self.window_, QPoint(0, 0)), w.size()))
        return QRectF(rect).adjusted(-PAD, -PAD, PAD, PAD).intersected(QRectF(self.rect()).adjusted(2, 2, -2, -2))

    def go(self, index: int, animate: bool = True) -> None:
        if not self.steps:
            self._end(True)
            return
        self.index = max(0, min(index, len(self.steps) - 1))
        step = self.steps[self.index]
        n, i = len(self.steps), self.index
        c = self.card
        c.step.setText(tr("STEP {step} OF {steps}", step=i + 1, steps=n))
        c.progress.set_state(i, n)
        c.title.setText(step.title)
        c.body.setText(step.body)
        left = n - i - 1
        c.left.setText(tr("last one") if left == 0 else ntr(left, "{n} to go", "{n} to go"))
        c.back.setEnabled(i > 0)
        c.back.setVisible(i > 0)
        c.next.setText(tr("Let's go") if i == 0 else (tr("Finish") if left == 0 else tr("Next")))
        c.skip.setVisible(left > 0)
        target = self.target_rect(step)
        centre = QRectF(QPointF(self.rect().center()), QSize(0, 0))
        self._from = QRectF(self._spot)
        self._to = target if target is not None else centre
        self.card.setFixedHeight(self.card.height_for_width())
        pos, self.side = place_card(target, QSize(CARD_WIDTH, self.card.height()), self.rect())
        self._card_pos = pos
        self.move_anim.stop()
        if animate and self.isVisible():
            self.card_fade.stop()
            self.card.fade.setOpacity(0.0)
            self.move_anim.start()
        else:
            self._spot = QRectF(self._to)
            self.card.move(pos)
            self.card.fade.setOpacity(1.0)
            self.update()
        self.setFocus()

    def _moved(self, k) -> None:
        self._spot = lerp_rect(self._from, self._to, float(k))
        self.update()

    def _arrive(self) -> None:
        self._spot = QRectF(self._to)
        offset = {"below": QPoint(0, 10), "above": QPoint(0, -10), "right": QPoint(10, 0),
                  "left": QPoint(-10, 0)}.get(self.side, QPoint(0, 8))
        self.card_slide.stop()
        self.card_slide.setStartValue(self._card_pos + offset)
        self.card_slide.setEndValue(self._card_pos)
        self.card_fade.setStartValue(0.0)
        self.card_fade.setEndValue(1.0)
        self.card.move(self._card_pos + offset)
        self.card_slide.start()
        self.card_fade.start()
        self.update()

    def next(self) -> None:
        if self.index >= len(self.steps) - 1:
            self._end(True)
        else:
            self.go(self.index + 1)

    def back(self) -> None:
        if self.index > 0:
            self.go(self.index - 1)

    def skip(self) -> None:
        self._end(False)

    def _end(self, completed: bool) -> None:
        self.pulse.stop()
        self.move_anim.stop()
        self.hide()
        self.window_.removeEventFilter(self)
        self.finished.emit(completed)
        self.deleteLater()

    # -- events -------------------------------------------------------------------------
    def eventFilter(self, obj, e) -> bool:
        if obj is self.window_ and e.type() == e.Type.Resize and self.isVisible():
            self.setGeometry(self.window_.rect())
            QTimer.singleShot(0, lambda: self.go(self.index, animate=False) if self.isVisible() else None)
        return False

    def keyPressEvent(self, e) -> None:
        key = e.key()
        if key in (Qt.Key.Key_Right, Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space, Qt.Key.Key_PageDown):
            self.next()
        elif key in (Qt.Key.Key_Left, Qt.Key.Key_Backspace, Qt.Key.Key_PageUp):
            self.back()
        elif key == Qt.Key.Key_Escape:
            self.skip()
        else:
            super().keyPressEvent(e)

    def mousePressEvent(self, e) -> None:
        e.accept()          # the window underneath waits until the tour is over

    def wheelEvent(self, e) -> None:
        e.accept()

    # -- painting -------------------------------------------------------------------------
    def paintEvent(self, e) -> None:
        t = tokens()
        dark = current_theme().dark
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        spot = self._spot
        has_spot = spot.width() > 2 and spot.height() > 2
        shade = QPainterPath()
        shade.addRect(QRectF(self.rect()))
        hole = QPainterPath()
        if has_spot:
            hole.addRoundedRect(spot, 10, 10)
            shade = shade.subtracted(hole)
        p.fillPath(shade, QColor(0, 0, 0, 150) if dark else QColor(24, 16, 30, 118))
        seconds = self.clock.elapsed() / 1000.0
        if has_spot:
            breathe = 0.5 + 0.5 * math.sin(seconds * 3.2)
            for grow, a in ((7, 0.10), (4, 0.18), (2, 0.30)):
                p.setPen(QPen(alpha(t["accent"], a * (0.6 + 0.4 * breathe)), 2.0))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(spot.adjusted(-grow, -grow, grow, grow), 10 + grow, 10 + grow)
            p.setPen(QPen(alpha(t["accent"], 0.75 + 0.25 * breathe), 2.0))
            p.drawRoundedRect(spot, 10, 10)
            ping = (seconds % PING_EVERY) / PING_EVERY
            if ping < 0.6 and not self.move_anim.state() == QVariantAnimation.State.Running:
                k = ping / 0.6
                grow = 4 + 18 * k
                p.setPen(QPen(alpha(t["accent"], 0.45 * (1 - k)), 2.0))
                p.drawRoundedRect(spot.adjusted(-grow, -grow, grow, grow), 10 + grow, 10 + grow)
            self._arrow(p, t)
        p.end()

    def _arrow(self, p: QPainter, t: dict) -> None:
        """A small point on the card's edge, aimed at the spotlight."""
        if not self.side or self.card.fade.opacity() < 0.05:
            return
        c = QRectF(self.card.geometry())
        s = self._spot
        size = 9.0
        if self.side == "below":
            x = max(c.left() + 22, min(s.center().x(), c.right() - 22))
            tri = [QPointF(x - size, c.top() + 1), QPointF(x + size, c.top() + 1), QPointF(x, c.top() - size)]
        elif self.side == "above":
            x = max(c.left() + 22, min(s.center().x(), c.right() - 22))
            tri = [QPointF(x - size, c.bottom() - 1), QPointF(x + size, c.bottom() - 1), QPointF(x, c.bottom() + size)]
        elif self.side == "right":
            y = max(c.top() + 22, min(s.center().y(), c.bottom() - 22))
            tri = [QPointF(c.left() + 1, y - size), QPointF(c.left() + 1, y + size), QPointF(c.left() - size, y)]
        else:
            y = max(c.top() + 22, min(s.center().y(), c.bottom() - 22))
            tri = [QPointF(c.right() - 1, y - size), QPointF(c.right() - 1, y + size), QPointF(c.right() + size, y)]
        p.setOpacity(self.card.fade.opacity())
        p.setPen(QPen(alpha(t["accent"], 0.55), 1.0))
        p.setBrush(QColor(t["panel_solid"]))
        p.drawPolygon(QPolygonF(tri))
        p.setOpacity(1.0)
