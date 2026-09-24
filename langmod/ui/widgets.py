"""The widgets that give the window its feel.

Buttons whose highlight warms up and trails the cursor, leaning a couple of
pixels toward it on an underdamped spring; "portal" buttons that bloom and
let a few dots drift through; and the one-bar segmented switch. All of them
read their colours from the current theme, so they change with it.
"""
from __future__ import annotations

import math
import random

from PySide6.QtCore import (QEasingCurve, QElapsedTimer, QPointF, QRectF, QSize, QTimer, QVariantAnimation, Qt,
                            Signal)
from PySide6.QtGui import QColor, QFont, QFontMetrics, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient
from PySide6.QtWidgets import (QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QPushButton, QStyle,
                               QStyleOptionButton, QVBoxLayout, QWidget)

from .themes import tokens

HEADER_HEIGHT = 44
GLOW_IN_MS = 260
GLOW_OUT_MS = 420
SLIDE_MS = 260
FOLLOW_RATE = 10.9
FOLLOW_INTERVAL_MS = 16
FOLLOW_MAX_STEP = 0.1
PULL_DISTANCE = 2.6
PULL_SHORT_AXIS = 0.55
PULL_STIFFNESS = 220.0
PULL_DAMPING = 17.0
PULL_SUB_STEP = 1.0 / 240.0
PULL_REST = 0.04
HALO_BLUR = 26
PORTAL_DOTS = 7
DRIFT_INTERVAL_MS = round(1000 / 30)
DRIFT_MAX_STEP = 0.1
SWITCH_FOLLOW_RATE = 7.0


def alpha(color: str, value: float) -> QColor:
    c = QColor(color)
    c.setAlphaF(max(0.0, min(1.0, value)))
    return c


class _Spring:
    """A point drawn toward a target, arriving a shade past it and settling back."""

    __slots__ = ("x", "y", "vx", "vy")

    def __init__(self) -> None:
        self.x = self.y = self.vx = self.vy = 0.0

    def step(self, tx: float, ty: float, seconds: float) -> None:
        left = min(seconds, FOLLOW_MAX_STEP)
        while left > 0.0:
            dt = min(left, PULL_SUB_STEP)
            left -= dt
            self.vx += (PULL_STIFFNESS * (tx - self.x) - PULL_DAMPING * self.vx) * dt
            self.vy += (PULL_STIFFNESS * (ty - self.y) - PULL_DAMPING * self.vy) * dt
            self.x += self.vx * dt
            self.y += self.vy * dt

    def settle(self) -> None:
        self.x = self.y = self.vx = self.vy = 0.0

    @property
    def leaning(self) -> bool:
        return abs(self.x) >= PULL_REST or abs(self.y) >= PULL_REST

    @property
    def at_rest(self) -> bool:
        return not self.leaning and abs(self.vx) < 1.0 and abs(self.vy) < 1.0


def _lean(point: QPointF, w: float, h: float, centre: QPointF) -> tuple[float, float]:
    across = max(-1.0, min(1.0, (point.x() - centre.x()) / max(w / 2.0, 1.0)))
    down = max(-1.0, min(1.0, (point.y() - centre.y()) / max(h / 2.0, 1.0)))
    return across * PULL_DISTANCE, down * PULL_DISTANCE * PULL_SHORT_AXIS


def _timer(owner, tick, interval: int) -> tuple[QTimer, QElapsedTimer]:
    t = QTimer(owner)
    t.setTimerType(Qt.TimerType.PreciseTimer)
    t.setInterval(interval)
    t.timeout.connect(tick)
    clock = QElapsedTimer()
    clock.start()
    return t, clock


def frame_seconds(clock: QElapsedTimer, longest: float = DRIFT_MAX_STEP) -> float:
    return min(max(clock.restart() / 1000.0, 0.0), longest)


class _Dot:
    __slots__ = ("x", "y", "speed", "size", "lift")

    def __init__(self, w: float, h: float, anywhere: bool):
        self.respawn(w, h, anywhere)

    def respawn(self, w: float, h: float, anywhere: bool = False) -> None:
        self.x = random.uniform(0, w) if anywhere else random.uniform(-10.0, -2.0)
        band = (0.14, 0.30) if random.random() < 0.5 else (0.70, 0.86)
        self.y = random.uniform(h * band[0], h * band[1])
        self.speed = random.uniform(19.0, 49.0)
        self.size = random.uniform(0.9, 1.7)
        self.lift = random.uniform(-0.64, 0.64)


def _drift(dots: list, w: float, h: float, seconds: float) -> None:
    for d in dots:
        d.x += d.speed * seconds
        d.y += d.lift * seconds
        if d.x > w + 10 or not 0 < d.y < h:
            d.respawn(w, h)


class _Glow:
    """A 0..1 value that eases in on hover and out on leave."""

    def _init_glow(self, on_change) -> None:
        self._glow = 0.0
        self._anim = QVariantAnimation(self)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(0.0)
        self._anim.valueChanged.connect(on_change)

    def _animate_glow(self, target: float) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._glow)
        self._anim.setEndValue(target)
        self._anim.setDuration(GLOW_IN_MS if target > self._glow else GLOW_OUT_MS)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.start()


class GlowButton(QPushButton, _Glow):
    """A button whose highlight warms up and trails the cursor across it."""

    def __init__(self, text: str = "", parent=None, radius: int = 6, strength: float = 0.16,
                 light: bool = False, **kwargs):
        super().__init__(text, parent, **kwargs)
        self.radius = radius
        self.strength = strength
        self.light = light
        self._init_glow(self._on_glow)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._spot = QPointF()
        self._target = QPointF()
        self._inside = False
        self._pull = _Spring()
        self._follow, self._follow_clock = _timer(self, self._step, FOLLOW_INTERVAL_MS)

    def _centre(self) -> QPointF:
        return QPointF(self.width() / 2.0, self.height() / 2.0)

    def _start_follow(self) -> None:
        if not self._follow.isActive():
            self._follow_clock.restart()
        self._follow.start()

    def _on_glow(self, value) -> None:
        self._glow = float(value)
        if self._glow <= 0.01 and self._follow.isActive() and self._pull.at_rest:
            self._follow.stop()
        self.update()

    def _step(self) -> None:
        self.follow(frame_seconds(self._follow_clock, FOLLOW_MAX_STEP))

    def follow(self, seconds: float) -> None:
        settled = False
        delta = self._target - self._spot
        if abs(delta.x()) < 0.4 and abs(delta.y()) < 0.4:
            self._spot = QPointF(self._target)
            settled = self._glow >= 0.99 or self._glow <= 0.01
        else:
            k = 1.0 - math.exp(-FOLLOW_RATE * seconds)
            self._spot = QPointF(self._spot.x() + delta.x() * k, self._spot.y() + delta.y() * k)
        target = (0.0, 0.0)
        if self._inside and self.isEnabled():
            target = _lean(self._target, self.width(), self.height(), self._centre())
        self._pull.step(*target, seconds)
        if settled and self._pull.at_rest:
            self._pull.settle()
            self._follow.stop()
        self.update()

    def enterEvent(self, event) -> None:
        if self.isEnabled():
            try:
                self._target = QPointF(event.position())
            except AttributeError:
                self._target = self._centre()
            self._spot = QPointF(self._target)
            self._inside = True
            self._animate_glow(1.0)
            self._start_follow()
        super().enterEvent(event)

    def mouseMoveEvent(self, event) -> None:
        self._target = QPointF(event.position())
        self._inside = True
        if self._glow > 0.01 and not self._follow.isActive():
            self._start_follow()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:
        self._inside = False
        self._animate_glow(0.0)
        self._start_follow()
        super().leaveEvent(event)

    def hideEvent(self, event) -> None:
        self._inside = False
        self._pull.settle()
        super().hideEvent(event)

    def _paint_chrome(self, event) -> None:
        if not self._pull.leaning:
            super().paintEvent(event)
            return
        option = QStyleOptionButton()
        self.initStyleOption(option)
        p = QPainter(self)
        style = self.style()
        style.drawControl(QStyle.ControlElement.CE_PushButtonBevel, option, p, self)
        p.translate(self._pull.x, self._pull.y)
        style.drawControl(QStyle.ControlElement.CE_PushButtonLabel, option, p, self)
        p.end()

    def paintEvent(self, event) -> None:
        self._paint_chrome(event)
        if self._glow <= 0.01 or not self.isEnabled():
            return
        colour = "#ffffff" if self.light else tokens()["accent"]
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        spot = self._spot if not self._spot.isNull() else self._centre()
        spot = QPointF(spot.x() + self._pull.x * 1.6, spot.y() + self._pull.y * 1.6)
        path = QPainterPath()
        path.addRoundedRect(rect, self.radius, self.radius)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setClipPath(path)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(alpha(colour, self.strength * 0.45 * self._glow))
        p.drawRect(rect)
        pool = QRadialGradient(spot, max(rect.width(), rect.height()) * 0.85)
        pool.setColorAt(0.0, alpha(colour, self.strength * 1.25 * self._glow))
        pool.setColorAt(1.0, alpha(colour, 0.0))
        p.setBrush(pool)
        p.drawRect(rect)
        p.end()


class PortalButton(GlowButton):
    """A button that leads somewhere else - here, into the game - and looks it:
    a glow blooms around it and a few dots drift through while it is pointed at."""

    def __init__(self, text: str = "", parent=None, **kwargs):
        super().__init__(text, parent, **kwargs)
        self._halo = QGraphicsDropShadowEffect(self)
        self._halo.setOffset(0, 0)
        self._halo.setBlurRadius(0)
        self._halo.setEnabled(False)
        self.setGraphicsEffect(self._halo)
        self._dots: list[_Dot] = []
        self._drift, self._drift_clock = _timer(self, self._move_dots, DRIFT_INTERVAL_MS)

    def _on_glow(self, value) -> None:
        super()._on_glow(value)
        halo = getattr(self, "_halo", None)
        if halo is None:
            return
        g = self._glow
        halo.setEnabled(g > 0.01)
        halo.setBlurRadius(HALO_BLUR * g)
        halo.setColor(alpha(tokens()["accent"], 0.55 * g))
        if g > 0.01 and not self._drift.isActive():
            if not self._dots:
                self._dots = [_Dot(self.width(), self.height(), True) for _ in range(PORTAL_DOTS)]
            self._drift_clock.start()
            self._drift.start()
        elif g <= 0.01 and self._drift.isActive():
            self._drift.stop()
            self._dots = []

    def _move_dots(self) -> None:
        _drift(self._dots, self.width(), self.height(), frame_seconds(self._drift_clock))
        self.update()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if not self._dots or self._glow <= 0.01 or not self.isEnabled():
            return
        colour = "#ffffff" if self.light else tokens()["accent"]
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, self.radius, self.radius)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setClipPath(path)
        p.setPen(Qt.PenStyle.NoPen)
        for d in self._dots:
            edge = max(0.0, min(1.0, min(d.x, rect.width() - d.x) / 14.0))
            p.setBrush(alpha(colour, 0.75 * self._glow * edge))
            p.drawEllipse(QPointF(d.x, d.y), d.size, d.size)
        p.end()


class SegmentSwitch(QWidget, _Glow):
    """One bar split by thin lines, one segment per choice; the pill slides to the pick."""

    selected = Signal(str)

    def __init__(self, choices: list[tuple[str, str]], parent=None):
        super().__init__(parent)
        self.choices = choices
        self._current = choices[0][0] if choices else ""
        self._enabled = {k: True for k, _ in choices}
        self._tips: dict[str, str] = {}
        self._hover = -1
        self._sel = 0.0
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(30)
        self._init_glow(self._on_glow)
        self._dots: list[_Dot] = []
        self._glow_x = -1.0
        self._tx = self._ty = -1.0
        self._inside = False
        self._pull = _Spring()
        self._drift, self._drift_clock = _timer(self, self._tick, DRIFT_INTERVAL_MS)
        self._slide = QVariantAnimation(self)
        self._slide.setDuration(SLIDE_MS)
        self._slide.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._slide.valueChanged.connect(self._on_slide)
        self._fit()

    def _font(self) -> QFont:
        f = QFont(self.font())
        f.setBold(True)
        return f

    def _fit(self) -> None:
        m = QFontMetrics(self._font())
        self._seg = max([m.horizontalAdvance(label) + 30 for _k, label in self.choices] or [70])
        self.setFixedWidth(self._seg * max(len(self.choices), 1))

    def sizeHint(self) -> QSize:
        return QSize(self._seg * max(len(self.choices), 1), 30)

    def changeEvent(self, e) -> None:
        # The style sheet's font arrives after the switch is made; measure again
        # then, or a long label is clipped at the edge of its segment.
        if e.type() in (e.Type.FontChange, e.Type.StyleChange, e.Type.PolishRequest):
            self._fit()
        super().changeEvent(e)

    def showEvent(self, e) -> None:
        self._fit()
        super().showEvent(e)

    def _rect(self, i: float) -> QRectF:
        return QRectF(i * self._seg, 0, self._seg, self.height())

    def _index_at(self, x: float) -> int:
        i = int(x // self._seg) if self._seg > 0 else -1
        return i if 0 <= i < len(self.choices) else -1

    # -- state
    def set_choices(self, choices: list[tuple[str, str]], current: str = "") -> None:
        self.choices = choices
        keys = [k for k, _ in choices]
        self._enabled = {k: self._enabled.get(k, True) for k in keys}
        if current in keys:
            self._current = current
        elif self._current not in keys:
            self._current = keys[0] if keys else ""
        self._slide.stop()
        self._sel = float(keys.index(self._current)) if self._current in keys else 0.0
        self._fit()
        self.update()

    def set_label(self, key: str, label: str) -> None:
        self.set_choices([(k, label if k == key else old) for k, old in self.choices], self._current)

    def current(self) -> str:
        return self._current

    def set_current(self, key: str, animate: bool = True) -> None:
        keys = [k for k, _ in self.choices]
        if key not in keys or key == self._current:
            return
        self._current = key
        self._slide.stop()
        if animate and self.isVisible():
            self._slide.setStartValue(self._sel)
            self._slide.setEndValue(float(keys.index(key)))
            self._slide.start()
        else:
            self._sel = float(keys.index(key))
            self.update()

    def set_enabled_choice(self, key: str, on: bool) -> None:
        self._enabled[key] = on
        self.update()

    def set_tip(self, key: str, text: str) -> None:
        self._tips[key] = text

    def _on_slide(self, v) -> None:
        self._sel = float(v)
        self.update()

    def _on_glow(self, value) -> None:
        self._glow = float(value)
        if self._glow > 0.01 and not self._drift.isActive():
            if not self._dots:
                n = round(PORTAL_DOTS * max(len(self.choices), 2) / 2)
                self._dots = [_Dot(self.width(), self.height(), True) for _ in range(n)]
            self._drift_clock.start()
            self._drift.start()
        elif self._glow <= 0.01 and self._drift.isActive() and self._pull.at_rest:
            self._drift.stop()
            self._pull.settle()
            self._dots = []
        self.update()

    def _tick(self) -> None:
        seconds = frame_seconds(self._drift_clock)
        _drift(self._dots, self.width(), self.height(), seconds)
        if self._tx >= 0:
            if self._glow_x < 0:
                self._glow_x = self._tx
            self._glow_x += (self._tx - self._glow_x) * (1.0 - math.exp(-SWITCH_FOLLOW_RATE * seconds))
        target = (0.0, 0.0)
        if self._inside and self._tx >= 0:
            pill = self._rect(self._sel)
            target = _lean(QPointF(self._tx, self._ty), pill.width(), pill.height(), pill.center())
        self._pull.step(*target, seconds)
        if self._glow <= 0.01 and self._pull.at_rest and self._drift.isActive():
            self._drift.stop()
            self._pull.settle()
            self._dots = []
        self.update()

    # -- input
    def mouseMoveEvent(self, e) -> None:
        self._tx, self._ty = e.position().x(), e.position().y()
        self._inside = True
        i = self._index_at(self._tx)
        if i != self._hover:
            self._hover = i
            self.setToolTip(self._tips.get(self.choices[i][0], "") if i >= 0 else "")
            self.update()
        super().mouseMoveEvent(e)

    def enterEvent(self, e) -> None:
        try:
            self._tx, self._ty = e.position().x(), e.position().y()
        except AttributeError:
            self._tx, self._ty = self.width() / 2.0, self.height() / 2.0
        self._glow_x = self._tx
        self._inside = True
        self._animate_glow(1.0)
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:
        self._hover = -1
        self._inside = False
        self._animate_glow(0.0)
        super().leaveEvent(e)

    def hideEvent(self, e) -> None:
        self._anim.stop()
        self._hover = -1
        self._inside = False
        self._pull.settle()
        self._on_glow(0.0)
        super().hideEvent(e)

    def mousePressEvent(self, e) -> None:
        i = self._index_at(e.position().x())
        if i >= 0:
            key = self.choices[i][0]
            if self._enabled.get(key, True) and key != self._current:
                self.set_current(key)
                self.selected.emit(key)
        super().mousePressEvent(e)

    # -- painting
    def paintEvent(self, e) -> None:
        t = tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        r = 8.0
        path = QPainterPath()
        path.addRoundedRect(rect, r, r)
        p.save()
        p.setClipPath(path)
        p.fillRect(rect, QColor(t["raised"]))
        if self._glow > 0.01 and len(self.choices) > 1:
            mid = self._seg * (len(self.choices) / 2.0)
            if len(self.choices) > 2 and self._glow_x >= 0:
                mid = self._glow_x
            spread = max(self._seg * 1.15 * self._glow, 1.0)
            g = QLinearGradient(QPointF(mid - spread, 0), QPointF(mid + spread, 0))
            g.setColorAt(0.0, alpha(t["accent"], 0.0))
            g.setColorAt(0.5, alpha(t["accent"], 0.20 * self._glow))
            g.setColorAt(1.0, alpha(t["accent"], 0.0))
            p.fillRect(rect, g)
        sel = self._rect(self._sel).adjusted(3, 3, -3, -3)
        sel.translate(self._pull.x, self._pull.y)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(t["accent_soft"]))
        p.drawRoundedRect(sel, r - 2, r - 2)
        if self._dots and self._glow > 0.01:
            for d in self._dots:
                edge = max(0.0, min(1.0, min(d.x, rect.width() - d.x) / 14.0))
                p.setBrush(alpha(t["accent"], 0.75 * self._glow * edge))
                p.drawEllipse(QPointF(d.x, d.y), d.size, d.size)
        p.restore()
        line, acc = QColor(t["border"]), QColor(t["accent"])
        for i in range(1, len(self.choices)):
            x = self._seg * i
            mix = QColor(int(line.red() + (acc.red() - line.red()) * self._glow),
                         int(line.green() + (acc.green() - line.green()) * self._glow),
                         int(line.blue() + (acc.blue() - line.blue()) * self._glow))
            p.setPen(QPen(mix, 1.0))
            p.drawLine(QPointF(x, 5.0), QPointF(x, self.height() - 5.0))
        p.setFont(self._font())
        for i, (key, label) in enumerate(self.choices):
            if not self._enabled.get(key, True):
                c = QColor(t["faint"])
            elif key == self._current:
                c = QColor(t["accent"])
            elif i == self._hover:
                c = QColor(t["text"])
            else:
                c = QColor(t["muted"])
            p.setPen(c)
            p.drawText(self._rect(i), Qt.AlignmentFlag.AlignCenter, label)
        p.setPen(QPen(QColor(t["border"]), 1.0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(rect, r, r)
        p.end()


class PanelHeader(QWidget):
    """A header of fixed height, so titles line up across panels."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("panelHeader")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(HEADER_HEIGHT)
        self.row = QHBoxLayout(self)
        self.row.setContentsMargins(14, 0, 10, 0)
        self.row.setSpacing(8)

    def add(self, w: QWidget, stretch: int = 0) -> QWidget:
        self.row.addWidget(w, stretch, Qt.AlignmentFlag.AlignVCenter)
        return w

    def add_stretch(self, n: int = 1) -> None:
        self.row.addStretch(n)


def section_label(text: str) -> QLabel:
    return QLabel(text.upper(), objectName="sectionTitle")


def card() -> QFrame:
    f = QFrame(objectName="card")
    f.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    return f


def chip(text: str = "", tone: str = "") -> QLabel:
    lab = QLabel(text, objectName="chip")
    lab.setProperty("tone", tone)
    return lab


def set_tone(widget: QWidget, tone: str, text: str | None = None) -> None:
    if text is not None:
        widget.setText(text)
    widget.setProperty("tone", tone)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


class Banner(QFrame):
    """A slim notice across the window, with at most one thing to do about it."""

    def __init__(self):
        super().__init__(objectName="banner")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 7, 8, 7)
        lay.setSpacing(10)
        self.icon = QLabel()
        self.icon.setFixedSize(18, 18)
        self.label = QLabel()
        self.label.setWordWrap(True)
        self.button = GlowButton()
        lay.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignTop)
        lay.addWidget(self.label, 1)
        lay.addWidget(self.button)
        self._action = None
        self.button.clicked.connect(lambda: self._action and self._action())
        self.hide()

    def show_message(self, text: str, button: str = "", action=None, tone: str = "", glyph=None,
                     tip: str = "") -> None:
        self.label.setText(text)
        self.button.setText(button)
        self.button.setToolTip(tip)
        self.button.setVisible(bool(button))
        self._action = action
        self.icon.setVisible(glyph is not None)
        if glyph is not None:
            self.icon.setPixmap(glyph.pixmap(18, 18))
        set_tone(self, tone)
        self.show()


class EmptyState(QWidget):
    """What a panel shows when there is nothing in it yet: a line, a reason, a way on."""

    def __init__(self, title: str, subtitle: str, parent=None):
        super().__init__(parent)
        box = QVBoxLayout(self)
        box.addStretch(1)
        self.glyph = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        self.title = QLabel(title, objectName="bigTitle", alignment=Qt.AlignmentFlag.AlignCenter)
        self.subtitle = QLabel(subtitle, objectName="hint", alignment=Qt.AlignmentFlag.AlignCenter)
        self.subtitle.setWordWrap(True)
        self.actions = QHBoxLayout()
        self.actions.addStretch(1)
        box.addWidget(self.glyph)
        box.addSpacing(6)
        box.addWidget(self.title)
        box.addWidget(self.subtitle)
        box.addSpacing(10)
        box.addLayout(self.actions)
        box.addStretch(1)

    def add_action(self, button: QPushButton) -> None:
        self.actions.insertWidget(self.actions.count(), button)

    def finish(self) -> None:
        self.actions.addStretch(1)
