"""The moving pictures behind the window, one per theme.

Every scene is a still layer - the sky, the land, the branch, the wall -
drawn once into a pixmap whenever the window changes size, and a set of
moving things painted over it each frame: stars that twinkle and now and
then shoot, cherry petals that tumble on a gusting wind, snow in three
depths, a fire and its sparks, curtains of northern light over a lake,
gears and belts at work, a starship among the rocks, a tank on a hill
before a city under smoke, the player's own picture drifting. Scenes with
things in front of what moves (the aurora's mountains, the factory's near
belt, the fire's logs) keep a second still layer for them.

The still layers carry the detail, since they cost nothing once drawn. Soft
things in them (glows, clouds, mist, dust lanes) go through :func:`_soft`,
painted small and smoothed up; :func:`_blob` lays each one down from a
sprite; and a faint :func:`_grain` hides the banding of 8-bit gradients.
Movement is by elapsed time, not by frame, so a slow frame changes how
smooth it looks and never how fast things go. With motion off, the scene is
painted once and holds still.
"""
from __future__ import annotations

import math
import random
import time

from PySide6.QtCore import QElapsedTimer, QPointF, QRectF, QSize, QTimer, Qt
from PySide6.QtGui import (QBrush, QColor, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap,
                           QPolygonF, QRadialGradient, QTransform)
from PySide6.QtWidgets import QWidget

from .themes import Theme

AMOUNTS = {"few": 0.55, "some": 1.0, "many": 1.8}
FPS = 30                   # frames a second, unless the player chooses otherwise
FPS_RANGE = (10, 60)
MAX_STEP = 0.1
#: Frame rates the background eases down through, from the one chosen, when drawing it costs
#: too much: a big window at high display scaling, or a slow computer. Movement goes by the
#: clock, so things keep their speed and only move in fewer, larger steps.
STEPS = (48, 40, 30, 24, 20, 15, 12, 10)
BUDGET = 0.20              # the share of one core it may take at 30 frames; more for more frames


def ladder(fps: int) -> tuple[int, ...]:
    """The chosen rate, then the slower ones it may ease down to."""
    return (fps,) + tuple(r for r in STEPS if r < fps)


PACE_MS = 2000             # how long it measures before it steps
SETTLE_MS = 150            # a resize is drawn anew once the window has kept its size this long
BASE_AREA = 1180 * 760


def _c(colour: str, a: float | None = None) -> QColor:
    q = QColor(colour)
    if a is not None:
        q.setAlphaF(max(0.0, min(1.0, a)))
    return q


def _mix(a: QColor, b: QColor, k: float) -> QColor:
    """``a`` taken ``k`` of the way to ``b``: how a thing fades into the haze with distance."""
    k = max(0.0, min(1.0, k))
    return QColor(round(a.red() + (b.red() - a.red()) * k), round(a.green() + (b.green() - a.green()) * k),
                  round(a.blue() + (b.blue() - a.blue()) * k))


def _sprite(inner: QColor, outer: QColor, size: int = 48, core: float = 0.16) -> QPixmap:
    """A soft round glow, drawn once and scaled wherever it is needed."""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    g = QRadialGradient(QPointF(size / 2, size / 2), size / 2)
    g.setColorAt(0.0, inner)
    g.setColorAt(core, inner)
    g.setColorAt(core + 0.14, outer)
    transparent = QColor(outer)
    transparent.setAlpha(0)
    g.setColorAt(1.0, transparent)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(g)
    p.drawEllipse(QRectF(0, 0, size, size))
    p.end()
    return pm


#: A soft falloff for glows and clouds, near to a bell curve: no bright point in the middle,
#: no ring at the edge, which a plain two-stop radial gradient has.
BELL = ((0.0, 1.0), (0.2, 0.88), (0.4, 0.60), (0.6, 0.30), (0.8, 0.10), (1.0, 0.0))


_blob_sprites: dict[int, QPixmap] = {}


def _blob(p: QPainter, x: float, y: float, r: float, colour: QColor, stretch: float = 1.0,
          turn: float = 0.0) -> None:
    """A soft round (or, stretched, oval) cloud of ``colour``, turned ``turn`` degrees. Scenes lay
    down thousands, so each colour's cloud is drawn once, at full strength, and then only
    scaled into place, as see-through as the colour is."""
    rgb = colour.rgb() & 0xFFFFFF
    sprite = _blob_sprites.get(rgb)
    if sprite is None:
        size = 64
        sprite = QPixmap(size, size)
        sprite.fill(Qt.GlobalColor.transparent)
        q = QPainter(sprite)
        g = QRadialGradient(QPointF(size / 2, size / 2), size / 2)
        for pos, k in BELL:
            c = QColor(colour)
            c.setAlphaF(k)
            g.setColorAt(pos, c)
        q.fillRect(QRectF(0, 0, size, size), g)
        q.end()
        _blob_sprites[rgb] = sprite
    p.save()
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    p.translate(x, y)
    if turn:
        p.rotate(turn)
    if stretch != 1.0:
        p.scale(1.0, stretch)
    p.setOpacity(p.opacity() * colour.alphaF())
    p.drawPixmap(QRectF(-r, -r, 2 * r, 2 * r), sprite, QRectF(0, 0, 64, 64))
    p.restore()


def _soft(w: float, h: float, paint, scale: float = 4.0) -> QImage:
    """Soft things (glows, clouds, lanes of dust) painted at a fraction of the size, to be smoothed
    up to full size when drawn: they have no edges to lose, and it costs a sixteenth."""
    img = QImage(max(1, math.ceil(w / scale)), max(1, math.ceil(h / scale)), QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    q = QPainter(img)
    q.setRenderHint(QPainter.RenderHint.Antialiasing)
    q.scale(1 / scale, 1 / scale)
    paint(q)
    q.end()
    return img


def _spread(p: QPainter, img: QImage, rect: QRectF, mode=None) -> None:
    """Draw a :func:`_soft` image smoothed up to ``rect``."""
    p.save()
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    if mode is not None:
        p.setCompositionMode(mode)
    p.drawImage(rect, img)
    p.restore()


_grain_tiles: dict[float, QPixmap] = {}
GRAIN = 0.06               # the strongest grain the tile holds; weaker is drawn more see-through


def _grain(p: QPainter, w: float, h: float, strength: float, dpr: float = 1.0) -> None:
    """A fine grain over a still layer, one screen pixel to a grain. It is too faint to see as
    such, but it breaks up the steps an 8-bit gradient shows across a big dark sky. Each grain
    is a touch of white or of black, see-through by how strong it is."""
    tile = _grain_tiles.get(dpr)
    if tile is None:
        size = 128
        rng = random.Random(7)
        data = bytearray(size * size * 4)
        for i in range(size * size):
            v = rng.random() - rng.random()
            a = min(255, round(abs(v) * GRAIN * 255))
            c = a if v > 0 else 0                       # premultiplied: white at a, or black
            data[4 * i:4 * i + 4] = bytes((c, c, c, a))
        tile = QPixmap.fromImage(QImage(bytes(data), size, size, size * 4, QImage.Format.Format_ARGB32_Premultiplied).copy())
        tile.setDevicePixelRatio(dpr)
        _grain_tiles[dpr] = tile
    p.save()
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
    p.setOpacity(min(1.0, strength / GRAIN))
    p.drawTiledPixmap(QRectF(0, 0, w, h), tile)
    p.restore()


def _vignette(p: QPainter, w: float, h: float, strength: float, colour: str = "#000000",
              centre: tuple[float, float] = (0.5, 0.45)) -> None:
    """Corners a little darker than the middle, the way a lens sees: it draws the eye in."""
    p.save()
    p.translate(w * centre[0], h * centre[1])
    p.scale(w / h, 1.0)
    g = QRadialGradient(QPointF(0, 0), h * 0.78)
    g.setColorAt(0.0, _c(colour, 0.0))
    g.setColorAt(0.55, _c(colour, 0.0))
    g.setColorAt(0.85, _c(colour, strength * 0.55))
    g.setColorAt(1.0, _c(colour, strength))
    p.fillRect(QRectF(-h * 0.5 * 3, -h, h * 3, h * 2), g)
    p.restore()


def _points(p: QPainter, stars, tints: list[QColor]) -> None:
    """Many small stars at once: ``stars`` is (x, y, radius, tint, alpha). They are drawn as round
    points, grouped by size, tint and brightness, a few dozen calls for thousands of them."""
    groups: dict[tuple, list[QPointF]] = {}
    for x, y, r, tint, alpha in stars:
        groups.setdefault((round(r * 5) / 5, tint, round(alpha * 10) / 10), []).append(QPointF(x, y))
    for (r, tint, alpha), pts in groups.items():
        if alpha <= 0 or r <= 0:
            continue
        colour = QColor(tints[tint])
        colour.setAlphaF(min(1.0, alpha))
        p.setPen(QPen(colour, 2 * r, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawPoints(QPolygonF(pts))
    p.setPen(Qt.PenStyle.NoPen)


def _fill_each(p: QPainter, path: QPainterPath, brush) -> None:
    """Fill each shape in ``path`` on its own. For many small overlapping shapes, a forest, this
    is fifty times quicker than filling the path as one, which works out every crossing."""
    p.save()
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(brush)
    for poly in path.toSubpathPolygons():
        p.drawPolygon(poly)
    p.restore()


def _flare(tint: QColor, size: int = 96) -> QPixmap:
    """A bright star as a lens sees it: a hot core in a soft halo, and four thin spikes that fade
    as they go out, with a fainter, shorter pair between them."""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    q = QPainter(pm)
    q.setRenderHint(QPainter.RenderHint.Antialiasing)
    c = size / 2
    halo = QRadialGradient(QPointF(c, c), c * 0.42)
    halo.setColorAt(0.0, _c("#ffffff", 1.0))
    halo.setColorAt(0.12, _c("#ffffff", 0.95))
    halo.setColorAt(0.3, _c(tint.name(), 0.45))
    halo.setColorAt(0.6, _c(tint.name(), 0.12))
    halo.setColorAt(1.0, _c(tint.name(), 0.0))
    q.setPen(Qt.PenStyle.NoPen)
    q.setBrush(halo)
    q.drawEllipse(QPointF(c, c), c * 0.42, c * 0.42)
    q.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
    for angle, length, width, alpha in ((0, 0.98, 0.030, 0.95), (90, 0.98, 0.030, 0.95),
                                        (45, 0.46, 0.022, 0.40), (135, 0.46, 0.022, 0.40)):
        q.save()
        q.translate(c, c)
        q.rotate(angle)
        span = c * length
        g = QLinearGradient(QPointF(-span, 0), QPointF(span, 0))
        g.setColorAt(0.0, _c(tint.name(), 0.0))
        g.setColorAt(0.3, _c(tint.name(), alpha * 0.28))
        g.setColorAt(0.5, _c("#ffffff", alpha))
        g.setColorAt(0.7, _c(tint.name(), alpha * 0.28))
        g.setColorAt(1.0, _c(tint.name(), 0.0))
        q.setBrush(g)
        half = size * width
        q.drawPolygon(QPolygonF([QPointF(-span, 0), QPointF(0, -half), QPointF(span, 0), QPointF(0, half)]))
        q.restore()
    q.end()
    return pm


class Scene:
    """A still layer and the things that move over it."""

    count = 0          # how many moving things at the base size and amount "some"
    fps = FPS          # the frame rate it is shown at, kept up to date by the Backdrop

    def __init__(self, t: dict, amount: float = 1.0, seed: int = 11):
        self.t = t
        self.amount = amount
        self.rng = random.Random(seed)
        self.w = self.h = 0
        self.dpr = 1.0
        self.time = 0.0
        self.static: QPixmap | None = None
        self.items: list = []

    # -- size -----------------------------------------------------------------
    def resize(self, w: int, h: int, dpr: float = 1.0) -> None:
        grew = w > self.w or h > self.h
        self.w, self.h, self.dpr = max(w, 1), max(h, 1), dpr
        self.static = self._render_static()
        area = max(0.4, min(2.6, (self.w * self.h) / BASE_AREA))
        target = int(self.count * self.amount * area)
        while len(self.items) < target:
            self.items.append(self.spawn(anywhere=True))
        del self.items[target:]
        if grew:
            for it in self.items:
                self.keep_inside(it)

    def _render_static(self) -> QPixmap:
        pm = QPixmap(QSize(int(self.w * self.dpr), int(self.h * self.dpr)))
        pm.setDevicePixelRatio(self.dpr)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        g = QLinearGradient(0, 0, 0, self.h)
        g.setColorAt(0.0, _c(self.t["grad_top"]))
        g.setColorAt(1.0, _c(self.t["grad_bottom"]))
        p.fillRect(QRectF(0, 0, self.w, self.h), g)
        self.draw_still(p)
        p.end()
        return pm

    def glow(self, p: QPainter, x: float, y: float, r: float, colour: QColor) -> None:
        g = QRadialGradient(QPointF(x, y), r)
        g.setColorAt(0.0, colour)
        clear = QColor(colour)
        clear.setAlpha(0)
        g.setColorAt(1.0, clear)
        # Clear beyond its radius, so only its square needs filling, not the whole window.
        area = QRectF(x - r, y - r, 2 * r, 2 * r).intersected(QRectF(0, 0, self.w, self.h))
        if not area.isEmpty():
            p.fillRect(area, g)

    # -- to override -------------------------------------------------------------
    def draw_still(self, p: QPainter) -> None:
        pass

    def spawn(self, anywhere: bool = False):
        return None

    def keep_inside(self, it) -> None:
        pass

    def step(self, dt: float) -> None:
        self.time += dt

    def redraw_every(self) -> float:
        """How often a layer that is costly but changes softly (the aurora's curtains, the flames)
        is worked out anew: every frame up to 15 a second, beyond that every other frame, and
        never more than 30 times a second."""
        fps = max(1.0, float(self.fps))
        return 1.0 / min(fps, max(15.0, fps / 2), 30.0)

    def changed(self) -> bool:
        """Whether the last step changed what is seen: a scene that moves slowly says no between steps
        too small to show, and nothing is drawn for them."""
        return True

    def paint_moving(self, p: QPainter) -> None:
        pass

    def paint(self, p: QPainter) -> None:
        if self.static is not None:
            p.drawPixmap(0, 0, self.static)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.paint_moving(p)


class Plain(Scene):
    """Standard front: the flat colour, nothing moving."""


# -- Astral ------------------------------------------------------------------------

class _Star:
    __slots__ = ("x", "y", "r", "base", "speed", "phase", "tint", "drift")


class _Shoot:
    __slots__ = ("x", "y", "vx", "vy", "age", "life")


class Stars(Scene):
    count = 120
    sky = 1.0          # the part of the height stars live in, from the top

    #: Star colours: white, hot blue-white, and the warm ones, cooler stars.
    TINTS = (None, "#b9d0ff", "#ffe6c4", "#ffc9a0")

    def __init__(self, t, amount=1.0, seed=11):
        super().__init__(t, amount, seed)
        self.tints = [QColor(c or t["star"]) for c in self.TINTS]
        self.sprites = [_sprite(_c("#ffffff", 1.0), _c(c.name(), 0.55), 48, 0.10) for c in self.tints]
        self.flares = [_flare(c) for c in self.tints]
        self.shoots: list[_Shoot] = []
        self.next_shoot = self.rng.uniform(2.5, 7.0) / max(amount, 0.3)

    def draw_still(self, p: QPainter) -> None:
        w, h = self.w, self.h
        big = max(w, h)
        self.glow(p, w * 0.82, h * 0.14, big * 0.55, _c(self.t["nebula_a"], 0.20))
        self.glow(p, w * 0.12, h * 0.86, big * 0.55, _c(self.t["nebula_b"], 0.16))
        # A little airglow low down, so the bottom of the sky is not a flat black.
        low = QLinearGradient(0, h * 0.62, 0, h)
        low.setColorAt(0.0, _c("#1d3f7a", 0.0))
        low.setColorAt(1.0, _c("#1d3f7a", 0.22))
        p.fillRect(QRectF(0, h * 0.62, w, h * 0.38), low)
        self.milky_way(p, 1.0)
        _vignette(p, w, h, 0.45)
        _grain(p, w, h, 0.03, self.dpr)

    # -- the Milky Way -------------------------------------------------------------------
    def band(self, u: float) -> tuple[float, float, float]:
        """Where the Milky Way's middle runs at ``u`` across (0 to 1, a little either side), and
        its direction there, in degrees: low on the left, rising to the right."""
        w, h = self.w, self.h
        x = w * u
        y = h * (0.92 - 0.80 * u + 0.07 * math.sin(math.pi * u))
        dy = h * (-0.80 + 0.07 * math.pi * math.cos(math.pi * u))
        return x, y, math.degrees(math.atan2(dy, w))

    def _across(self, u: float, off: float) -> tuple[float, float, float]:
        x, y, turn = self.band(u)
        a = math.radians(turn)
        return x - math.sin(a) * off, y + math.cos(a) * off, turn

    def milky_way(self, p: QPainter, strength: float) -> None:
        """The galaxy seen edge on: a long glow, brightest and warmest round its middle, broken
        by lanes of dark dust, with pink knots of glowing gas, and thick with small stars."""
        w, h = self.w, self.h
        rng = random.Random(29)
        area = max(0.05, min(2.6, w * h / BASE_AREA))          # the stars follow the size, down to a thumbnail

        def lane(u: float) -> float:
            """How far the main lane of dust wanders from the band's middle."""
            return h * (0.006 + 0.010 * math.sin(u * 17 + 1) + 0.006 * math.sin(u * 43))

        def glow(q: QPainter) -> None:
            # The broad glow, then the clouds of stars that give it its grain.
            for big, count, spread, size, alpha in ((True, 50, 0.05, (0.07, 0.15), (0.035, 0.06)),
                                                    (False, 420, 0.04, (0.01, 0.028), (0.02, 0.05))):
                for _ in range(count):
                    u = rng.uniform(-0.12, 1.12)
                    core = math.exp(-((u - 0.34) / 0.16) ** 2)      # the bulge round the galaxy's middle
                    x, y, turn = self._across(u, rng.gauss(0, h * spread * (1 + 0.6 * core)))
                    r = h * rng.uniform(*size) * (1 + 0.8 * core)
                    warm = rng.random() < 0.15 + 0.7 * core
                    colour = rng.choice(("#ffe0c0", "#ffd2b0", "#fff0dc")) if warm else \
                        rng.choice(("#9fb4ff", "#b7a4ff", "#c8d4ff"))
                    _blob(q, x, y, r, _c(colour, rng.uniform(*alpha) * strength * (1 + 0.8 * core)),
                          0.55 if big else rng.uniform(0.5, 1.0), turn)
            for u, off, r, colour, alpha in ((0.22, 0.03, 0.035, "#ff6f9f", 0.16), (0.47, -0.02, 0.028, "#ff7fa8", 0.13),
                                             (0.63, 0.04, 0.022, "#ff8f8f", 0.10), (0.29, -0.06, 0.03, "#7fb0ff", 0.10),
                                             (0.80, -0.03, 0.025, "#ff7fb4", 0.09)):
                x, y, _turn = self._across(u, off * h)
                _blob(q, x, y, h * r, _c(colour, alpha * strength))
                _blob(q, x, y, h * r * 0.35, _c("#ffd6e4", alpha * 0.8 * strength))

        def lanes(q: QPainter) -> None:
            # Many small, faint, stretched patches along a wandering line, and a second lane that
            # splits off through the bright middle, the way the Great Rift does.
            for rift in (False, True):
                for _ in range(90 if rift else 240):
                    u = rng.uniform(0.18, 0.62) if rift else rng.uniform(-0.08, 1.08)
                    core = math.exp(-((u - 0.34) / 0.2) ** 2)
                    off = lane(u) + (h * (0.02 + 0.03 * (u - 0.18)) if rift else 0.0)
                    x, y, turn = self._across(u, off + rng.gauss(0, h * 0.007 * (1 + core)))
                    _blob(q, x, y, h * rng.uniform(0.008, 0.026) * (1 + 0.7 * core),
                          _c("#020309", rng.uniform(0.14, 0.32) * strength), rng.uniform(0.3, 0.6),
                          turn + rng.uniform(-15, 15))

        _spread(p, _soft(w, h, glow, 3.0), QRectF(0, 0, w, h))
        # Stars: a scatter over all the sky, and many more, and smaller, crowding the band.
        stars = []
        for _ in range(int(420 * self.amount * area)):
            stars.append((rng.uniform(0, w), rng.uniform(0, h), 0.3 + 0.55 * rng.random() ** 2,
                           rng.choices((0, 1, 2, 3), (6, 2, 2, 1))[0], 0.15 + 0.5 * rng.random() ** 1.5))
        for _ in range(int(3200 * self.amount * area * strength)):
            u = rng.uniform(-0.1, 1.1)
            core = math.exp(-((u - 0.34) / 0.16) ** 2)
            x, y, _turn = self._across(u, rng.gauss(0, h * 0.04 * (1 + 0.6 * core)))
            stars.append((x, y, 0.3 + 0.4 * rng.random() ** 3, rng.choices((0, 1, 2, 3), (5, 1, 2 + 3 * core, 1))[0],
                          (0.12 + 0.55 * rng.random() ** 2) * (0.6 + 0.4 * strength)))
        _points(p, stars, self.tints)
        # The dust goes over the stars, as it does in the sky: it hides them.
        _spread(p, _soft(w, h, lanes, 3.0), QRectF(0, 0, w, h))

    def spawn(self, anywhere=False):
        s = _Star()
        s.x = self.rng.random() * self.w if anywhere else self.w + 4
        s.y = self.rng.random() * self.h * self.sky
        u = self.rng.random()
        s.r = 0.6 + 1.5 * u * u
        s.base = 0.45 + 0.55 * self.rng.random()
        s.speed = 0.6 + 2.0 * self.rng.random()
        s.phase = self.rng.random() * math.tau
        s.tint = self.rng.choices((0, 1, 2, 3), (14, 5, 3, 2))[0]
        s.drift = 1.0 + 4.5 * u          # the big ones are near, and pass a little quicker
        return s

    def keep_inside(self, s) -> None:
        s.x %= self.w
        s.y %= self.h * self.sky

    def step(self, dt: float) -> None:
        super().step(dt)
        for s in self.items:
            s.x -= s.drift * dt
            if s.x < -4:
                s.x += self.w + 8
                s.y = self.rng.random() * self.h * self.sky
        self.next_shoot -= dt
        if self.next_shoot <= 0:
            self.next_shoot = self.rng.uniform(4.0, 11.0) / max(self.amount, 0.3)
            sh = _Shoot()
            sh.x = self.rng.uniform(self.w * 0.25, self.w * 1.05)
            sh.y = self.rng.uniform(-10, self.h * 0.45)
            angle = math.radians(self.rng.uniform(150, 165))
            speed = self.rng.uniform(620, 900)
            sh.vx, sh.vy = math.cos(angle) * speed, math.sin(angle) * speed      # left and down
            sh.age, sh.life = 0.0, self.rng.uniform(0.7, 1.1)
            self.shoots.append(sh)
        for sh in self.shoots:
            sh.age += dt
            sh.x += sh.vx * dt
            sh.y += sh.vy * dt
        self.shoots = [sh for sh in self.shoots if sh.age < sh.life]

    def paint_moving(self, p: QPainter) -> None:
        t = self.time
        for s in self.items:
            a = s.base * (0.55 + 0.45 * math.sin(s.phase + t * s.speed))
            p.setOpacity(a)
            if s.r > 1.55:
                # A bright one: spikes, which grow a little as it flares.
                size = s.r * 7.0 * (0.8 + 0.2 * a)
                p.drawPixmap(QRectF(s.x - size, s.y - size, size * 2, size * 2), self.flares[s.tint],
                             QRectF(0, 0, 96, 96))
            else:
                size = s.r * 3.4
                p.drawPixmap(QRectF(s.x - size, s.y - size, size * 2, size * 2), self.sprites[s.tint],
                             QRectF(0, 0, 48, 48))
        p.setOpacity(1.0)
        for sh in self.shoots:
            k = sh.age / sh.life
            fade = math.sin(math.pi * min(1.0, k)) ** 0.8
            speed = math.hypot(sh.vx, sh.vy)
            length = min(sh.age * speed, 190.0)
            tx, ty = sh.x - sh.vx / speed * length, sh.y - sh.vy / speed * length
            head, tail = QPointF(sh.x, sh.y), QPointF(tx, ty)
            # A wide faint glow, then the bright streak: white at the head, green-blue behind it,
            # the colour a meteor burns.
            g = QLinearGradient(head, tail)
            g.setColorAt(0.0, _c("#c8ffe8", 0.30 * fade))
            g.setColorAt(1.0, _c("#8fb8ff", 0.0))
            p.setPen(QPen(QBrush(g), 5.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(head, tail)
            g = QLinearGradient(head, tail)
            g.setColorAt(0.0, _c("#ffffff", 0.95 * fade))
            g.setColorAt(0.15, _c("#d8fff0", 0.7 * fade))
            g.setColorAt(0.45, _c("#9fc4ff", 0.3 * fade))
            g.setColorAt(1.0, _c("#9fc4ff", 0.0))
            p.setPen(QPen(QBrush(g), 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(head, tail)
            p.setOpacity(fade)
            p.drawPixmap(QRectF(sh.x - 9, sh.y - 9, 18, 18), self.sprites[0], QRectF(0, 0, 48, 48))
            p.setOpacity(1.0)


# -- Sakura ---------------------------------------------------------------------------

def petal_path() -> QPainterPath:
    """A cherry petal, point down, with the notch at its outer edge; unit size."""
    p = QPainterPath(QPointF(0, 1.0))
    p.cubicTo(QPointF(0.95, 0.55), QPointF(0.85, -0.85), QPointF(0.22, -0.98))
    p.lineTo(QPointF(0.0, -0.74))
    p.lineTo(QPointF(-0.22, -0.98))
    p.cubicTo(QPointF(-0.85, -0.85), QPointF(-0.95, 0.55), QPointF(0, 1.0))
    p.closeSubpath()
    return p


def _bezier(p0: QPointF, p1: QPointF, p2: QPointF, p3: QPointF, n: int = 24) -> list[QPointF]:
    pts = []
    for i in range(n + 1):
        t = i / n
        m = 1 - t
        pts.append(p0 * (m * m * m) + p1 * (3 * m * m * t) + p2 * (3 * m * t * t) + p3 * (t * t * t))
    return pts


def _taper(pts: list[QPointF], w0: float, w1: float, shift: float = 0.0) -> QPainterPath:
    """A limb along ``pts``, ``w0`` thick at its start narrowing to ``w1``, its end rounded.
    ``shift`` moves it sideways by that part of its width (to the left of the way it runs)."""
    n = len(pts)
    left, right = [], []
    for i, pt in enumerate(pts):
        a, b = pts[max(i - 1, 0)], pts[min(i + 1, n - 1)]
        dx, dy = b.x() - a.x(), b.y() - a.y()
        length = math.hypot(dx, dy) or 1.0
        nx, ny = dy / length, -dx / length
        half = (w0 + (w1 - w0) * i / (n - 1)) / 2
        cx, cy = pt.x() + nx * half * 2 * shift, pt.y() + ny * half * 2 * shift
        left.append(QPointF(cx + nx * half, cy + ny * half))
        right.append(QPointF(cx - nx * half, cy - ny * half))
    path = QPainterPath()
    path.setFillRule(Qt.FillRule.WindingFill)
    path.addPolygon(QPolygonF(left + right[::-1]))
    path.closeSubpath()
    end = (left[-1] + right[-1]) / 2
    path.addEllipse(end, w1 / 2, w1 / 2)
    return path


def _blur(pm: QPixmap, factor: float) -> QPixmap:
    """A soft copy: shrunk and smoothed back up."""
    w, h = pm.width(), pm.height()
    small = pm.scaled(max(1, round(w / factor)), max(1, round(h / factor)), Qt.AspectRatioMode.IgnoreAspectRatio,
                      Qt.TransformationMode.SmoothTransformation)
    return small.scaled(w, h, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)


class _Petal:
    __slots__ = ("x", "y", "s", "d", "vy", "amp", "freq", "phase", "angle", "spin", "flip", "flips", "kind",
                 "op", "soft")


class Petals(Scene):
    """Cherry blossom. Light comes from the top left, in a warm glow and faint rays, over hills
    far off in the mist, one still white with snow, and rows of trees in flower nearer; a few
    blossoms float out of focus in front of it all. A branch in flower reaches in from the top
    right, its bark with the cherry's pale bands, its flowers in clusters with buds and a few
    young leaves. Petals drift down on a gusting wind, turning over as they fall and showing
    their paler backs, and now and then one passes close to the eye, big and blurred."""

    count = 38
    SPRITE = 64
    SPAN = (1.25, 1.136)             # the half size of a petal sprite, in petal units

    def __init__(self, t, amount=1.0, seed=11):
        super().__init__(t, amount, seed)
        self.shape = petal_path()
        # For each pink: the petal's face, its paler back, and a blurred one for those near the eye.
        self.sprites = []
        for key in ("petal_a", "petal_b", "petal_c"):
            front = self._petal_sprite(QColor(t[key]))
            self.sprites.append((front, self._petal_sprite(QColor(t[key]), back=True), _blur(front, 5.0)))

    def _petal_sprite(self, colour: QColor, back: bool = False) -> QPixmap:
        s = self.SPRITE
        pm = QPixmap(s, s)
        pm.fill(Qt.GlobalColor.transparent)
        q = QPainter(pm)
        q.setRenderHint(QPainter.RenderHint.Antialiasing)
        q.translate(s / 2, s / 2)
        q.scale(s / 2 / self.SPAN[0], s / 2 / self.SPAN[1])
        # Palest where it grew from the flower, deepening to its notched tip.
        g = QRadialGradient(QPointF(0, 1.0), 2.0)
        if back:
            g.setColorAt(0.0, _c("#fffafc"))
            g.setColorAt(0.5, colour.lighter(122))
            g.setColorAt(1.0, colour.lighter(108))
        else:
            g.setColorAt(0.0, _c("#fff6f9"))
            g.setColorAt(0.3, colour.lighter(116))
            g.setColorAt(0.75, colour)
            g.setColorAt(1.0, colour.darker(110))
        q.setPen(QPen(_c(colour.darker(135).name(), 0.40), 0.035))
        q.setBrush(g)
        q.drawPath(self.shape)
        q.setBrush(Qt.BrushStyle.NoBrush)
        q.setPen(QPen(_c(colour.darker(120).name(), 0.22), 0.03))
        for lean in (-0.25, 0.0, 0.25):                                   # its fine veins
            vein = QPainterPath(QPointF(0, 0.9))
            vein.quadTo(QPointF(lean * 0.6, 0.1), QPointF(lean, -0.6))
            q.drawPath(vein)
        if not back:
            sheen = QRadialGradient(QPointF(-0.3, -0.2), 0.6)
            sheen.setColorAt(0.0, _c("#ffffff", 0.35))
            sheen.setColorAt(1.0, _c("#ffffff", 0.0))
            q.setPen(Qt.PenStyle.NoPen)
            q.setBrush(sheen)
            q.drawPath(self.shape)
        q.end()
        return pm

    def wind(self) -> float:
        return 14 + 12 * math.sin(self.time * 0.11) + 6 * math.sin(self.time * 0.37 + 1.3)

    # -- the still picture ------------------------------------------------------------------
    def draw_still(self, p: QPainter) -> None:
        w, h = self.w, self.h
        big = max(w, h)
        self.glow(p, w * 0.96, h * 0.98, big * 0.55, _c("#ffe3ec", 0.70))
        self.glow(p, w * 0.04, -h * 0.06, big * 0.80, _c("#fff3e4", 0.85))     # the light
        self._rays(p)
        self._far(p)
        self._bokeh(p)
        self._branch(p)
        _grain(p, w, h, 0.025, self.dpr)

    def _rays(self, p: QPainter) -> None:
        """Faint shafts of light fanning down from the top left."""
        w, h = self.w, self.h
        ox, oy = -w * 0.02, -h * 0.10
        reach = math.hypot(w, h) * 0.95

        def paint(q: QPainter) -> None:
            q.setPen(Qt.PenStyle.NoPen)
            for angle, spread, alpha in ((18, 3.5, 0.16), (27, 2.0, 0.12), (35, 5.0, 0.14), (46, 2.5, 0.10),
                                         (55, 4.0, 0.12), (66, 2.0, 0.08)):
                g = QRadialGradient(QPointF(ox, oy), reach)
                g.setColorAt(0.0, _c("#fffaf0", alpha))
                g.setColorAt(0.55, _c("#fffaf0", alpha * 0.45))
                g.setColorAt(1.0, _c("#fffaf0", 0.0))
                q.setBrush(g)
                a0, a1 = math.radians(angle - spread / 2), math.radians(angle + spread / 2)
                q.drawPolygon(QPolygonF([QPointF(ox, oy),
                                         QPointF(ox + math.cos(a0) * reach, oy + math.sin(a0) * reach),
                                         QPointF(ox + math.cos(a1) * reach, oy + math.sin(a1) * reach)]))

        _spread(p, _soft(w, h, paint, 6.0), QRectF(0, 0, w, h))

    def _far(self, p: QPainter) -> None:
        """Hills far off, pale with the distance, then two rows of trees in flower, the mist
        thickening between them and at the foot of the window."""
        w, h = self.w, self.h
        rng = random.Random(52)
        mist = "#fff6f9"
        for base, colour, alpha, seed, peak in ((0.70, "#d7b0ca", 0.42, 1.0, True), (0.77, "#e8bdd0", 0.48, 2.3, False)):
            path = QPainterPath(QPointF(0, h))
            top = h
            steps = 90
            for i in range(steps + 1):
                u = i / steps
                y = h * (base - 0.035 * math.sin(u * math.pi * 2.2 + seed) - 0.018 * math.sin(u * math.pi * 5.3 + seed * 2)
                         - 0.006 * math.sin(u * 29 + seed))
                if peak:
                    y -= h * 0.17 * max(0.0, 1 - abs(u - 0.66) / 0.15) ** 1.25
                top = min(top, y)
                path.lineTo(QPointF(u * w, y))
            path.lineTo(QPointF(w, h))
            path.closeSubpath()
            g = QLinearGradient(0, top, 0, h * (base + 0.08))
            g.setColorAt(0.0, _c(colour, alpha))
            g.setColorAt(1.0, _c(mist, alpha * 0.4))
            p.fillPath(path, g)
            if peak:
                # Snow on the peak, lit from the left.
                p.save()
                p.setClipPath(path)
                snow = QLinearGradient(0, top, 0, top + h * 0.075)
                snow.setColorAt(0.0, _c("#ffffff", 0.85))
                snow.setColorAt(0.6, _c("#ffffff", 0.55))
                snow.setColorAt(1.0, _c("#ffffff", 0.0))
                p.fillRect(QRectF(0, top, w, h * 0.08), snow)
                # The far side of the peak, away from the light, a little darker.
                shade = QLinearGradient(w * 0.64, 0, w * 0.84, 0)
                shade.setColorAt(0.0, _c("#b98aa8", 0.0))
                shade.setColorAt(0.4, _c("#b98aa8", 0.20))
                shade.setColorAt(1.0, _c("#b98aa8", 0.0))
                p.fillRect(QRectF(w * 0.64, top, w * 0.20, h * 0.2), shade)
                p.restore()
        haze = QLinearGradient(0, h * 0.66, 0, h * 0.84)
        haze.setColorAt(0.0, _c(mist, 0.0))
        haze.setColorAt(1.0, _c(mist, 0.55))
        p.fillRect(QRectF(0, h * 0.66, w, h * 0.34), haze)
        # Groves of trees in flower, soft as clouds in the mist: a far row, paler, then a nearer one.
        for row, (level, size, colours, alpha) in enumerate(((0.835, (0.018, 0.032), ("#f5c6d7", "#fbdbe6"), 0.55),
                                                            (0.93, (0.032, 0.060), ("#f2adc6", "#f8c9d9", "#fde6ee"), 0.62))):
            y0 = h * level

            def grove(q: QPainter, y0=y0, size=size, colours=colours, alpha=alpha) -> None:
                x = -h * size[1]
                while x < w + h * size[1]:
                    if rng.random() < 0.18:                          # a gap between the groves
                        x += h * size[1] * rng.uniform(1.0, 2.5)
                        continue
                    r = h * rng.uniform(*size)
                    colour = QColor(rng.choice(colours))
                    # One tree: a lumpy crown, lit from above, dappled with blossom, on a dark stem.
                    q.setPen(QPen(_c("#7d4a5e", 0.8), max(1.0, r * 0.12)))
                    q.drawLine(QPointF(x, y0 - r * 0.4), QPointF(x + r * 0.06, y0 + r * 0.5))
                    q.setPen(Qt.PenStyle.NoPen)
                    crown = QPainterPath()
                    crown.setFillRule(Qt.FillRule.WindingFill)
                    for _ in range(22):
                        crown.addEllipse(QPointF(x + rng.gauss(0, r * 0.48), y0 - r * 0.55 + rng.gauss(0, r * 0.24)),
                                         r * rng.uniform(0.22, 0.38), r * rng.uniform(0.2, 0.34))
                    g = QLinearGradient(0, y0 - r * 1.15, 0, y0 + r * 0.05)
                    g.setColorAt(0.0, _c("#fff3f7"))
                    g.setColorAt(0.45, colour)
                    g.setColorAt(1.0, colour.darker(112))
                    q.fillPath(crown, g)
                    q.save()
                    q.setClipPath(crown)
                    for _ in range(26):
                        light = rng.random() < 0.55
                        q.setBrush(_c("#fff7fa" if light else colour.darker(116).name(), 0.55))
                        q.drawEllipse(QPointF(x + rng.gauss(0, r * 0.5), y0 - r * 0.55 + rng.gauss(0, r * 0.3)),
                                      r * rng.uniform(0.05, 0.12), r * rng.uniform(0.04, 0.10))
                    q.restore()
                    x += r * rng.uniform(0.9, 1.5)

            # Drawn solid, then laid on see-through as one, so the puffs do not show through each other.
            p.save()
            p.setOpacity(alpha)
            _spread(p, _soft(w, h, grove, 1.5), QRectF(0, 0, w, h))
            p.restore()
            fog = QLinearGradient(0, y0 - h * size[1] * 0.6, 0, y0 + h * size[1] * 1.2)
            fog.setColorAt(0.0, _c(mist, 0.0))
            fog.setColorAt(1.0, _c(mist, 0.5 if row == 0 else 0.75))
            p.fillRect(QRectF(0, y0 - h * size[1] * 0.6, w, h - y0 + h * size[1] * 0.6), fog)

    def _bokeh(self, p: QPainter) -> None:
        """Blossoms near the eye, out of focus: soft discs with a brighter rim."""
        w, h = self.w, self.h
        rng = random.Random(41)
        area = max(0.4, min(2.6, w * h / BASE_AREA))
        p.setPen(Qt.PenStyle.NoPen)
        for _ in range(int(26 * area)):
            if rng.random() < 0.6:
                x, y = rng.uniform(0, w * 0.55), rng.uniform(0, h * 0.7)
            else:
                x, y = rng.uniform(w * 0.4, w), rng.uniform(h * 0.45, h)
            r = h * rng.uniform(0.010, 0.045)
            colour = rng.choice(("#ffffff", "#ffffff", self.t["petal_b"], self.t["petal_a"]))
            alpha = rng.uniform(0.08, 0.22)
            g = QRadialGradient(QPointF(x, y), r)
            g.setColorAt(0.0, _c(colour, alpha * 0.55))
            g.setColorAt(0.78, _c(colour, alpha * 0.7))
            g.setColorAt(0.92, _c(colour, alpha))
            g.setColorAt(1.0, _c(colour, 0.0))
            p.setBrush(g)
            p.drawEllipse(QPointF(x, y), r, r)

    def _branch(self, p: QPainter) -> None:
        """The branch in flower, drawn on its own layer so it can cast a soft shadow."""
        w, h = self.w, self.h
        s = min(w * 0.46, 620.0)
        ox, oy = w - s, -s * 0.06
        top, bottom = 0.0, min(h, oy + s * 0.70)
        layer = _layer(w, bottom, self.dpr)
        q = QPainter(layer)
        q.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._draw_branch(q, s, ox, oy)
        q.end()
        # The shadow: the branch, blurred, in a deep pink, a little down and to the right.
        shadow = layer.toImage().scaled(max(1, round(w / 10)), max(1, round(bottom / 10)),
                                        Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
        tint = QPainter(shadow)
        tint.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        tint.fillRect(shadow.rect(), _c("#8a2f58", 0.20))
        tint.end()
        _spread(p, shadow, QRectF(s * 0.012, top + s * 0.024, w, bottom))
        p.drawPixmap(QPointF(0, top), layer)

    def _draw_branch(self, p: QPainter, s: float, ox: float, oy: float) -> None:
        t = self.t

        def pt(u, v):
            return QPointF(ox + u * s, oy + v * s)

        limbs = [
            (0.050, 0.020, [(1.06, 0.24), (0.88, 0.18), (0.74, 0.36), (0.50, 0.29)]),
            (0.021, 0.007, [(0.50, 0.29), (0.41, 0.26), (0.33, 0.34), (0.19, 0.30)]),
            (0.024, 0.007, [(0.84, 0.225), (0.79, 0.12), (0.72, 0.08), (0.61, 0.06)]),
            (0.019, 0.006, [(0.67, 0.335), (0.65, 0.45), (0.58, 0.50), (0.49, 0.56)]),
            (0.012, 0.005, [(0.41, 0.265), (0.38, 0.18), (0.34, 0.15), (0.27, 0.13)]),
            (0.020, 0.006, [(0.965, 0.235), (0.985, 0.38), (0.94, 0.46), (0.885, 0.53)]),
        ]
        bark = QColor(t["branch"])
        marks = random.Random(8)
        curves = []
        for w0, w1, ctrl in limbs:
            pts = _bezier(*(pt(u, v) for u, v in ctrl))
            curves.append(pts)
            w0, w1 = w0 * s, w1 * s
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(bark.darker(135))
            p.drawPath(_taper(pts, w0, w1))
            p.setBrush(bark)
            p.drawPath(_taper(pts, w0 * 0.78, w1 * 0.78, 0.10))
            p.setBrush(_c(bark.lighter(150).name(), 0.55))
            p.drawPath(_taper(pts, w0 * 0.22, w1 * 0.22, 1.25))              # the lit top edge
            # The pale bands across cherry bark.
            p.setPen(QPen(_c(bark.lighter(185).name(), 0.40), max(0.8, w0 * 0.06), Qt.PenStyle.SolidLine,
                          Qt.PenCapStyle.RoundCap))
            n = len(pts)
            for i in range(1, n - 2):
                width = w0 + (w1 - w0) * i / (n - 1)
                if width < 6:
                    break
                if marks.random() < 0.45:
                    continue
                a, b = pts[i - 1], pts[i + 1]
                dx, dy = b.x() - a.x(), b.y() - a.y()
                length = math.hypot(dx, dy) or 1.0
                nx, ny = dy / length, -dx / length
                c = pts[i]
                k = marks.uniform(-0.25, 0.3)
                half = width * marks.uniform(0.12, 0.25)
                p.drawLine(QPointF(c.x() + nx * width * k + nx * half, c.y() + ny * width * k + ny * half),
                           QPointF(c.x() + nx * width * k - nx * half, c.y() + ny * width * k - ny * half))
        rng = random.Random(3)
        # Clusters along the limbs: (limb, how far along, flowers in it).
        spots = [(0, 0.12, 3), (0, 0.32, 4), (0, 0.55, 3), (0, 0.80, 3), (0, 0.97, 2), (1, 0.30, 3), (1, 0.62, 3),
                 (1, 1.0, 3), (2, 0.45, 3), (2, 0.78, 2), (2, 1.0, 3), (3, 0.40, 2), (3, 1.0, 3), (4, 0.55, 2),
                 (4, 1.0, 2), (5, 0.55, 2), (5, 1.0, 3)]
        pinks = [QColor(t["petal_a"]), QColor(t["petal_b"]), QColor(t["petal_a"]).lighter(106)]
        for limb, along, n in spots:
            pts = curves[limb]
            c = pts[min(len(pts) - 1, round(along * (len(pts) - 1)))]
            # A leaf or two first, so the flowers sit over them.
            if rng.random() < 0.55:
                self._leaf(p, c + QPointF(rng.uniform(-1, 1) * s * 0.02, rng.uniform(-1, 1) * s * 0.02),
                           s * rng.uniform(0.035, 0.05), rng.uniform(0, 360))
            flowers = []
            for _ in range(n):
                off = QPointF(rng.gauss(0, s * 0.022), rng.gauss(0, s * 0.018))
                flowers.append((c + off, s * rng.uniform(0.029, 0.045), rng.uniform(0, 72), rng.uniform(0.62, 1.0),
                                rng.uniform(-40, 40), rng.choice(pinks)))
            flowers.sort(key=lambda f: f[0].y())
            for at, size, turn, tilt, lean, pink in flowers:
                self._flower(p, at, size, turn, tilt, lean, pink)
            if rng.random() < 0.7:
                self._bud(p, c + QPointF(rng.uniform(-1, 1) * s * 0.04, rng.uniform(0.2, 1) * s * 0.03),
                          s * rng.uniform(0.010, 0.014), rng.uniform(-60, 60))

    def _flower(self, p: QPainter, c: QPointF, size: float, turn: float, tilt: float, lean: float,
                colour: QColor) -> None:
        """Five notched petals, palest at the heart, a deep pink eye, and a crown of stamens.
        ``tilt`` squashes it, seen at an angle, and ``lean`` is the way it faces."""
        p.save()
        p.translate(c)
        p.rotate(lean)
        p.scale(1.0, tilt)
        p.rotate(turn)
        g = QRadialGradient(QPointF(0, 0), size * 1.08)
        g.setColorAt(0.0, _c("#ffffff"))
        g.setColorAt(0.28, _c("#fff3f7"))
        g.setColorAt(0.7, colour.lighter(108))
        g.setColorAt(1.0, colour)
        p.setPen(QPen(_c(colour.darker(140).name(), 0.35), max(0.5, size * 0.035)))
        p.setBrush(g)
        for k in range(5):
            shape = QTransform().rotate(k * 72).translate(0, -size * 0.52).scale(size * 0.46, size * 0.53).map(self.shape)
            p.drawPath(shape)
        eye = QRadialGradient(QPointF(0, 0), size * 0.34)
        eye.setColorAt(0.0, _c("#d24f7c", 0.75))
        eye.setColorAt(1.0, _c("#d24f7c", 0.0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(eye)
        p.drawEllipse(QPointF(0, 0), size * 0.34, size * 0.34)
        p.setPen(QPen(_c("#f3c9d6", 0.9), max(0.5, size * 0.022)))
        tips = []
        for i in range(13):
            a = math.tau * i / 13 + 0.2
            r = size * (0.26 + 0.12 * ((i * 7) % 5) / 4)
            tip = QPointF(math.cos(a) * r, math.sin(a) * r)
            p.drawLine(QPointF(math.cos(a) * size * 0.07, math.sin(a) * size * 0.07), tip)
            tips.append(tip)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_c("#f2b75f", 0.95))
        for tip in tips:
            p.drawEllipse(tip, size * 0.035, size * 0.035)
        p.restore()

    def _bud(self, p: QPainter, c: QPointF, size: float, turn: float) -> None:
        p.save()
        p.translate(c)
        p.rotate(turn)
        bud = QPainterPath(QPointF(0, -size * 1.6))
        bud.cubicTo(QPointF(size * 1.1, -size * 0.9), QPointF(size * 0.9, size * 0.5), QPointF(0, size * 0.6))
        bud.cubicTo(QPointF(-size * 0.9, size * 0.5), QPointF(-size * 1.1, -size * 0.9), QPointF(0, -size * 1.6))
        g = QLinearGradient(0, -size * 1.6, 0, size * 0.6)
        g.setColorAt(0.0, _c("#e0507f"))
        g.setColorAt(1.0, _c(self.t["petal_a"]))
        p.setPen(QPen(_c("#a8385f", 0.4), max(0.5, size * 0.08)))
        p.setBrush(g)
        p.drawPath(bud)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_c("#7e8a4a", 0.9))                                         # its green cup
        p.drawChord(QRectF(-size * 0.8, size * 0.1, size * 1.6, size * 1.2), 0, -180 * 16)
        p.setPen(QPen(_c("#7e6a4a", 0.8), max(0.6, size * 0.15)))
        p.drawLine(QPointF(0, size * 0.7), QPointF(0, size * 1.8))
        p.restore()

    def _leaf(self, p: QPainter, c: QPointF, size: float, turn: float) -> None:
        """A young leaf, still bronze at its edge."""
        p.save()
        p.translate(c)
        p.rotate(turn)
        leaf = QPainterPath(QPointF(0, 0))
        leaf.cubicTo(QPointF(size * 0.35, -size * 0.28), QPointF(size * 0.8, -size * 0.22), QPointF(size, 0))
        leaf.cubicTo(QPointF(size * 0.8, size * 0.22), QPointF(size * 0.35, size * 0.28), QPointF(0, 0))
        g = QLinearGradient(0, -size * 0.25, 0, size * 0.25)
        g.setColorAt(0.0, _c("#a9b86a"))
        g.setColorAt(1.0, _c("#6f7d3c"))
        p.setPen(QPen(_c("#9a6a3c", 0.55), max(0.5, size * 0.03)))
        p.setBrush(g)
        p.drawPath(leaf)
        p.setPen(QPen(_c("#e8f0c0", 0.45), max(0.5, size * 0.025)))
        p.drawLine(QPointF(size * 0.05, 0), QPointF(size * 0.9, 0))
        p.restore()

    # -- the petals ---------------------------------------------------------------------------
    def spawn(self, anywhere=False):
        r = self.rng
        pe = _Petal()
        pe.d = r.random()
        pe.soft = pe.d > 0.94                          # one in sixteen passes close to the eye
        pe.s = r.uniform(15, 24) if pe.soft else 5.5 + 7.5 * pe.d
        pe.x = r.uniform(-0.25 * self.w, self.w)
        pe.y = r.uniform(-20, self.h) if anywhere else r.uniform(-60, -15) - (pe.s if pe.soft else 0)
        pe.vy = r.uniform(60, 85) if pe.soft else 18 + 30 * pe.d + r.uniform(0, 8)
        pe.amp = 12 + 30 * r.random()
        pe.freq = 0.35 + 0.55 * r.random()
        pe.phase = r.random() * math.tau
        pe.angle = r.uniform(0, 360)
        pe.spin = r.uniform(-75, 75)
        pe.flip = r.random() * math.tau
        pe.flips = 1.0 + 1.6 * r.random()
        pe.kind = r.randrange(3)
        pe.op = 0.5 if pe.soft else 0.55 + 0.42 * pe.d
        return pe

    def keep_inside(self, pe) -> None:
        if pe.x > self.w + 30:
            pe.x = self.rng.uniform(-0.25 * self.w, self.w)

    def step(self, dt: float) -> None:
        super().step(dt)
        wind = self.wind()
        for pe in self.items:
            pe.y += pe.vy * dt
            pe.x += wind * (0.45 + 0.8 * pe.d) * dt
            pe.angle += pe.spin * dt
            pe.flip += pe.flips * dt
            if pe.y > self.h + 24 + pe.s or pe.x > self.w + 40 + pe.s:
                fresh = self.spawn()
                for name in _Petal.__slots__:
                    setattr(pe, name, getattr(fresh, name))

    def paint_moving(self, p: QPainter) -> None:
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        t = self.time
        sw, sh = self.SPAN
        source = QRectF(0, 0, self.SPRITE, self.SPRITE)
        target = QRectF(-sw, -sh, 2 * sw, 2 * sh)
        for pe in self.items:
            x = pe.x + pe.amp * math.sin(pe.phase + t * pe.freq)
            face = math.cos(pe.flip)
            front, back, soft = self.sprites[pe.kind]
            p.save()
            p.translate(x, pe.y)
            p.rotate(pe.angle)
            # Tumbling: seen edge on, a petal is a sliver; face on, all of it; turned over, its back.
            p.scale(pe.s * (0.22 + 0.78 * abs(face)), pe.s)
            p.setOpacity(pe.op)
            p.drawPixmap(target, soft if pe.soft else (front if face >= 0 else back), source)
            p.restore()
        p.setOpacity(1.0)


# -- Frost ------------------------------------------------------------------------------

def _ridge_line(rng, x0: float, x1: float, y: float, rough: float, depth: int = 7) -> list[QPointF]:
    """A mountain skyline by midpoint displacement: each halving of the steps halves how far
    the middle may be pushed up or down, which is what gives ridges their jagged look."""
    ys = [y + rng.uniform(-rough, rough), y + rng.uniform(-rough, rough)]
    amp = rough
    for _ in range(depth):
        nxt = []
        for a, b in zip(ys, ys[1:]):
            nxt += [a, (a + b) / 2 + rng.uniform(-amp, amp)]
        nxt.append(ys[-1])
        ys = nxt
        amp *= 0.55
    n = len(ys) - 1
    return [QPointF(x0 + (x1 - x0) * i / n, v) for i, v in enumerate(ys)]


def _snowy_spruce(p: QPainter, x: float, base: float, tall: float, green: str, snow: str) -> None:
    """A spruce in winter: tiers of dark boughs, each carrying a cap of snow, heaviest on top."""
    tiers = 7
    wide = tall * 0.42
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(_c(QColor(green).darker(130).name()))
    p.drawRect(QRectF(x - tall * 0.018, base - tall * 0.12, tall * 0.036, tall * 0.14))
    for k in range(tiers):
        top = base - tall + tall * 0.13 * k * 0.95
        low = top + tall * (0.20 + 0.03 * k)
        half = wide * 0.5 * (0.22 + 0.78 * (k + 1) / tiers)
        bough = QPainterPath(QPointF(x, top))
        bough.cubicTo(QPointF(x + half * 0.35, top + (low - top) * 0.45), QPointF(x + half * 0.8, low - (low - top) * 0.2),
                      QPointF(x + half, low))
        bough.quadTo(QPointF(x + half * 0.5, low - (low - top) * 0.12), QPointF(x, low - (low - top) * 0.05))
        bough.quadTo(QPointF(x - half * 0.5, low - (low - top) * 0.12), QPointF(x - half, low))
        bough.cubicTo(QPointF(x - half * 0.8, low - (low - top) * 0.2), QPointF(x - half * 0.35, top + (low - top) * 0.45),
                      QPointF(x, top))
        g = QLinearGradient(x - half, 0, x + half, 0)
        g.setColorAt(0.0, _c(QColor(green).lighter(115).name()))
        g.setColorAt(1.0, _c(QColor(green).darker(120).name()))
        p.setBrush(g)
        p.drawPath(bough)
        # The snow on it: the upper part of the tier, its lower edge in soft scallops.
        cap = QPainterPath(QPointF(x, top - tall * 0.006))
        cap.cubicTo(QPointF(x + half * 0.35, top + (low - top) * 0.40), QPointF(x + half * 0.75, low - (low - top) * 0.28),
                    QPointF(x + half * 0.95, low - (low - top) * 0.10))
        steps = 4
        for i in range(steps, -steps - 1, -1):
            u = i / steps
            dip = (low - top) * (0.30 + 0.12 * (1 - abs(u)))
            cap.quadTo(QPointF(x + half * 0.95 * (u + 0.5 / steps), low - dip + (low - top) * 0.10),
                       QPointF(x + half * 0.95 * u, low - dip - (low - top) * 0.04 + (low - top) * 0.2 * abs(u) ** 2))
        cap.cubicTo(QPointF(x - half * 0.75, low - (low - top) * 0.28), QPointF(x - half * 0.35, top + (low - top) * 0.40),
                    QPointF(x, top - tall * 0.006))
        sg = QLinearGradient(x - half, top, x + half, low)
        sg.setColorAt(0.0, _c("#ffffff"))
        sg.setColorAt(0.6, _c(snow))
        sg.setColorAt(1.0, _c("#c9daf0"))
        p.setBrush(sg)
        p.drawPath(cap)


class _Flake:
    __slots__ = ("x", "y", "d", "r", "vy", "amp", "freq", "phase", "op", "kind", "turn", "spin")


class Snow(Scene):
    """A winter valley. A low sun glows behind a far range of snowy peaks, lit on the side that
    faces it; nearer, hills dark with frosted spruce fade into the mist; snow lies deep in
    front, and a tall spruce heavy with snow stands at either side. Snow falls in three depths:
    fine far flakes, soft nearer ones with a few crystals turning among them, and now and
    then a big blurred flake close to the eye."""

    count = 95

    def __init__(self, t, amount=1.0, seed=11):
        super().__init__(t, amount, seed)
        self.flake = self._disc(48, 0.30)
        self.near = _blur(self._disc(48, 0.0), 3.0)
        self.crystal = self._crystal(64)

    @staticmethod
    def _disc(size: int, shadow: float) -> QPixmap:
        """A snowflake at a distance: a soft white disc, with a faint blue-grey edge so it still
        shows against the white of the snow below."""
        pm = QPixmap(size, size)
        pm.fill(Qt.GlobalColor.transparent)
        q = QPainter(pm)
        q.setRenderHint(QPainter.RenderHint.Antialiasing)
        g = QRadialGradient(QPointF(size / 2, size / 2), size / 2)
        g.setColorAt(0.0, _c("#ffffff", 1.0))
        g.setColorAt(0.42, _c("#ffffff", 0.95))
        g.setColorAt(0.62, _c("#e8f1fb", 0.55))
        g.setColorAt(0.82, _c("#8aa9cf", shadow))
        g.setColorAt(1.0, _c("#8aa9cf", 0.0))
        q.setPen(Qt.PenStyle.NoPen)
        q.setBrush(g)
        q.drawEllipse(QRectF(0, 0, size, size))
        q.end()
        return pm

    @staticmethod
    def _crystal(size: int) -> QPixmap:
        """A snow crystal: six arms, each with side branches, white edged in blue."""
        pm = QPixmap(size, size)
        pm.fill(Qt.GlobalColor.transparent)
        q = QPainter(pm)
        q.setRenderHint(QPainter.RenderHint.Antialiasing)
        q.translate(size / 2, size / 2)
        arm = size * 0.44
        lines = []
        for k in range(6):
            a = math.radians(k * 60)
            ca, sa = math.cos(a), math.sin(a)
            lines.append((QPointF(0, 0), QPointF(ca * arm, sa * arm)))
            for at, length in ((0.38, 0.30), (0.62, 0.22), (0.82, 0.13)):
                base = QPointF(ca * arm * at, sa * arm * at)
                for side in (-1, 1):
                    b = a + side * math.radians(60)
                    lines.append((base, base + QPointF(math.cos(b) * arm * length, math.sin(b) * arm * length)))
        for pen in (QPen(_c("#7f9fc8", 0.55), size * 0.07, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap),
                    QPen(_c("#ffffff", 1.0), size * 0.032, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)):
            q.setPen(pen)
            for a, b in lines:
                q.drawLine(a, b)
        q.setPen(Qt.PenStyle.NoPen)
        q.setBrush(_c("#ffffff"))
        q.drawPolygon(QPolygonF([QPointF(math.cos(math.radians(k * 60 + 30)) * size * 0.07,
                                         math.sin(math.radians(k * 60 + 30)) * size * 0.07) for k in range(6)]))
        q.end()
        return pm

    # -- the still picture -----------------------------------------------------------------------
    def draw_still(self, p: QPainter) -> None:
        w, h = self.w, self.h
        big = max(w, h)
        sun = QPointF(w * 0.30, h * 0.50)
        self.glow(p, sun.x(), sun.y(), big * 0.55, _c("#fff6e6", 0.50))
        self.glow(p, sun.x(), sun.y(), big * 0.12, _c("#fffdf6", 0.80))
        self._peaks(p)
        self._forest(p)
        self._drifts(p)
        for x, tall in ((w * 0.035, h * 0.62), (w * 0.975, h * 0.55), (w * 0.93, h * 0.34)):
            _snowy_spruce(p, x, h * 1.02, tall, "#5b7d95", "#f2f7fd")
        _grain(p, w, h, 0.02, self.dpr)

    def _peaks(self, p: QPainter) -> None:
        """The far range, two rows of peaks: white with snow, blue with distance, each peak's
        face away from the light in shadow behind a jagged ridge, a few rocky gullies on the
        lit faces, and mist at their feet."""
        w, h = self.w, self.h
        rng = random.Random(71)
        for base, tall, body, shadow, alpha in ((0.61, (0.09, 0.20), "#d5e2f2", "#8fa9cc", 0.9),
                                               (0.67, (0.05, 0.12), "#e2ebf6", "#9db4d3", 1.0)):
            peaks = []
            x = -w * 0.06
            while x < w * 1.06:
                half = w * rng.uniform(0.06, 0.13)
                peaks.append((x + half * rng.uniform(0.5, 1.0), h * rng.uniform(*tall), half))
                x += half * rng.uniform(0.9, 1.5)
            rough = _ridge_line(rng, -w * 0.02, w * 1.02, 0.0, h * 0.010, 8)
            lifts = []
            pts = []
            for n in rough:
                lift = max(ph * max(0.0, 1 - abs(n.x() - px) / half) ** 1.2 for px, ph, half in peaks)
                lifts.append(lift)
                pts.append(QPointF(n.x(), h * base - lift + n.y() * (0.5 + lift / (h * 0.12))))
            path = QPainterPath(QPointF(pts[0].x(), h))
            for pt in pts:
                path.lineTo(pt)
            path.lineTo(QPointF(pts[-1].x(), h))
            path.closeSubpath()
            top = min(pt.y() for pt in pts)
            g = QLinearGradient(0, top, 0, h * (base + 0.08))
            g.setColorAt(0.0, _c("#f7faff", alpha))
            g.setColorAt(0.5, _c(body, alpha))
            g.setColorAt(1.0, _c("#e8f0fa", alpha))
            p.fillPath(path, g)
            p.save()
            p.setClipPath(path)
            p.setPen(Qt.PenStyle.NoPen)
            n = len(pts)
            for px, ph, half in peaks:
                # The apex: the highest point near the peak's middle.
                lo, hi = max(0, int((px - half * 0.3 - pts[0].x()) / (pts[-1].x() - pts[0].x()) * (n - 1))), \
                    min(n - 1, int((px + half * 0.3 - pts[0].x()) / (pts[-1].x() - pts[0].x()) * (n - 1)))
                if hi <= lo:
                    continue
                i0 = min(range(lo, hi + 1), key=lambda i: pts[i].y())
                apex = pts[i0]
                # Down its right side to the valley.
                i1 = i0
                while i1 < n - 1 and (pts[i1 + 1].y() >= pts[i1].y() - h * 0.002 or i1 - i0 < 4):
                    i1 += 1
                    if pts[i1].y() > h * base - h * 0.01:
                        break
                foot = QPointF(apex.x() + (pts[i1].x() - apex.x()) * 0.28, h * base + h * 0.05)
                face = [apex] + pts[i0 + 1:i1 + 1] + [foot]
                # The ridge between the faces, jagged, back up to the apex.
                steps = 7
                for k in range(1, steps):
                    u = k / steps
                    face.append(QPointF(foot.x() + (apex.x() - foot.x()) * u + rng.uniform(-1, 1) * half * 0.05,
                                        foot.y() + (apex.y() - foot.y()) * u))
                sg = QLinearGradient(0, apex.y(), 0, h * base + h * 0.04)
                sg.setColorAt(0.0, _c(shadow, 0.75 * alpha))
                sg.setColorAt(1.0, _c(shadow, 0.0))
                p.setBrush(sg)
                p.drawPolygon(QPolygonF(face))
                # A few rocky gullies down the lit face.
                for _ in range(rng.randint(1, 3)):
                    start = QPointF(apex.x() - half * rng.uniform(0.05, 0.25), apex.y() + ph * rng.uniform(0.08, 0.3))
                    end = QPointF(start.x() - half * rng.uniform(0.1, 0.3), start.y() + ph * rng.uniform(0.3, 0.6))
                    mid = QPointF((start.x() + end.x()) / 2 + rng.uniform(-1, 1) * half * 0.05, (start.y() + end.y()) / 2)
                    gully = _taper(_bezier(start, mid, mid, end, 10), max(1.0, half * 0.02), 0.5)
                    p.setBrush(_c(shadow, 0.35 * alpha))
                    p.drawPath(gully)
            p.restore()
            rim = QPainterPath(pts[0])
            for pt in pts[1:]:
                rim.lineTo(pt)
            p.strokePath(rim, QPen(_c("#ffffff", 0.6), 1.0))
            mist = QLinearGradient(0, h * (base - 0.03), 0, h * (base + 0.08))
            mist.setColorAt(0.0, _c("#eef5fd", 0.0))
            mist.setColorAt(1.0, _c("#eef5fd", 0.85))
            p.fillRect(QRectF(0, h * (base - 0.03), w, h * 0.11), mist)

    def _forest(self, p: QPainter) -> None:
        """Rolling hills and the spruce on them, two rows, fading into the mist."""
        w, h = self.w, self.h
        rng = random.Random(83)
        for base, bumps, seed, tree, colour in ((0.73, 3, 1.0, 0.035, "#98b0cc"), (0.80, 2, 2.3, 0.05, "#7893b3")):
            hill = QPainterPath(QPointF(0, h))
            ridge = []
            steps = 80
            for i in range(steps + 1):
                u = i / steps
                y = h * (base - 0.035 * math.sin(u * math.pi * bumps + seed)
                         - 0.018 * math.sin(u * math.pi * bumps * 2.7 + seed * 2))
                ridge.append(QPointF(u * w, y))
                hill.lineTo(ridge[-1])
            hill.lineTo(QPointF(w, h))
            hill.closeSubpath()
            g = QLinearGradient(0, h * (base - 0.06), 0, h)
            g.setColorAt(0.0, _c("#f4f8fd"))
            g.setColorAt(1.0, _c("#dfeaf7"))
            p.fillPath(hill, g)
            trees = QPainterPath()
            trees.setFillRule(Qt.FillRule.WindingFill)
            x = -tree * h
            while x < w + tree * h:
                i = min(steps, max(0, round(x / w * steps)))
                clump = 0.5 + 0.5 * math.sin(x / w * 13 + seed * 3) * math.sin(x / w * 5 + seed)
                if clump > 0.25:
                    tall = h * tree * (0.5 + 0.7 * clump * rng.random() ** 0.6)
                    _spruce(trees, x, ridge[i].y() + h * 0.012, tall)
                    x += tall * rng.uniform(0.18, 0.34)
                else:
                    x += h * tree * 0.4
            # Snow along their upper edges, catching the light: the same trees in white, a little up
            # and to the left, under them.
            _fill_each(p, trees.translated(-1.0, -1.4), _c("#ffffff", 0.9))
            _fill_each(p, trees, _c(colour))
            mist = QLinearGradient(0, h * (base - 0.05), 0, h * (base + 0.06))
            mist.setColorAt(0.0, _c("#eef5fd", 0.0))
            mist.setColorAt(1.0, _c("#eef5fd", 0.8))
            p.fillRect(QRectF(0, h * (base - 0.05), w, h * 0.11), mist)

    def _drifts(self, p: QPainter) -> None:
        """Deep snow in front: two long drifts, each with its shadowed lee under the crest."""
        w, h = self.w, self.h
        for base, amp, seed, shade in ((0.87, 0.030, 0.7, 0.20), (0.94, 0.025, 2.1, 0.26)):
            crest = []
            steps = 70
            for i in range(steps + 1):
                u = i / steps
                crest.append(QPointF(u * w, h * (base - amp * math.sin(u * math.pi * 1.6 + seed)
                                                 - amp * 0.4 * math.sin(u * math.pi * 4.1 + seed * 2))))
            drift = QPainterPath(QPointF(0, h))
            for pt in crest:
                drift.lineTo(pt)
            drift.lineTo(QPointF(w, h))
            drift.closeSubpath()
            g = QLinearGradient(0, h * (base - amp * 1.4), 0, h)
            g.setColorAt(0.0, _c("#ffffff"))
            g.setColorAt(1.0, _c("#e6f0fb"))
            p.fillPath(drift, g)
            p.save()
            p.setClipPath(drift)
            lee = QPainterPath(crest[0])
            for pt in crest[1:]:
                lee.lineTo(pt)
            p.strokePath(lee, QPen(_c("#9db8da", shade), h * 0.018, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.strokePath(lee, QPen(_c("#9db8da", shade * 0.6), h * 0.045, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.restore()
            p.strokePath(lee, QPen(_c("#ffffff", 0.9), 1.2))

    # -- the snow ------------------------------------------------------------------------------
    def spawn(self, anywhere=False):
        r = self.rng
        f = _Flake()
        f.d = r.random()
        f.kind = "near" if f.d > 0.95 else ("crystal" if f.d > 0.62 and r.random() < 0.18 else "flake")
        if f.kind == "near":
            f.r = r.uniform(6.0, 10.0)
            f.vy = r.uniform(75, 100)
        else:
            f.r = 1.0 + 3.4 * f.d ** 1.5
            f.vy = 14 + 44 * f.d + r.uniform(0, 6)
        f.x = r.uniform(-0.1 * self.w, self.w * 1.05)
        f.y = r.uniform(-10, self.h) if anywhere else r.uniform(-40, -8) - f.r * 2
        f.amp = 5 + 18 * f.d
        f.freq = 0.4 + 0.9 * r.random()
        f.phase = r.random() * math.tau
        f.op = 0.45 if f.kind == "near" else 0.5 + 0.45 * f.d
        f.turn = r.uniform(0, 60)
        f.spin = r.uniform(-25, 25)
        return f

    def step(self, dt: float) -> None:
        super().step(dt)
        wind = 6 + 9 * math.sin(self.time * 0.09) + 3 * math.sin(self.time * 0.41)
        for f in self.items:
            f.y += f.vy * dt
            f.x += wind * (0.4 + f.d) * dt
            f.turn += f.spin * dt
            if f.y > self.h + 10 + f.r * 2 or f.x > self.w + 20 + f.r * 2:
                fresh = self.spawn()
                for name in _Flake.__slots__:
                    setattr(f, name, getattr(fresh, name))

    def paint_moving(self, p: QPainter) -> None:
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        t = self.time
        for f in self.items:
            x = f.x + f.amp * math.sin(f.phase + t * f.freq)
            p.setOpacity(f.op)
            if f.kind == "crystal":
                size = f.r * 2.6
                p.save()
                p.translate(x, f.y)
                p.rotate(f.turn)
                p.drawPixmap(QRectF(-size, -size, size * 2, size * 2), self.crystal, QRectF(0, 0, 64, 64))
                p.restore()
            elif f.kind == "near":
                size = f.r * 2.0
                p.drawPixmap(QRectF(x - size, f.y - size, size * 2, size * 2), self.near, QRectF(0, 0, 48, 48))
            else:
                size = f.r * 1.9
                p.drawPixmap(QRectF(x - size, f.y - size, size * 2, size * 2), self.flake, QRectF(0, 0, 48, 48))
        p.setOpacity(1.0)


# -- Ember -------------------------------------------------------------------------------

class _Spark:
    __slots__ = ("x", "y", "vx", "vy", "age", "life", "r", "amp", "freq", "phase", "flick", "near")


class _Wisp:
    __slots__ = ("x", "y", "r", "vy", "drift", "age", "life", "sprite")


class Embers(Scene):
    """A fire at the foot of the window. Charred logs lie across a bed of glowing coals; flames
    lick up between them, flickering, tallest in the middle, their light pulsing on the smoky
    air. Sparks fly up, swirling, cooling from white to red as they go, some close to the eye
    and blurred, and smoke drifts up slowly.

    The flames are worked out at a quarter of the size and smoothed up, which makes them soft,
    as flames are, and costs a sixteenth; and as often as the aurora's curtains are, which a
    flicker hides.
    """

    count = 70
    FIRE = 4               # the flames are drawn at 1/FIRE of the size
    FIRE_HEIGHT = 0.26     # how much of the height, up from the coals, flames may reach

    def __init__(self, t, amount=1.0, seed=11):
        super().__init__(t, amount, seed)
        self.hot, self.warm, self.cool = QColor(t["spark_hot"]), QColor(t["spark_warm"]), QColor(t["spark_cool"])
        self.halo = _sprite(_c(t["spark_warm"], 0.85), _c(t["spark_cool"], 0.35), 48, 0.05)
        self.smoke = [self._smoke_sprite(40 + i) for i in range(3)]
        self.flames: list[tuple] = []
        self.wisps: list[_Wisp] = []
        self.fire: QImage | None = None
        self.front: QPixmap | None = None
        self.bed = self.fire_top = 0.0
        self.fire_due = True
        self.since = 0.0

    @staticmethod
    def _smoke_sprite(seed: int, size: int = 96) -> QPixmap:
        """A drifting puff of smoke, dark, warmed a little underneath by the fire."""
        pm = QPixmap(size, size)
        pm.fill(Qt.GlobalColor.transparent)
        q = QPainter(pm)
        q.setRenderHint(QPainter.RenderHint.Antialiasing)
        rng = random.Random(seed)
        for _ in range(9):
            cx = size / 2 + rng.uniform(-0.2, 0.2) * size
            cy = size / 2 + rng.uniform(-0.18, 0.2) * size
            r = size * rng.uniform(0.16, 0.3)
            under = cy > size * 0.55
            _blob(q, cx, cy, r, _c("#8a4a2c" if under else "#5a4238", 0.45))
        q.end()
        return pm

    # -- the still picture -------------------------------------------------------------------
    def draw_still(self, p: QPainter) -> None:
        w, h = self.w, self.h
        big = max(w, h)
        self.glow(p, w * 0.5, h * 1.15, big * 0.85, _c(self.t["spark_warm"], 0.30))
        self.glow(p, w * 0.5, h * 1.02, big * 0.42, _c("#ffb347", 0.16))
        self.glow(p, w * 0.12, h * 1.10, big * 0.45, _c(self.t["spark_cool"], 0.24))
        self.glow(p, w * 0.88, h * 1.12, big * 0.40, _c(self.t["spark_cool"], 0.18))
        # Smoke hanging in the air, darker high up, faintly lit lower down.
        rng = random.Random(33)

        def haze(q: QPainter) -> None:
            for _ in range(30):
                x, y = rng.uniform(-0.1, 1.1) * w, rng.uniform(-0.1, 0.8) * h
                lit = y > h * 0.45
                _blob(q, x, y, h * rng.uniform(0.14, 0.32), _c("#6a3018" if lit else "#000000", rng.uniform(0.05, 0.12)),
                      rng.uniform(0.35, 0.6), rng.uniform(-25, 25))

        _spread(p, _soft(w, h, haze, 6.0), QRectF(0, 0, w, h))
        _vignette(p, w, h, 0.5, centre=(0.5, 0.65))
        _grain(p, w, h, 0.035, self.dpr)

    def resize(self, w: int, h: int, dpr: float = 1.0) -> None:
        super().resize(w, h, dpr)
        w, h = self.w, self.h
        self.bed = h * 0.95
        self.fire_top = self.bed - h * self.FIRE_HEIGHT
        rng = random.Random(19)
        # Flames along the bed, tallest in the middle: (x, width, height, phase, speed, lean).
        self.flames = []
        x = w * 0.08
        while x < w * 0.92:
            middle = math.exp(-((x / w - 0.5) / 0.30) ** 2)
            tall = h * (0.03 + 0.22 * middle) * rng.uniform(0.65, 1.1)
            wide = tall * rng.uniform(0.38, 0.6)
            self.flames.append((x, wide, tall, rng.uniform(0, math.tau), rng.uniform(4.5, 8.0), rng.uniform(-0.2, 0.2)))
            x += max(h * 0.035, wide * rng.uniform(0.45, 0.75))
        self.fire = QImage(max(1, math.ceil(w / self.FIRE)),
                           max(1, math.ceil((self.bed + h * 0.02 - self.fire_top) / self.FIRE)),
                           QImage.Format.Format_ARGB32_Premultiplied)
        self.front = self._render_front()
        area = max(0.4, min(2.6, w * h / BASE_AREA))
        self.wisps = [self._wisp(anywhere=True) for _ in range(max(2, int(6 * self.amount * area)))]

    def _render_front(self) -> QPixmap:
        """The bed of coals and the logs on it: dark, lit from beneath, their edges glowing."""
        w, h = self.w, self.h
        bed = self.bed
        self.front_top = max(0, math.floor(h * 0.86))
        pm = _layer(w, h - self.front_top, self.dpr)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.translate(0, -self.front_top)
        rng = random.Random(27)
        glow = QLinearGradient(0, bed - h * 0.03, 0, h)
        glow.setColorAt(0.0, _c("#b8431a", 0.0))
        glow.setColorAt(0.4, _c("#c24a1a", 0.75))
        glow.setColorAt(1.0, _c("#e8701f", 0.9))
        p.fillRect(QRectF(0, bed - h * 0.03, w, h - bed + h * 0.03), glow)
        # Coals: heaped rows of dark broken lumps, the light coming up in the cracks between them,
        # ash grey on their tops, and here and there one still glowing through.
        for row in range(3):
            x = -h * 0.01 + row * h * 0.006
            while x < w + h * 0.02:
                r = h * rng.uniform(0.007, 0.015)
                c = QPointF(x, bed - h * 0.006 + row * h * 0.012 + rng.uniform(-0.3, 0.3) * r)
                pts = []
                for k in range(6):
                    a = math.tau * (k + rng.uniform(-0.3, 0.3)) / 6
                    pts.append(c + QPointF(math.cos(a) * r * rng.uniform(0.75, 1.15),
                                           math.sin(a) * r * rng.uniform(0.55, 0.85)))
                hot = rng.random() < 0.16
                g = QLinearGradient(0, c.y() - r, 0, c.y() + r)
                if hot:
                    g.setColorAt(0.0, _c("#ffb347"))
                    g.setColorAt(1.0, _c("#c2451a"))
                else:
                    g.setColorAt(0.0, _c("#4a3a34"))
                    g.setColorAt(0.35, _c("#1e0e09"))
                    g.setColorAt(1.0, _c("#3a150a"))
                p.setPen(QPen(_c("#ff8a36", 0.55), max(0.6, r * 0.14)))
                p.setBrush(g)
                p.drawPolygon(QPolygonF(pts))
                x += r * rng.uniform(1.3, 1.9)
        # Logs across the fire: black with char, lit orange from the coals beneath.
        p.setPen(Qt.PenStyle.NoPen)
        for x0, y0, x1, y1, thick in ((-0.05, 0.925, 0.33, 0.9, 0.028), (0.24, 0.945, 0.57, 0.915, 0.032),
                                      (0.47, 0.91, 0.82, 0.94, 0.030), (0.72, 0.925, 1.06, 0.9, 0.026),
                                      (0.37, 0.935, 0.62, 0.885, 0.021)):
            a, b = QPointF(w * x0, h * y0), QPointF(w * x1, h * y1)
            r = h * thick
            length = math.hypot(b.x() - a.x(), b.y() - a.y())
            angle = math.degrees(math.atan2(b.y() - a.y(), b.x() - a.x()))
            p.save()
            p.translate(a)
            p.rotate(angle)
            body = QPainterPath()
            body.addRoundedRect(QRectF(0, -r, length, 2 * r), r * 0.9, r * 0.9)
            g = QLinearGradient(0, -r, 0, r)
            g.setColorAt(0.0, _c("#2e1710"))
            g.setColorAt(0.4, _c("#120806"))
            g.setColorAt(0.75, _c("#2a0f08"))
            g.setColorAt(1.0, _c("#d0531c"))
            p.fillPath(body, g)
            p.save()
            p.setClipPath(body)
            # The grain of the charred wood, and patches glowing where it still burns.
            p.setPen(QPen(_c("#000000", 0.35), max(0.6, r * 0.05)))
            for k in range(4):
                v = -r * 0.6 + k * r * 0.35
                p.drawLine(QPointF(rng.uniform(0, length * 0.3), v),
                           QPointF(length - rng.uniform(0, length * 0.3), v + rng.uniform(-1, 1) * r * 0.1))
            p.setPen(Qt.PenStyle.NoPen)
            for _ in range(int(length / (r * 2.5))):
                _blob(p, rng.uniform(0, length), rng.uniform(0.2, 0.9) * r, r * rng.uniform(0.3, 0.7),
                      _c("#ff6a1a", rng.uniform(0.25, 0.55)), 0.5)
            rim = QLinearGradient(0, -r, 0, -r * 0.45)
            rim.setColorAt(0.0, _c("#ff9a45", 0.40))
            rim.setColorAt(1.0, _c("#ff9a45", 0.0))
            p.fillRect(QRectF(0, -r, length, r * 0.55), rim)
            p.restore()
            p.restore()
            # The cut end, where it shows: dark rings with a glowing edge.
            if 0 < b.x() < w:
                p.save()
                p.translate(b)
                p.rotate(angle)
                end = QRadialGradient(QPointF(0, 0), r)
                end.setColorAt(0.0, _c("#2a140c"))
                end.setColorAt(0.7, _c("#4a1c0c"))
                end.setColorAt(1.0, _c("#ff8a36"))
                p.setBrush(end)
                p.drawEllipse(QPointF(0, 0), r * 0.45, r)
                p.restore()
        p.end()
        return pm

    # -- what moves ----------------------------------------------------------------------------
    def _wisp(self, anywhere: bool = False) -> _Wisp:
        r, w, h = self.rng, self.w, self.h
        s = _Wisp()
        s.x = r.uniform(0.1, 0.9) * w
        s.life = r.uniform(14, 22)
        s.age = r.uniform(0, s.life) if anywhere else 0.0
        s.vy = h * r.uniform(0.025, 0.045)
        s.y = self.bed - s.vy * s.age
        s.r = h * r.uniform(0.08, 0.14)
        s.drift = r.uniform(-8, 12)
        s.sprite = r.randrange(len(self.smoke))
        return s

    def spawn(self, anywhere=False):
        r = self.rng
        s = _Spark()
        # One in eight drifts past close to the eye: big, soft, slow and dim.
        s.near = r.random() < 0.12
        middle = r.random() < 0.6                     # most fly up from the middle of the fire
        s.x = r.gauss(0.5, 0.18) * self.w if middle else r.uniform(0, self.w)
        s.vy = -r.uniform(22, 45) if s.near else -r.uniform(40, 130)
        s.vx = r.uniform(-8, 8)
        s.life = r.uniform(6.0, 11.0) if s.near else r.uniform(4.0, 9.0)
        s.age = r.uniform(0, s.life) if anywhere else 0.0
        s.y = self.h * 0.93 + r.uniform(0, 20) + (s.vy * s.age if anywhere else 0.0)
        s.r = r.uniform(4.0, 7.0) if s.near else 1.0 + 2.1 * r.random() ** 2
        s.amp = r.uniform(4, 26)
        s.freq = r.uniform(0.6, 1.8)
        s.phase = r.random() * math.tau
        s.flick = r.uniform(6, 15)
        return s

    def step(self, dt: float) -> None:
        super().step(dt)
        t = self.time
        for s in self.items:
            s.age += dt
            s.y += s.vy * dt
            # Carried by the rising air, which swirls: a slow sway, and eddies that come and go.
            swirl = 14 * math.sin(s.y * 0.012 + t * 0.7 + s.phase) * math.cos(t * 0.23 + s.phase * 0.5)
            s.x += (s.vx + s.amp * math.cos(s.phase + t * s.freq) * s.freq + swirl) * dt
            if s.age > s.life or s.y < -20:
                fresh = self.spawn()
                for name in _Spark.__slots__:
                    setattr(s, name, getattr(fresh, name))
        self.since += dt
        if self.since >= self.redraw_every():
            self.since = 0.0
            self.fire_due = True
        for i, m in enumerate(self.wisps):
            m.age += dt
            m.y -= m.vy * dt
            m.x += m.drift * dt
            m.r += self.h * 0.006 * dt
            if m.age > m.life:
                self.wisps[i] = self._wisp()

    def _colour(self, k: float) -> QColor:
        if k < 0.35:
            a, b, u = self.hot, self.warm, k / 0.35
        else:
            a, b, u = self.warm, self.cool, (k - 0.35) / 0.65
        return QColor(int(a.red() + (b.red() - a.red()) * u), int(a.green() + (b.green() - a.green()) * u),
                      int(a.blue() + (b.blue() - a.blue()) * u))

    def _flame(self, x: float, wide: float, tall: float, lean: float) -> QPainterPath:
        """A tongue of flame: full low down, drawn in and curling to its tip, leaning with the air."""
        base = self.bed
        k = lean * tall
        path = QPainterPath(QPointF(x - wide / 2, base))
        path.cubicTo(QPointF(x - wide * 0.78, base - tall * 0.25), QPointF(x - wide * 0.45 + k * 0.3, base - tall * 0.55),
                     QPointF(x - wide * 0.12 + k * 0.6, base - tall * 0.78))
        path.quadTo(QPointF(x + k * 0.9, base - tall * 0.9), QPointF(x + k, base - tall))
        path.quadTo(QPointF(x + wide * 0.14 + k * 0.7, base - tall * 0.7),
                    QPointF(x + wide * 0.36 + k * 0.35, base - tall * 0.45))
        path.cubicTo(QPointF(x + wide * 0.72, base - tall * 0.25), QPointF(x + wide * 0.62, base - tall * 0.05),
                     QPointF(x + wide / 2, base))
        path.closeSubpath()
        return path

    def _render_fire(self) -> None:
        img, t, w = self.fire, self.time, self.w
        img.fill(Qt.GlobalColor.transparent)
        q = QPainter(img)
        q.setRenderHint(QPainter.RenderHint.Antialiasing)
        q.scale(1 / self.FIRE, 1 / self.FIRE)
        q.translate(0, -self.fire_top)
        q.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
        base = self.bed
        # The glow over the coals, pulsing with the flames.
        pulse = 0.8 + 0.12 * math.sin(t * 3.1) + 0.08 * math.sin(t * 7.3 + 1.0)
        _blob(q, w * 0.5, base, w * 0.34, _c("#ff6a1a", 0.40 * pulse), 0.32)
        q.setPen(Qt.PenStyle.NoPen)
        for x, wide, tall, phase, speed, lean in self.flames:
            f = 0.72 + 0.18 * math.sin(t * speed + phase) + 0.10 * math.sin(t * speed * 2.3 + phase * 1.7)
            sway = lean + 0.16 * math.sin(t * speed * 0.4 + phase)
            height = tall * f
            g = QLinearGradient(0, base, 0, base - height)
            g.setColorAt(0.0, _c("#ffb347", 0.60))
            g.setColorAt(0.3, _c("#ff7a22", 0.50))
            g.setColorAt(0.7, _c("#d8401a", 0.25))
            g.setColorAt(1.0, _c("#b0301a", 0.0))
            q.setBrush(g)
            q.drawPath(self._flame(x, wide, height, sway))
            if tall < self.h * 0.07:
                continue                                  # too small for a heart that would show
            inner = QLinearGradient(0, base, 0, base - height * 0.55)
            inner.setColorAt(0.0, _c("#fff2c4", 0.55))
            inner.setColorAt(0.5, _c("#ffc05a", 0.30))
            inner.setColorAt(1.0, _c("#ff8a2a", 0.0))
            q.setBrush(inner)
            q.drawPath(self._flame(x + wide * 0.04, wide * 0.45, height * 0.55, sway * 0.8))
        q.end()

    def paint_moving(self, p: QPainter) -> None:
        t = self.time
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        # Smoke first, behind the fire's light.
        for m in self.wisps:
            k = m.age / m.life
            p.setOpacity(0.32 * max(0.0, math.sin(math.pi * k)) ** 1.3)
            p.drawPixmap(QRectF(m.x - m.r, m.y - m.r, m.r * 2, m.r * 2), self.smoke[m.sprite], QRectF(0, 0, 96, 96))
        p.setOpacity(1.0)
        plus = QPainter.CompositionMode.CompositionMode_Plus
        if self.fire is not None:
            if self.fire_due:
                self._render_fire()
                self.fire_due = False
            p.setCompositionMode(plus)
            p.drawImage(QRectF(0, self.fire_top, self.fire.width() * self.FIRE, self.fire.height() * self.FIRE), self.fire)
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        if self.front is not None:
            p.drawPixmap(QPointF(0, self.front_top), self.front)
        # Sparks: their glow, added, then their hot cores and streaks.
        p.setCompositionMode(plus)
        for s in self.items:
            k = min(1.0, s.age / s.life)
            flicker = 0.62 + 0.38 * math.sin(s.phase * 3 + t * s.flick)
            a = (1.0 - k ** 2.4) * flicker
            size = s.r * (4.0 if s.near else 6.0)
            p.setOpacity(a * (0.22 if s.near else 0.75))
            p.drawPixmap(QRectF(s.x - size, s.y - size, size * 2, size * 2), self.halo, QRectF(0, 0, 48, 48))
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        p.setPen(Qt.PenStyle.NoPen)
        for s in self.items:
            if s.near:
                continue          # all glow, no core: out of focus
            k = min(1.0, s.age / s.life)
            a = (1.0 - k ** 2.4) * (0.62 + 0.38 * math.sin(s.phase * 3 + t * s.flick))
            colour = self._colour(k)
            p.setOpacity(a)
            if -s.vy > 70:
                tail = QPointF(s.x - s.vx * 0.08, s.y - s.vy * 0.08)
                g = QLinearGradient(QPointF(s.x, s.y), tail)
                g.setColorAt(0.0, colour)
                clear = QColor(colour)
                clear.setAlpha(0)
                g.setColorAt(1.0, clear)
                p.setPen(QPen(QBrush(g), s.r * 1.1, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                p.drawLine(QPointF(s.x, s.y), tail)
                p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(colour)
            p.drawEllipse(QPointF(s.x, s.y), s.r * 0.75, s.r * 0.75)
            if k < 0.3:
                p.setBrush(_c("#fffbe8"))
                p.drawEllipse(QPointF(s.x, s.y), s.r * 0.38, s.r * 0.38)       # still white hot
        p.setOpacity(1.0)


# -- Aurora -------------------------------------------------------------------------------

def _layer(w: float, h: float, dpr: float) -> QPixmap:
    """A transparent pixmap to draw a still layer into."""
    pm = QPixmap(QSize(max(1, math.ceil(w * dpr)), max(1, math.ceil(h * dpr))))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    return pm


def _ray(stops) -> QImage:
    """One ray of a curtain of light, top to bottom: faint high up, brightest just above its lower
    edge (at 0.9), with the thin pink fringe real aurora has under that."""
    img = QImage(4, 256, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    q = QPainter(img)
    g = QLinearGradient(0, 0, 0, 256)
    for pos, colour, alpha in stops:
        g.setColorAt(pos, _c(colour, alpha))
    q.fillRect(QRectF(0, 0, 4, 256), g)
    q.end()
    return img


GREEN_RAY = ((0.0, "#7b5cff", 0.0), (0.30, "#8a5cff", 0.13), (0.55, "#22d3c5", 0.30), (0.78, "#39ff9c", 0.66),
             (0.885, "#c4ffe2", 0.95), (0.915, "#4dffab", 0.55), (0.96, "#ff5fa8", 0.07), (1.0, "#ff5fa8", 0.0))
VIOLET_RAY = ((0.0, "#6d4dff", 0.0), (0.40, "#7a5bff", 0.17), (0.70, "#2bd8d0", 0.34), (0.885, "#8affd8", 0.62),
              (0.93, "#2bd8d0", 0.22), (1.0, "#2bd8d0", 0.0))
RAY_EDGE = 0.9


class _Band:
    """One curtain: where its lower edge runs, how tall it stands, and where along it the light is."""
    __slots__ = ("base", "height", "gain", "floor", "ray", "wave", "tall", "env", "rays", "surge", "grain")


class _Glint:
    __slots__ = ("x", "y", "length", "age", "life", "phase")


class Aurora(Stars):
    """Northern lights over mountains, a spruce shore and a still lake that mirrors them.

    The curtains are worked out a column at a time at a quarter of the window's size, into
    an image that is then smoothed up to full size: aurora has no hard edges, so nothing is
    lost, and it costs a sixteenth. Each column is one stretched ray whose lower edge follows
    slow waves, whose height breathes, and whose brightness comes from a drifting glow along
    the curtain, fine rays that slide past each other, and now and then a surge that runs
    the length of it. Light is added, not painted over, so where curtains cross they shine.
    """

    count = 80
    sky = 0.78
    SCALE = 4
    COLUMNS = 120          # per curtain, whatever the width

    def __init__(self, t, amount=1.0, seed=11):
        super().__init__(t, amount, seed)
        self.green, self.violet = _ray(GREEN_RAY), _ray(VIOLET_RAY)
        self.bands = self._bands()
        self.horizon = 0.0
        self.lights: QImage | None = None
        self.land = self.shore = None
        self.glints: list[_Glint] = []
        self.dirty = True
        self.since = 0.0

    def _bands(self) -> list[_Band]:
        r = random.Random(17)

        def band(base, height, gain, floor, ray, reach):
            b = _Band()
            b.base, b.height, b.gain, b.floor, b.ray = base, height, gain, floor, ray
            b.wave = (0.07 * reach, r.uniform(4.0, 6.5), r.uniform(0.05, 0.09) * r.choice((-1, 1)),
                      r.uniform(0, math.tau), 0.03 * reach, r.uniform(10, 15), r.uniform(0.08, 0.14),
                      r.uniform(0, math.tau))
            b.tall = (r.uniform(6, 9), r.uniform(0.10, 0.20), r.uniform(0, math.tau))
            b.env = (r.uniform(3.0, 4.5), r.uniform(0.04, 0.08) * r.choice((-1, 1)), r.uniform(0, math.tau))
            b.rays = (r.uniform(55, 80), r.uniform(0.5, 0.9), r.uniform(0, math.tau), r.uniform(120, 170),
                      r.uniform(0.9, 1.5))
            b.surge = (r.uniform(8, 11), r.uniform(1.2, 1.9), r.uniform(0, math.tau))
            b.grain = tuple(r.random() for _ in range(97))      # how bright each fine ray is, drifting along
            return b

        return [band(0.40, 0.30, 0.55, 0.0, self.violet, 0.8),     # high and faint, more violet
                band(0.62, 0.46, 0.95, 0.30, self.green, 1.0),     # the main curtain
                band(0.87, 0.26, 0.60, 0.15, self.green, 0.6)]     # low, down behind the mountains

    # -- the still parts ---------------------------------------------------------------
    def _render_static(self) -> QPixmap:
        self.horizon = round(self.h * self.sky)
        return super()._render_static()

    def draw_still(self, p: QPainter) -> None:
        w, h, hz = self.w, self.h, self.horizon
        self.glow(p, w * 0.5, hz, max(w, h) * 0.75, _c("#2fe0a0", 0.09))
        self.glow(p, w * 0.3, h * 0.05, max(w, h) * 0.5, _c("#6d4dff", 0.06))
        self.milky_way(p, 0.4)
        _grain(p, w, h, 0.03, self.dpr)

    def resize(self, w: int, h: int, dpr: float = 1.0) -> None:
        super().resize(w, h, dpr)
        self.lights = QImage(max(1, math.ceil(self.w / self.SCALE)), max(1, math.ceil(self.horizon / self.SCALE)),
                             QImage.Format.Format_ARGB32_Premultiplied)
        trees = self._trees()
        self.land = self._render_land(trees)
        self.shore = self._render_shore(trees)
        target = int(34 * self.amount * max(0.4, min(2.6, self.w * self.h / BASE_AREA)))
        self.glints = [self._glint(anywhere=True) for _ in range(target)]
        self.dirty = True

    def _ridge(self, u: float, jitter: float) -> float:
        """How high the mountains stand at ``u`` across, as a part of the height."""
        m1 = 0.15 * max(0.0, 1 - abs(u - 0.72) / 0.30) ** 1.4
        m2 = 0.10 * max(0.0, 1 - abs(u - 0.23) / 0.27) ** 1.3
        m3 = 0.055 * max(0.0, 1 - abs(u - 0.47) / 0.18)
        return 0.024 + max(m1, m2, m3) + 0.010 * math.sin(u * 37 + 1.7) + 0.006 * math.sin(u * 83) + jitter

    def _trees(self) -> QPainterPath:
        """The far shore: a band of spruce, taller in clumps."""
        w, h, hz = self.w, self.h, self.horizon
        rng = random.Random(13)
        path = QPainterPath()
        path.setFillRule(Qt.FillRule.WindingFill)
        path.addRect(QRectF(-2, hz - h * 0.006, w + 4, h * 0.006 + 1))
        x = -4.0
        while x < w + 6:
            clump = 0.55 + 0.45 * math.sin(x / max(w, 1) * 11 + 0.8) * math.sin(x / max(w, 1) * 4.3)
            tall = h * (0.016 + 0.050 * clump * rng.random() ** 0.7)
            _spruce(path, x, hz, tall)
            x += max(1.6, tall * rng.uniform(0.18, 0.34))
        return path

    def _render_land(self, trees: QPainterPath) -> QPixmap:
        w, h, hz = self.w, self.h, self.horizon
        self.land_top = max(0, math.floor(hz - h * 0.21))           # nothing of it is higher than this
        pm = _layer(w, h - self.land_top, self.dpr)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.translate(0, -self.land_top)
        rng = random.Random(9)
        steps = 180
        pts = [QPointF(0, hz - h * self._ridge(0.0, 0.0))]
        for i in range(1, steps + 1):
            u = i / steps
            pts.append(QPointF(u * w, hz - h * self._ridge(u, rng.uniform(-0.004, 0.004))))
        ridge = QPainterPath(pts[0])
        for pt in pts[1:]:
            ridge.lineTo(pt)
        mountains = QPainterPath(ridge)
        mountains.lineTo(QPointF(w, hz + 1))
        mountains.lineTo(QPointF(0, hz + 1))
        mountains.closeSubpath()
        self.mountains = mountains
        g = QLinearGradient(0, hz - h * 0.18, 0, hz)
        g.setColorAt(0.0, _c(self.t["ridge"]).lighter(135))
        g.setColorAt(1.0, _c(self.t["land"]))
        p.fillPath(mountains, g)
        p.save()
        p.setClipPath(mountains)
        # Snow on the tops, faintly lit by the sky.
        snow = QLinearGradient(0, hz - h * 0.17, 0, hz - h * 0.05)
        snow.setColorAt(0.0, _c("#6f9ea6", 0.42))
        snow.setColorAt(1.0, _c("#6f9ea6", 0.0))
        p.fillRect(QRectF(0, hz - h * 0.2, w, h * 0.15), snow)
        # Each peak's two faces: the one towards the brightest of the lights, to the left, catching
        # their green; the other in shadow; a jagged ridge between them.
        p.setPen(Qt.PenStyle.NoPen)
        n = len(pts)
        apexes = [i for i in range(3, n - 3) if hz - pts[i].y() > h * 0.045
                  and pts[i].y() <= min(pt.y() for pt in pts[i - 3:i + 4])]
        for i0 in apexes:
            apex = pts[i0]
            for side in (1, -1):
                i1 = i0
                while 0 < i1 < n - 1 and (pts[i1 + side].y() >= pts[i1].y() - h * 0.002 or abs(i1 - i0) < 3):
                    i1 += side
                    if pts[i1].y() > hz - h * 0.012:
                        break
                foot = QPointF(apex.x() + (pts[i1].x() - apex.x()) * 0.3, hz + 2)
                edge = pts[i0 + 1:i1 + 1] if side > 0 else pts[i1:i0][::-1]
                face = [apex] + edge + [foot]
                for k in range(1, 6):
                    u = k / 6
                    face.append(QPointF(foot.x() + (apex.x() - foot.x()) * u + rng.uniform(-1, 1) * h * 0.004,
                                        foot.y() + (apex.y() - foot.y()) * u))
                fg = QLinearGradient(0, apex.y(), 0, hz)
                if side > 0:
                    fg.setColorAt(0.0, _c("#000000", 0.42))
                    fg.setColorAt(1.0, _c("#000000", 0.12))
                else:
                    fg.setColorAt(0.0, _c("#3fe0a8", 0.16))
                    fg.setColorAt(1.0, _c("#3fe0a8", 0.0))
                p.setBrush(fg)
                p.drawPolygon(QPolygonF(face))
        # Mist lying along the foot of the mountains.
        mist = QLinearGradient(0, hz - h * 0.05, 0, hz)
        mist.setColorAt(0.0, _c("#1c4a52", 0.0))
        mist.setColorAt(1.0, _c("#1c4a52", 0.35))
        p.fillRect(QRectF(0, hz - h * 0.05, w, h * 0.05), mist)
        p.restore()
        p.strokePath(ridge, QPen(_c("#9fffd8", 0.20), 1.1))
        # The lake, and the shore in front of the mountains.
        water = QLinearGradient(0, hz, 0, h)
        water.setColorAt(0.0, _c(self.t["water"]).lighter(140))
        water.setColorAt(1.0, _c(self.t["land"]))
        p.fillRect(QRectF(0, hz, w, h - hz), water)
        _fill_each(p, trees, _c(self.t["land"]))
        p.end()
        return pm

    def _render_shore(self, trees: QPainterPath) -> QPixmap:
        """What goes over the reflection: the mountains and the trees upside down in the water,
        and the waterline."""
        w, h, hz = self.w, self.h, self.horizon
        self.shore_top = max(0, math.floor(hz - 2))
        pm = _layer(w, h - self.shore_top, self.dpr)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.translate(0, -self.shore_top)
        mirror = QTransform(1, 0, 0, -0.8, 0, hz * 1.8)          # y -> hz - 0.8 (y - hz)
        fade = QLinearGradient(0, hz, 0, hz + h * 0.16)
        fade.setColorAt(0.0, _c(self.t["ridge"], 0.75))
        fade.setColorAt(1.0, _c(self.t["ridge"], 0.35))
        p.fillPath(mirror.map(self.mountains), fade)
        _fill_each(p, mirror.map(trees), _c(self.t["land"], 0.88))
        p.setPen(QPen(_c("#9fffd8", 0.10), 1))
        p.drawLine(QPointF(0, hz + 0.5), QPointF(w, hz + 0.5))
        p.end()
        return pm

    # -- the moving parts ---------------------------------------------------------------
    def _glint(self, anywhere: bool = False) -> _Glint:
        r, hz = self.rng, self.horizon
        g = _Glint()
        near = r.random() ** 0.8
        g.y = hz + 4 + near * (self.h - hz - 4)
        g.x = r.uniform(0, self.w)
        g.length = (2 + 9 * r.random()) * (0.6 + near)
        g.life = r.uniform(1.5, 4.0)
        g.age = r.uniform(0, g.life) if anywhere else 0.0
        g.phase = r.uniform(0, math.tau)
        return g

    def step(self, dt: float) -> None:
        super().step(dt)
        self.since += dt
        if self.since >= self.redraw_every():
            self.since = 0.0
            self.dirty = True
        for i, g in enumerate(self.glints):
            g.age += dt
            if g.age > g.life:
                self.glints[i] = self._glint()

    def _render_lights(self) -> None:
        img = self.lights
        img.fill(Qt.GlobalColor.transparent)
        q = QPainter(img)
        q.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
        q.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        bw, bh = img.width(), img.height()
        step = max(1, round(bw / self.COLUMNS))
        t = self.time
        sin = math.sin
        for b in self.bands:
            a1, k1, w1, p1, a2, k2, w2, p2 = b.wave
            k3, w3, p3 = b.tall
            ek, ew, ep = b.env
            rk, rw, rp, rk2, rw2 = b.rays
            sk, sw, sp = b.surge
            ray = b.ray
            grain, count = b.grain, len(b.grain)
            drift = t * 1.3
            for i in range(0, bw, step):
                x = i / bw
                g = i / step + drift
                j = int(g)
                g -= j
                fine = grain[j % count] * (1 - g) + grain[(j + 1) % count] * g
                env = 0.5 + 0.5 * sin(ek * x + ew * t + ep)
                env = b.floor + (1 - b.floor) * env * env
                surge = sin(sk * x - sw * t + sp)
                surge = 1.0 + 0.6 * surge ** 8 if surge > 0 else 1.0
                rays = (0.62 + 0.38 * sin(rk * x + rw * t + rp) * sin(rk2 * x - rw2 * t)) * (0.65 + 0.7 * fine)
                a = b.gain * env * rays * surge
                if a < 0.03:
                    continue
                edge = (b.base + a1 * sin(k1 * x + w1 * t + p1) + a2 * sin(k2 * x - w2 * t + p2)) * bh
                tall = b.height * bh * (0.78 + 0.22 * sin(k3 * x + w3 * t + p3))
                # Each ray twice a column wide at half strength, so neighbours blend and no column's
                # edge shows.
                q.setOpacity(0.5 * (a if a < 1.0 else 1.0))
                q.drawImage(QRectF(i - step * 0.5, edge - tall, step * 2, tall / RAY_EDGE), ray)
        q.end()

    def paint(self, p: QPainter) -> None:
        if self.static is not None:
            p.drawPixmap(0, 0, self.static)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        super().paint_moving(p)                           # the stars, and the odd shooting star
        if self.lights is None:
            return
        if self.dirty:
            self._render_lights()
            self.dirty = False
        w, h, hz = self.w, self.h, self.horizon
        p.save()
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
        p.drawImage(QRectF(0, 0, w, hz), self.lights)
        p.restore()
        p.drawPixmap(QPointF(0, self.land_top), self.land)
        # The lake mirrors the sky, squeezed the way a reflection seen at a low angle is.
        p.save()
        p.setClipRect(QRectF(0, hz, w, h - hz))
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
        p.setOpacity(0.32)
        p.translate(0, hz)
        p.scale(1, -1 / 3.0)
        p.drawImage(QRectF(0, -hz, w, hz), self.lights)
        p.restore()
        p.drawPixmap(QPointF(0, self.shore_top), self.shore)
        # Glints where the water catches the light.
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
        t = self.time
        pen = QPen(_c("#bfffe4"), 1.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        for g in self.glints:
            k = math.sin(math.pi * g.age / g.life)
            p.setOpacity(0.55 * k * k * (0.7 + 0.3 * math.sin(t * 9 + g.phase)))
            p.drawLine(QPointF(g.x - g.length / 2, g.y), QPointF(g.x + g.length / 2, g.y))
        p.setOpacity(1.0)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)


def _spruce(path: QPainterPath, x: float, base: float, tall: float) -> None:
    """A spruce's outline, tiers of boughs widening downward, added to ``path``."""
    wide = tall * 0.36
    tiers = 4
    right = [QPointF(x, base - tall)]
    for k in range(1, tiers + 1):
        y = base - tall + tall * k / (tiers + 0.35)
        half = wide * 0.5 * (0.2 + 0.8 * k / tiers)
        right.append(QPointF(x + half, y))
        if k < tiers:
            right.append(QPointF(x + half * 0.35, y - tall * 0.03))
    right.append(QPointF(x + wide * 0.06, base))
    left = [QPointF(2 * x - pt.x(), pt.y()) for pt in reversed(right[1:])]
    path.addPolygon(QPolygonF(right + left))
    path.closeSubpath()


# -- Factory ------------------------------------------------------------------------------

class _Gear:
    __slots__ = ("x", "y", "teeth", "module", "pitch", "angle", "speed", "holes", "style", "far", "sprite", "half",
                 "sheen")

    @property
    def step(self) -> float:
        """The angle from one tooth to the next."""
        return math.tau / self.teeth


def gear_path(teeth: int, pitch: float, module: float, holes: int = 0) -> QPainterPath:
    """A spur gear's outline, tooth ``k`` centred on the angle ``k`` teeth round, with round
    lightening holes cut through its web. A tooth is a little narrower than the gap between
    two, as on a real gear, so meshing teeth never touch."""
    tip, root = pitch + module, pitch - 1.25 * module
    step = math.tau / teeth
    pts = []
    for k in range(teeth):
        c = k * step
        for frac, r in ((-0.30, root), (-0.13, tip), (0.13, tip), (0.30, root), (0.5, root)):
            a = c + frac * step
            pts.append(QPointF(r * math.cos(a), r * math.sin(a)))
    path = QPainterPath()
    path.addPolygon(QPolygonF(pts))
    path.closeSubpath()
    path.setFillRule(Qt.FillRule.OddEvenFill)
    for i in range(holes):
        a = math.tau * (i + 0.5) / holes
        path.addEllipse(QPointF(math.cos(a) * root * 0.56, math.sin(a) * root * 0.56), root * 0.18, root * 0.18)
    return path


def driver(teeth: int, module: float, x: float, y: float, speed: float, angle: float = 0.0, **kw) -> _Gear:
    """A gear turning of itself, ``speed`` radians a second, clockwise on screen when positive."""
    g = _Gear()
    g.teeth, g.module, g.pitch = teeth, module, module * teeth / 2
    g.x, g.y, g.speed, g.angle = x, y, speed, angle
    g.holes, g.style, g.far = kw.get("holes", 0), kw.get("style", "steel"), kw.get("far", False)
    g.sprite, g.half, g.sheen = None, 0.0, None
    return g


def mesh(other: _Gear, teeth: int, direction: float, **kw) -> _Gear:
    """A gear driven by ``other``, set ``direction`` radians round from it.

    Same tooth size, so their pitch circles touch; turning the other way, at the speed its
    size gives; and set so that where they meet, a tooth of one sits in a gap of the other.
    If ``other``'s tooth is a part ``f`` of a tooth past the contact line, this gear's gap
    centre has to be the same part past it on its side, which puts this gear's own tooth
    phase there at a half minus ``f``. Both then turn by the same arc a second, so they
    stay meshed for good.
    """
    g = driver(teeth, other.module, 0.0, 0.0, 0.0, **kw)
    d = other.pitch + g.pitch
    g.x, g.y = other.x + d * math.cos(direction), other.y + d * math.sin(direction)
    g.speed = -other.speed * other.pitch / g.pitch
    f = ((direction - other.angle) / other.step) % 1.0
    g.angle = direction + math.pi - (0.5 - f) * g.step
    return g


class _Belt:
    __slots__ = ("y", "r", "speed", "far", "crates", "gap", "sprites", "tread", "caps")


class _Crate:
    __slots__ = ("x", "w", "h", "sprite")


class _Mote:
    __slots__ = ("x", "y", "vx", "vy", "r", "phase", "speed")


class Factory(Scene):
    """A factory wall: riveted steel, pipes along the top, light falling in from skylights.

    Two gear trains turn in the corners and a larger one far back, all meshing properly
    (see :func:`mesh`). A conveyor runs across the bottom and a hung one further back, each
    carrying crates, their rollers turning at the speed the belt moves. Dust drifts through
    the light, a gauge's needle trembles on the pipe, and a stack light by the belt shows
    green, with amber now and then. The count of moving things is the dust.
    """

    count = 70

    def __init__(self, t, amount=1.0, seed=11):
        super().__init__(t, amount, seed)
        self.gears: list[_Gear] = []
        self.belts: list[_Belt] = []
        self.shafts: list[tuple[float, float, float]] = []
        self.front: QPixmap | None = None
        self.mote = _sprite(_c("#fff4d6", 1.0), _c("#ffe2a8", 0.45), 32, 0.12)
        self.lamp_glow = {k: _sprite(_c(c, 0.95), _c(c, 0.40), 48, 0.10)
                          for k, c in (("green", "#5dff8f"), ("amber", "#ffb52e"))}
        self.steam_puffs = [_puff("#b9c0c8", "#f4f6f8", 90 + i) for i in range(3)]
        self.steam: list[list[float]] = []            # [x, y, r, age, life, vx, vy, sprite]
        self.venting = 0.0                             # how much longer the valve blows
        self.next_vent = self.rng.uniform(2.0, 5.0)
        self.valve = (0.0, 0.0)

    # -- where everything is ------------------------------------------------------------
    def _build(self) -> None:
        w, h = self.w, self.h
        rad = math.radians
        m = h * 0.0125
        a1 = driver(30, m, w * 0.035, h * 0.80, 0.22, holes=6)
        a2 = mesh(a1, 16, rad(-50), style="brass")
        a3 = mesh(a2, 22, rad(12), holes=5)
        mb = h * 0.011
        b1 = driver(26, mb, w * 0.965, h * 0.16, -0.30, angle=0.4, holes=5)
        b2 = mesh(b1, 12, rad(160), style="brass")
        b3 = mesh(b2, 18, rad(215))
        mf = h * 0.0105
        f1 = driver(40, mf, w * 0.60, h * 0.64, 0.10, holes=6, far=True)
        f2 = mesh(f1, 20, rad(-32), far=True)
        f3 = mesh(f1, 14, rad(150), far=True)
        self.gears = [f1, f2, f3, a1, a2, a3, b1, b2, b3]
        self.pairs = [(a1, a2), (a2, a3), (b1, b2), (b2, b3), (f1, f2), (f1, f3)]
        for g in self.gears:
            g.sprite = self._gear_sprite(g)
            g.sheen = self._sheen_sprite(g)

        self.belts = [self._belt(h * 0.30, h * 0.018, -h * 0.045, far=True),
                      self._belt(h * 0.83, h * 0.030, h * 0.070, far=False)]
        self.floor = h * 0.955
        # Skylight beams: where they start along the top, how wide, how strong.
        self.shafts = [(w * 0.16, w * 0.055, 0.8), (w * 0.47, w * 0.085, 1.0), (w * 0.80, w * 0.045, 0.6)]
        self.tilt = math.radians(24)

    def _belt(self, y: float, r: float, speed: float, far: bool) -> _Belt:
        b = _Belt()
        b.y, b.r, b.speed, b.far = y, r, speed, far
        b.crates, b.gap = [], 0.0
        tread = max(4.0, r * 0.9)
        band = max(1.0, r * 0.45)
        b.tread = _layer(tread, band, self.dpr)
        q = QPainter(b.tread)
        q.fillRect(QRectF(0, 0, tread, band), _c(self.t["rubber"]))
        q.fillRect(QRectF(0, 0, max(1.0, tread * 0.18), band), _c(self.t["steel_light"], 0.30))
        q.fillRect(QRectF(tread * 0.18, 0, max(0.6, tread * 0.08), band), _c("#000000", 0.45))
        q.end()
        b.caps = max(18.0, self.w * (0.12 if far else 0.09))
        b.sprites = []
        base = r * 2.8
        for kind, tall in (("wood", 1.0), ("card", 0.85), ("steel", 1.1), ("mark", 0.8)):
            for wide in (1.05, 1.4):
                ch = base * tall
                b.sprites.append(self._crate_sprite(kind, ch * wide, ch, far))
        return b

    # -- sprites ----------------------------------------------------------------------------
    def _gear_sprite(self, g: _Gear) -> QPixmap:
        t = self.t
        tip, root = g.pitch + g.module, g.pitch - 1.25 * g.module
        g.half = tip + 2
        pm = _layer(2 * g.half, 2 * g.half, self.dpr)
        q = QPainter(pm)
        q.setRenderHint(QPainter.RenderHint.Antialiasing)
        q.translate(g.half, g.half)
        if g.style == "brass":
            dark, mid, light = t["brass_dark"], t["brass"], t["brass_light"]
        else:
            dark, mid, light = t["steel_dark"], t["steel"], t["steel_light"]
        # Shading that is the same all the way round, so it still looks right as the gear turns.
        body = QRadialGradient(QPointF(0, 0), tip)
        body.setColorAt(0.0, _c(mid))
        body.setColorAt(0.55, _c(dark))
        body.setColorAt(0.80, _c(mid))
        body.setColorAt(1.0, _c(light))
        q.setPen(QPen(_c(light, 0.75), max(0.8, g.module * 0.12)))
        q.setBrush(body)
        q.drawPath(gear_path(g.teeth, g.pitch, g.module, g.holes))
        q.setBrush(Qt.BrushStyle.NoBrush)
        q.setPen(QPen(_c(dark, 0.9), max(1.0, root * 0.05)))
        q.drawEllipse(QPointF(0, 0), root * 0.84, root * 0.84)
        hub = root * (0.30 if g.holes else 0.42)
        hg = QRadialGradient(QPointF(0, 0), hub)
        hg.setColorAt(0.0, _c(light))
        hg.setColorAt(0.7, _c(mid))
        hg.setColorAt(1.0, _c(dark))
        q.setPen(QPen(_c(dark), max(0.8, hub * 0.08)))
        q.setBrush(hg)
        q.drawEllipse(QPointF(0, 0), hub, hub)
        q.setPen(Qt.PenStyle.NoPen)
        q.setBrush(_c(dark))
        bolts = 4 if g.teeth >= 20 else 3
        for i in range(bolts):
            a = math.tau * (i + 0.5) / bolts
            q.drawEllipse(QPointF(math.cos(a) * hub * 0.64, math.sin(a) * hub * 0.64), hub * 0.11, hub * 0.11)
        axle = hub * 0.34
        q.setBrush(_c("#0a0c0f"))
        q.drawEllipse(QPointF(0, 0), axle, axle)
        q.drawRect(QRectF(axle * 0.55, -axle * 0.28, axle * 0.7, axle * 0.56))      # the keyway: it shows the turn
        if g.far:
            self._dim(q, QRectF(-g.half, -g.half, 2 * g.half, 2 * g.half), 0.68)
        q.end()
        return pm

    def _sheen_sprite(self, g: _Gear) -> QPixmap:
        """The light on a gear's face, from the skylights up to the left. It does not turn with the
        gear, so it is drawn over it separately; it stays inside the round of the body, which looks
        the same at any angle, so it never falls on the wall between the teeth."""
        root = g.pitch - 1.25 * g.module
        pm = _layer(2 * g.half, 2 * g.half, self.dpr)
        q = QPainter(pm)
        q.setRenderHint(QPainter.RenderHint.Antialiasing)
        q.translate(g.half, g.half)
        r = root * 0.97
        light = QLinearGradient(QPointF(-r * 0.7, -r * 0.7), QPointF(r * 0.7, r * 0.7))
        light.setColorAt(0.0, _c("#fff1d6", 0.16 if g.far else 0.22))
        light.setColorAt(0.45, _c("#fff1d6", 0.0))
        light.setColorAt(0.6, _c("#000000", 0.0))
        light.setColorAt(1.0, _c("#000000", 0.30))
        q.setPen(Qt.PenStyle.NoPen)
        q.setBrush(light)
        q.drawEllipse(QPointF(0, 0), r, r)
        # A bevel on the rim: bright where it faces the light, dark opposite.
        rim = QRectF(-root * 0.84, -root * 0.84, root * 1.68, root * 1.68)
        q.setBrush(Qt.BrushStyle.NoBrush)
        q.setPen(QPen(_c("#ffffff", 0.22), max(0.8, root * 0.03), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        q.drawArc(rim, 100 * 16, 80 * 16)
        q.setPen(QPen(_c("#000000", 0.35), max(0.8, root * 0.03), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        q.drawArc(rim, 280 * 16, 80 * 16)
        q.end()
        return pm

    def _dim(self, q: QPainter, rect: QRectF, amount: float) -> None:
        """Sink what is drawn into the haze of the room, for things further back."""
        q.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceAtop)
        q.fillRect(rect, _c(self.t["grad_top"], amount))
        q.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)

    def _crate_sprite(self, kind: str, cw: float, ch: float, far: bool) -> tuple[QPixmap, float, float]:
        t = self.t
        pm = _layer(cw, ch, self.dpr)
        q = QPainter(pm)
        q.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(0.5, 0.5, cw - 1, ch - 1)
        line = max(0.8, ch * 0.035)

        def body(top, bottom, edge):
            g = QLinearGradient(0, 0, 0, ch)
            g.setColorAt(0.0, _c(top))
            g.setColorAt(1.0, _c(bottom))
            q.setPen(QPen(_c(edge), line))
            q.setBrush(g)
            q.drawRoundedRect(rect, ch * 0.04, ch * 0.04)

        if kind == "wood":
            body("#8f5e35", "#603c20", "#3a2312")
            inset = ch * 0.11
            q.setPen(QPen(_c("#a8743f"), max(0.8, ch * 0.08)))
            q.setBrush(Qt.BrushStyle.NoBrush)
            q.drawRect(rect.adjusted(inset, inset, -inset, -inset))
            q.drawLine(QPointF(inset, ch - inset), QPointF(cw - inset, inset))
            q.setPen(QPen(_c("#3a2312", 0.6), max(0.6, ch * 0.02)))
            for k in (1, 2):
                q.drawLine(QPointF(inset, ch * k / 3), QPointF(cw - inset, ch * k / 3))
        elif kind == "card":
            body("#c49c66", "#a07845", "#6d5030")
            q.setPen(Qt.PenStyle.NoPen)
            q.setBrush(_c("#e2cfa2", 0.9))
            q.drawRect(QRectF(cw * 0.41, 0.5, cw * 0.18, ch - 1))                      # tape
            q.setBrush(_c("#f4efe4", 0.8))
            q.drawRect(QRectF(cw * 0.08, ch * 0.52, cw * 0.24, ch * 0.30))              # label
            q.setBrush(_c("#6d5030", 0.7))
            for k in range(2):
                q.drawRect(QRectF(cw * 0.11, ch * (0.58 + k * 0.11), cw * 0.17, max(0.6, ch * 0.035)))
        elif kind == "steel":
            body("#5d6c7c", "#3c4753", "#20272e")
            q.setPen(QPen(_c(t["steel_light"], 0.45), max(0.7, cw * 0.02)))
            n = max(3, int(cw / max(ch * 0.2, 1)))
            for k in range(1, n):
                x = cw * k / n
                q.drawLine(QPointF(x, ch * 0.12), QPointF(x, ch * 0.88))
            q.setPen(Qt.PenStyle.NoPen)
            q.setBrush(_c(t["hazard"]))
            s = ch * 0.26
            cx, cy = cw * 0.78, ch * 0.62
            q.drawPolygon(QPolygonF([QPointF(cx, cy - s / 2), QPointF(cx + s / 2, cy + s / 2),
                                     QPointF(cx - s / 2, cy + s / 2)]))
        else:
            # The house crate, stamped with a speech bubble: this factory makes language.
            body(t["hazard"], QColor(t["hazard"]).darker(135).name(), QColor(t["hazard"]).darker(200).name())
            ink = _c("#1a1400", 0.85)
            bw, bh = cw * 0.44, ch * 0.34
            bx, by = (cw - bw) / 2, ch * 0.24
            q.setPen(Qt.PenStyle.NoPen)
            q.setBrush(ink)
            q.drawRoundedRect(QRectF(bx, by, bw, bh), bh * 0.25, bh * 0.25)
            q.drawPolygon(QPolygonF([QPointF(bx + bw * 0.18, by + bh - 0.5), QPointF(bx + bw * 0.14, by + bh + ch * 0.13),
                                     QPointF(bx + bw * 0.42, by + bh - 0.5)]))
            q.setBrush(_c(t["hazard"]))
            q.drawRoundedRect(QRectF(bx + bw * 0.18, by + bh * 0.28, bw * 0.62, bh * 0.15), 1, 1)
            q.drawRoundedRect(QRectF(bx + bw * 0.18, by + bh * 0.58, bw * 0.40, bh * 0.15), 1, 1)
        if far:
            self._dim(q, QRectF(0, 0, cw, ch), 0.5)
        q.end()
        return pm, cw, ch

    # -- the still parts ------------------------------------------------------------------
    def _render_static(self) -> QPixmap:
        self._build()
        return super()._render_static()

    def draw_still(self, p: QPainter) -> None:
        w, h = self.w, self.h
        self._wall(p)
        self._beams(p)
        self._pipes(p)
        far = next(b for b in self.belts if b.far)
        layer = _layer(w, h, self.dpr)
        q = QPainter(layer)
        q.setRenderHint(QPainter.RenderHint.Antialiasing)
        for x in self._every(w * 0.24, w * 0.12):                       # it hangs from the ceiling
            q.fillRect(QRectF(x - far.r * 0.12, 0, far.r * 0.24, far.y + far.r), _c(self.t["steel"]))
        self._belt_frame(q, far)
        self._dim(q, QRectF(0, 0, w, h), 0.45)
        q.end()
        p.drawPixmap(0, 0, layer)
        # A soft shadow under each gear on the wall; round, so it need not turn.
        for g in self.gears:
            tip = g.pitch + g.module
            self.glow(p, g.x + tip * 0.05, g.y + tip * 0.08, tip * 1.12, _c("#000000", 0.35 if g.far else 0.5))
        # The floor, with its painted edge.
        floor = QLinearGradient(0, self.floor, 0, h)
        floor.setColorAt(0.0, _c("#0f1215"))
        floor.setColorAt(1.0, _c("#07090b"))
        p.fillRect(QRectF(0, self.floor, w, h - self.floor), floor)
        self._stripes(p, QRectF(0, self.floor, w, max(2.0, h * 0.012)), 0.75)
        _grain(p, w, h, 0.03, self.dpr)

    def _every(self, spacing: float, start: float):
        x = start
        while x < self.w + spacing:
            yield x
            x += spacing

    def _wall(self, p: QPainter) -> None:
        w, h = self.w, self.h
        rng = random.Random(21)
        pw, ph = w * 0.16, h * 0.22
        rivet = max(0.7, h * 0.0033)
        y = -ph * 0.35
        while y < h:
            x = -pw * (0.3 if int(y / ph) % 2 else 0.0)
            while x < w:
                plate = QRectF(x, y, pw, ph)
                shade = rng.uniform(-0.04, 0.04)
                p.fillRect(plate, _c("#ffffff" if shade > 0 else "#000000", abs(shade)))
                p.setPen(QPen(_c("#000000", 0.45), 1.2))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRect(plate)
                p.setPen(QPen(_c("#ffffff", 0.05), 1))
                p.drawLine(QPointF(x + 1.5, y + 1.5), QPointF(x + pw - 1.5, y + 1.5))
                p.setPen(Qt.PenStyle.NoPen)
                n = 6
                for k in range(n):
                    rx = x + pw * (k + 0.5) / n
                    for ry in (y + rivet * 3, y + ph - rivet * 3):
                        p.setBrush(_c("#000000", 0.45))
                        p.drawEllipse(QPointF(rx + rivet * 0.3, ry + rivet * 0.4), rivet, rivet)
                        p.setBrush(_c(self.t["steel_light"], 0.35))
                        p.drawEllipse(QPointF(rx, ry), rivet, rivet)
                x += pw
            y += ph
        # Grime run down from the rivets and seams, and rust coming through here and there.
        grime = random.Random(22)
        y = -ph * 0.35
        while y < h:
            x = -pw * (0.3 if int(y / ph) % 2 else 0.0)
            while x < w:
                for _ in range(grime.randint(1, 4)):
                    sx, sy = x + pw * grime.uniform(0.05, 0.95), y + ph * grime.uniform(0.0, 0.2)
                    length, wide = ph * grime.uniform(0.2, 0.7), pw * grime.uniform(0.01, 0.035)
                    streak = QLinearGradient(0, sy, 0, sy + length)
                    streak.setColorAt(0.0, _c("#000000", grime.uniform(0.12, 0.25)))
                    streak.setColorAt(1.0, _c("#000000", 0.0))
                    p.fillRect(QRectF(sx - wide / 2, sy, wide, length), streak)
                if grime.random() < 0.35:
                    _blob(p, x + pw * grime.uniform(0.1, 0.9), y + ph * grime.uniform(0.1, 0.9),
                          ph * grime.uniform(0.08, 0.2),
                          _c("#7a4020", grime.uniform(0.08, 0.16)), grime.uniform(0.5, 1.0), grime.uniform(0, 180))
                x += pw
            y += ph
        g = QRadialGradient(QPointF(w * 0.5, h * 0.45), max(w, h) * 0.75)
        g.setColorAt(0.0, _c("#000000", 0.0))
        g.setColorAt(1.0, _c("#000000", 0.5))
        p.fillRect(QRectF(0, 0, w, h), g)

    def _beams(self, p: QPainter) -> None:
        """Light from skylights above, falling slantwise and fading as it goes down."""
        w, h = self.w, self.h
        layer = _layer(w, h, self.dpr)
        q = QPainter(layer)
        q.setRenderHint(QPainter.RenderHint.Antialiasing)
        s, c = math.sin(self.tilt), math.cos(self.tilt)
        length = h / c + 10
        for x0, width, strength in self.shafts:
            nx, ny = c, -s                                     # across the beam
            a, b = QPointF(x0 - 2.2 * width * nx, -2.2 * width * ny), QPointF(x0 + 2.2 * width * nx, 2.2 * width * ny)
            g = QLinearGradient(a, b)
            g.setColorAt(0.0, _c("#ffe9c2", 0.0))
            g.setColorAt(0.5, _c("#ffe9c2", 0.15 * strength))
            g.setColorAt(1.0, _c("#ffe9c2", 0.0))
            quad = QPolygonF([a, b, QPointF(b.x() + s * length, b.y() + c * length),
                              QPointF(a.x() + s * length, a.y() + c * length)])
            q.setPen(Qt.PenStyle.NoPen)
            q.setBrush(g)
            q.drawPolygon(quad)
        fade = QLinearGradient(0, 0, 0, h)
        fade.setColorAt(0.0, _c("#000000", 1.0))
        fade.setColorAt(1.0, _c("#000000", 0.2))
        q.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
        q.fillRect(QRectF(0, 0, w, h), fade)
        q.end()
        p.drawPixmap(0, 0, layer)

    def beam(self, x: float, y: float) -> float:
        """How much skylight falls on a point: what lights the dust."""
        s, c = math.sin(self.tilt), math.cos(self.tilt)
        best = 0.0
        for x0, width, strength in self.shafts:
            k = abs((x - x0) * c - y * s) / width
            if k < 2.2:
                best = max(best, strength * math.exp(-k * k) * (1 - 0.8 * y / self.h))
        return best

    def _pipe(self, p: QPainter, y: float, thick: float, dark: str, mid: str, light: str, flanges: float) -> None:
        w = self.w

        def shaded(rect: QRectF) -> None:
            g = QLinearGradient(0, rect.top(), 0, rect.bottom())
            g.setColorAt(0.0, _c(dark))
            g.setColorAt(0.32, _c(light))
            g.setColorAt(0.62, _c(mid))
            g.setColorAt(1.0, _c(dark))
            p.fillRect(rect, g)

        for x in self._every(w * 0.22, w * 0.07):                      # brackets up to the ceiling
            p.fillRect(QRectF(x - thick * 0.12, 0, thick * 0.24, y), _c(self.t["steel_dark"]))
        shaded(QRectF(0, y - thick / 2, w, thick))
        for x in self._every(flanges, flanges * 0.5):
            shaded(QRectF(x - thick * 0.2, y - thick * 0.68, thick * 0.4, thick * 1.36))

    def _pipes(self, p: QPainter) -> None:
        w, h, t = self.w, self.h, self.t
        self._pipe(p, h * 0.055, h * 0.036, t["steel_dark"], t["steel"], t["steel_light"], w * 0.21)
        self._pipe(p, h * 0.105, h * 0.018, t["brass_dark"], t["brass"], t["brass_light"], w * 0.17)
        # A pressure gauge on a stem under the big pipe.
        gx, r = w * 0.40, h * 0.028
        gy = h * 0.055 + h * 0.018 + h * 0.022 + r
        p.fillRect(QRectF(gx - r * 0.12, h * 0.07, r * 0.24, gy - h * 0.07), _c(t["steel"]))
        p.setPen(QPen(_c(t["steel_light"]), max(1.0, r * 0.16)))
        p.setBrush(_c("#d8d3c4"))
        p.drawEllipse(QPointF(gx, gy), r, r)
        p.setPen(QPen(_c("#c43b30"), max(1.0, r * 0.14)))
        p.setBrush(Qt.BrushStyle.NoBrush)
        span = QRectF(gx - r * 0.72, gy - r * 0.72, r * 1.44, r * 1.44)
        p.drawArc(span, int(-45 * 16), int(50 * 16))                     # the red end of the dial
        p.setPen(QPen(_c("#2a2a2a"), max(0.6, r * 0.06)))
        for k in range(9):
            a = math.radians(225 - k * 270 / 8)
            p.drawLine(QPointF(gx + math.cos(a) * r * 0.62, gy - math.sin(a) * r * 0.62),
                       QPointF(gx + math.cos(a) * r * 0.80, gy - math.sin(a) * r * 0.80))
        self.gauge = (gx, gy, r)
        # A valve on the big pipe, its handwheel towards us and an outlet underneath that lets
        # off steam now and then.
        vx, vy = w * 0.285, h * 0.055
        size = h * 0.014
        stub = QRectF(vx - size * 0.3, vy + h * 0.012, size * 0.6, size * 1.6)
        sg = QLinearGradient(stub.left(), 0, stub.right(), 0)
        sg.setColorAt(0.0, _c(t["steel_light"]))
        sg.setColorAt(1.0, _c(t["steel_dark"]))
        p.setPen(QPen(_c("#0a0c0f", 0.7), 1))
        p.setBrush(sg)
        p.drawRect(stub)
        p.drawRect(QRectF(stub.left() - size * 0.15, stub.bottom() - size * 0.3, size * 0.9, size * 0.3))
        body = QRectF(vx - size * 0.9, vy - size * 0.9, size * 1.8, size * 1.8)
        bg = QRadialGradient(QPointF(vx - size * 0.3, vy - size * 0.3), size * 1.4)
        bg.setColorAt(0.0, _c(t["steel_light"]))
        bg.setColorAt(1.0, _c(t["steel_dark"]))
        p.setBrush(bg)
        p.drawRoundedRect(body, size * 0.3, size * 0.3)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(_c("#b3261e"), max(1.2, size * 0.2)))
        p.drawEllipse(QPointF(vx, vy), size * 0.75, size * 0.75)                 # the handwheel
        p.setPen(QPen(_c("#8a1d17"), max(0.8, size * 0.12)))
        for k in range(4):
            a = math.radians(45 + k * 90)
            p.drawLine(QPointF(vx, vy), QPointF(vx + math.cos(a) * size * 0.72, vy + math.sin(a) * size * 0.72))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_c("#d8d3c4"))
        p.drawEllipse(QPointF(vx, vy), size * 0.16, size * 0.16)
        self.valve = (stub.center().x() + size * 0.3, stub.bottom())

    def _stripes(self, p: QPainter, rect: QRectF, alpha: float) -> None:
        """Hazard stripes, yellow and black, slanting."""
        p.save()
        p.setClipRect(rect)
        p.fillRect(rect, _c("#111111", alpha))
        band = max(3.0, rect.height() * 1.1)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_c(self.t["hazard"], alpha))
        x = rect.left() - rect.height()
        while x < rect.right() + band:
            p.drawPolygon(QPolygonF([QPointF(x, rect.bottom()), QPointF(x + band, rect.bottom()),
                                     QPointF(x + band + rect.height(), rect.top()),
                                     QPointF(x + rect.height(), rect.top())]))
            x += band * 2
        p.restore()

    def _belt_frame(self, p: QPainter, b: _Belt) -> None:
        """The side of a conveyor: the belt's top run, the side plate over the rollers, its legs."""
        w, t = self.w, self.t
        band = max(1.0, b.r * 0.45)
        p.fillRect(QRectF(0, b.y, w, band), _c(t["rubber"]))
        plate = QRectF(0, b.y + band, w, 2 * b.r - band)
        g = QLinearGradient(0, plate.top(), 0, plate.bottom())
        g.setColorAt(0.0, _c(t["steel_light"]))
        g.setColorAt(0.25, _c(t["steel"]))
        g.setColorAt(1.0, _c(t["steel_dark"]))
        p.fillRect(plate, g)
        p.setPen(QPen(_c("#000000", 0.5), 1))
        p.drawLine(QPointF(0, plate.bottom()), QPointF(w, plate.bottom()))
        if not b.far:
            self._stripes(p, QRectF(0, b.y + b.r * 1.28, w, b.r * 0.5), 0.85)
            for x in self._every(w * 0.17, w * 0.085):
                leg = QRectF(x - b.r * 0.22, plate.bottom(), b.r * 0.44, self.floor - plate.bottom())
                lg = QLinearGradient(leg.left(), 0, leg.right(), 0)
                lg.setColorAt(0.0, _c(t["steel_dark"]))
                lg.setColorAt(0.4, _c(t["steel"]))
                lg.setColorAt(1.0, _c(t["steel_dark"]))
                p.fillRect(leg, lg)
                p.fillRect(QRectF(x - b.r * 0.5, self.floor - b.r * 0.18, b.r, b.r * 0.18), _c(t["steel_dark"]))

    def resize(self, w: int, h: int, dpr: float = 1.0) -> None:
        super().resize(w, h, dpr)
        near = next(b for b in self.belts if not b.far)
        self.front_top = max(0, math.floor(near.y - 1))                # the near belt, down to the floor
        self.front = _layer(self.w, self.floor - self.front_top + 2, self.dpr)
        q = QPainter(self.front)
        q.setRenderHint(QPainter.RenderHint.Antialiasing)
        q.translate(0, -self.front_top)
        self._belt_frame(q, near)
        q.end()
        for b in self.belts:
            x = self.rng.uniform(-b.r * 4, b.r * 6)
            while x < self.w:
                self._crate(b, x)
                x += b.gap
            b.gap = self.rng.uniform(0, b.r * 6)

    def _crate(self, b: _Belt, x: float | None = None) -> None:
        c = _Crate()
        c.sprite, c.w, c.h = self.rng.choice(b.sprites)
        c.x = x if x is not None else (-c.w - 2 if b.speed > 0 else self.w + 2)
        b.crates.append(c)
        b.gap = c.w + self.rng.uniform(1.0, 3.4) * b.r * 2.8 / max(self.amount, 0.3)

    # -- the moving parts ------------------------------------------------------------------
    def spawn(self, anywhere=False):
        r = self.rng
        m = _Mote()
        m.x, m.y = r.uniform(0, self.w), r.uniform(0, self.h)
        m.vx, m.vy = r.uniform(-2, 5), r.uniform(-5, 2)
        m.r = 0.6 + 1.4 * r.random() ** 2
        m.phase, m.speed = r.uniform(0, math.tau), r.uniform(0.4, 1.2)
        return m

    def keep_inside(self, m) -> None:
        m.x %= self.w
        m.y %= self.h

    def step(self, dt: float) -> None:
        super().step(dt)
        for g in self.gears:
            g.angle = (g.angle + g.speed * dt) % math.tau
        for b in self.belts:
            moved = b.speed * dt
            for c in b.crates:
                c.x += moved
            b.crates = [c for c in b.crates if -c.w - 8 < c.x < self.w + 8]
            b.gap -= abs(moved)
            if b.gap <= 0:
                self._crate(b)
        t = self.time
        for m in self.items:
            m.x = (m.x + (m.vx + 3 * math.sin(t * m.speed + m.phase)) * dt) % self.w
            m.y = (m.y + (m.vy + 2 * math.cos(t * m.speed * 0.8 + m.phase)) * dt) % self.h
        # Steam: the valve blows for a second or so every few seconds, a jet that slows, rises and spreads.
        r, h = self.rng, self.h
        self.next_vent -= dt
        if self.next_vent <= 0 and self.venting <= 0:
            self.venting = r.uniform(0.8, 1.6)
            self.next_vent = r.uniform(7.0, 14.0) / max(self.amount, 0.3)
        if self.venting > 0:
            self.venting -= dt
            for _ in range(max(1, round(dt / 0.04))):
                vx, vy = self.valve
                self.steam.append([vx, vy, h * 0.008, 0.0, r.uniform(1.6, 2.4), r.uniform(60, 110) * h / 760,
                                   r.uniform(10, 30) * h / 760, r.randrange(len(self.steam_puffs))])
        for puff in self.steam:
            puff[3] += dt
            puff[0] += puff[5] * dt
            puff[1] += puff[6] * dt
            puff[5] *= max(0.0, 1 - 1.6 * dt)
            puff[6] -= h * 0.06 * dt                 # warm: it turns and rises
            puff[2] += h * 0.045 * dt
        self.steam = [puff for puff in self.steam if puff[3] < puff[4]]

    def _paint_gear(self, p: QPainter, g: _Gear) -> None:
        p.save()
        p.translate(g.x, g.y)
        p.rotate(math.degrees(g.angle))
        p.drawPixmap(QPointF(-g.half, -g.half), g.sprite)
        p.restore()
        if g.sheen is not None:
            p.drawPixmap(QPointF(g.x - g.half, g.y - g.half), g.sheen)

    def _paint_belt(self, p: QPainter, b: _Belt) -> None:
        t = self.time
        band = max(1.0, b.r * 0.45)
        period = max(4.0, b.r * 0.9)
        p.drawTiledPixmap(QRectF(0, b.y, self.w, band), b.tread, QPointF((-b.speed * t) % period, 0))
        # Roller ends through the side plate, turning as fast as the belt runs.
        roll = b.speed * t / (b.r * 0.8)
        cap = b.r * 0.26
        cy = b.y + band + (2 * b.r - band) * 0.34
        dx, dy = math.cos(roll) * cap * 0.8, math.sin(roll) * cap * 0.8
        steel = _c(self.t["steel_light"], 0.5 if b.far else 0.9)
        slot = QPen(_c("#0a0c0f", 0.8), max(0.8, cap * 0.35))
        for x in self._every(b.caps, b.caps * 0.5):
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(steel)
            p.drawEllipse(QPointF(x, cy), cap, cap)
            p.setPen(slot)
            p.drawLine(QPointF(x - dx, cy - dy), QPointF(x + dx, cy + dy))
        for c in b.crates:
            p.drawPixmap(QPointF(c.x, b.y - c.h), c.sprite)

    def paint_moving(self, p: QPainter) -> None:
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        far_belt, near_belt = self.belts
        for g in self.gears:
            if g.far:
                self._paint_gear(p, g)
        self._paint_belt(p, far_belt)
        for g in self.gears:
            if not g.far:
                self._paint_gear(p, g)
        if self.front is not None:
            p.drawPixmap(QPointF(0, self.front_top), self.front)
        self._paint_belt(p, near_belt)
        self._paint_instruments(p)
        for x, y, size, age, life, _vx, _vy, sprite in self.steam:
            p.setOpacity(0.4 * min(1.0, age / 0.1) * (1 - age / life) ** 1.3)
            p.drawPixmap(QRectF(x - size, y - size, size * 2, size * 2), self.steam_puffs[sprite], QRectF(0, 0, 64, 64))
        p.setOpacity(1.0)
        # Dust, lit only where the skylight falls.
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
        t = self.time
        for m in self.items:
            light = self.beam(m.x, m.y)
            if light < 0.05:
                continue
            p.setOpacity(light * (0.55 + 0.45 * math.sin(t * m.speed * 2 + m.phase)))
            size = m.r * 2.6
            p.drawPixmap(QRectF(m.x - size, m.y - size, size * 2, size * 2), self.mote, QRectF(0, 0, 32, 32))
        p.setOpacity(1.0)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)

    def _paint_instruments(self, p: QPainter) -> None:
        t, h = self.time, self.h
        # The gauge's needle, trembling around the middle of the dial.
        if getattr(self, "gauge", None):
            gx, gy, r = self.gauge
            a = math.radians(225 - 270 * (0.52 + 0.06 * math.sin(t * 1.3) + 0.02 * math.sin(t * 7.1)))
            p.setPen(QPen(_c("#b3261e"), max(0.8, r * 0.09), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(QPointF(gx, gy), QPointF(gx + math.cos(a) * r * 0.7, gy - math.sin(a) * r * 0.7))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(_c("#2a2a2a"))
            p.drawEllipse(QPointF(gx, gy), r * 0.1, r * 0.1)
        # A stack light on the near belt: green while it runs, amber now and then.
        near = self.belts[1]
        x, size = self.w * 0.94, h * 0.022
        pole_top = near.y - size * 4.2
        p.fillRect(QRectF(x - size * 0.12, pole_top, size * 0.24, near.y - pole_top), _c(self.t["steel"]))
        lamps = (("red", "#e5484d", 0.0),
                 ("amber", "#ffb52e", 1.0 if (t % 5.0) < 0.9 else 0.0),
                 ("green", "#5dff8f", 0.75 + 0.25 * math.sin(t * 1.4)))
        for i, (name, colour, lit) in enumerate(lamps):
            box = QRectF(x - size * 0.7, pole_top + i * size * 1.05, size * 1.4, size)
            p.setPen(QPen(_c("#0a0c0f", 0.8), 1))
            p.setBrush(_c(colour, 0.25 + 0.75 * lit))
            p.drawRoundedRect(box, size * 0.2, size * 0.2)
            if lit > 0.05 and name in self.lamp_glow:
                glow = size * 2.6
                p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
                p.setOpacity(0.55 * lit)
                p.drawPixmap(QRectF(box.center().x() - glow, box.center().y() - glow, glow * 2, glow * 2),
                             self.lamp_glow[name], QRectF(0, 0, 48, 48))
                p.setOpacity(1.0)
                p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)


# -- Space travel ---------------------------------------------------------------------------

def _hull_path() -> QPainterPath:
    """The ship's outline, stern at 0 and nose at 1 along x, the keel line at y 0."""
    hull = QPainterPath(QPointF(0.10, -0.075))
    hull.cubicTo(QPointF(0.30, -0.105), QPointF(0.55, -0.100), QPointF(0.74, -0.070))
    hull.cubicTo(QPointF(0.86, -0.052), QPointF(0.95, -0.024), QPointF(1.00, 0.0))
    hull.cubicTo(QPointF(0.95, 0.022), QPointF(0.86, 0.040), QPointF(0.74, 0.055))
    hull.cubicTo(QPointF(0.55, 0.080), QPointF(0.30, 0.088), QPointF(0.10, 0.080))
    hull.closeSubpath()
    return hull


def _bridge_path() -> QPainterPath:
    b = QPainterPath(QPointF(0.46, -0.090))
    b.lineTo(QPointF(0.515, -0.150))
    b.lineTo(QPointF(0.635, -0.152))
    b.cubicTo(QPointF(0.665, -0.140), QPointF(0.700, -0.110), QPointF(0.735, -0.068))
    b.lineTo(QPointF(0.46, -0.080))
    b.closeSubpath()
    return b


def _engine_path() -> QPainterPath:
    e = QPainterPath()
    e.addRoundedRect(QRectF(0.03, -0.108, 0.15, 0.206), 0.018, 0.018)
    return e


def _plume_sprite() -> QPixmap:
    """An engine's exhaust, nozzle at the right: white hot there, cooling and narrowing leftward."""
    w, h = 256, 64
    pm = QPixmap(w, h)
    pm.fill(Qt.GlobalColor.transparent)
    q = QPainter(pm)
    q.setRenderHint(QPainter.RenderHint.Antialiasing)
    g = QLinearGradient(w, 0, 0, 0)
    g.setColorAt(0.0, _c("#ffffff", 1.0))
    g.setColorAt(0.08, _c("#bff4ff", 0.95))
    g.setColorAt(0.35, _c("#4fd6ff", 0.55))
    g.setColorAt(0.75, _c("#6a5cff", 0.18))
    g.setColorAt(1.0, _c("#6a5cff", 0.0))
    q.fillRect(QRectF(0, 0, w, h), g)
    q.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
    v = QLinearGradient(0, 0, 0, h)
    v.setColorAt(0.0, _c("#000000", 0.0))
    v.setColorAt(0.5, _c("#000000", 1.0))
    v.setColorAt(1.0, _c("#000000", 0.0))
    q.fillRect(QRectF(0, 0, w, h), v)
    taper = QPainterPath(QPointF(w, h * 0.05))
    taper.lineTo(QPointF(w, h * 0.95))
    taper.cubicTo(QPointF(w * 0.5, h * 0.78), QPointF(w * 0.2, h * 0.6), QPointF(0, h * 0.5))
    taper.cubicTo(QPointF(w * 0.2, h * 0.4), QPointF(w * 0.5, h * 0.22), QPointF(w, h * 0.05))
    mask = QPixmap(w, h)
    mask.fill(Qt.GlobalColor.transparent)
    m = QPainter(mask)
    m.setRenderHint(QPainter.RenderHint.Antialiasing)
    m.fillPath(taper, _c("#000000", 1.0))
    m.end()
    q.drawPixmap(0, 0, mask)
    q.end()
    return pm


class _Views:
    """The turned views of a piece of debris (see :meth:`Space._lit`), each drawn the first time
    it is wanted."""
    __slots__ = ("draw", "frames")

    def __init__(self, draw, count: int):
        self.draw = draw
        self.frames: list[QPixmap | None] = [None] * count

    def __getitem__(self, k: int) -> QPixmap:
        pm = self.frames[k]
        if pm is None:
            s = 96
            pm = QPixmap(s, s)
            pm.fill(Qt.GlobalColor.transparent)
            q = QPainter(pm)
            q.setRenderHint(QPainter.RenderHint.Antialiasing)
            q.translate(s / 2, s / 2)
            q.rotate(k * 360 / len(self.frames))
            self.draw(q)
            q.resetTransform()
            q.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceAtop)
            light = QLinearGradient(s * 0.22, s * 0.18, s * 0.78, s * 0.82)
            light.setColorAt(0.0, _c("#fff4e8", 0.38))
            light.setColorAt(0.42, _c("#fff4e8", 0.0))
            light.setColorAt(0.55, _c("#000000", 0.0))
            light.setColorAt(1.0, _c("#000000", 0.65))
            q.fillRect(QRectF(0, 0, s, s), light)
            q.end()
            self.frames[k] = pm
        return pm


_debris: dict[tuple, list[_Views]] = {}


class _Drift:
    """Something the ship passes: a star, a rock, a scrap of wreckage, a grain of dust."""
    __slots__ = ("x", "y", "vx", "vy", "size", "angle", "spin", "sprite", "phase", "base")


class Space(Scene):
    """A starship cruising to the right while the universe slides past it to the left.

    Everything passes at the speed its distance gives it. The far sky (nebula, galaxies, a
    ringed planet, faint stars) is one picture wider than the window that wraps round and
    moves a few pixels a second; nearer stars drift by as points; rocks and wreckage tumble
    past behind the ship, and bigger and quicker in front of it, smeared a little by speed;
    grains of dust streak by fastest. The ship is drawn once and bobs gently, its engines
    flickering, its lights blinking, and now and then a glint of light runs along the hull.
    """

    count = 70                 # the nearer stars
    FAR_SPEED = 2.5            # pixels a second: the far sky
    GLINT_EVERY = 11.0

    def __init__(self, t, amount=1.0, seed=11):
        super().__init__(t, amount, seed)
        self.hull = _hull_path().united(_bridge_path()).united(_engine_path())
        self.plume = _plume_sprite()
        self.star_sprite = _sprite(_c("#ffffff", 1.0), _c(t["star"], 0.5), 32, 0.12)
        self.glows = {k: _sprite(_c(c, 1.0), _c(c, 0.45), 48, 0.10)
                      for k, c in (("engine", t["engine"]), ("red", "#ff4a4a"), ("green", "#57ff9a"),
                                   ("white", "#ffffff"))}
        self.far: QPixmap | None = None
        self.span = 1
        self.rocks_far: list[_Drift] = []
        self.rocks_near: list[_Drift] = []
        self.dust: list[_Drift] = []
        # Rocks and scraps of wreckage, each in turned views lit the same way (see _lit); drawn once
        # for all the scenes with these colours, the theme picker's among them.
        key = (t["rock"], t["hull"], t["hull_light"], t["hull_dark"], t["accent"])
        if key not in _debris:
            _debris[key] = [self._rock(random.Random(100 + i)) for i in range(6)] + \
                           [self._scrap(i) for i in range(3)]
        self.debris: list[_Views] = _debris[key]
        self.ship: QPixmap | None = None
        self.length = 1.0

    # -- the far sky ------------------------------------------------------------------------
    def _render_static(self) -> QPixmap:
        """The far sky, one and a half windows wide, drawn so its two ends meet."""
        w, h = self.w, self.h
        self.span = span = max(2, math.ceil(w * 1.6))
        pm = QPixmap(QSize(max(1, math.ceil(span * self.dpr)), max(1, math.ceil(h * self.dpr))))
        pm.setDevicePixelRatio(self.dpr)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        g = QLinearGradient(0, 0, 0, h)
        g.setColorAt(0.0, _c(self.t["grad_top"]))
        g.setColorAt(1.0, _c(self.t["grad_bottom"]))
        p.fillRect(QRectF(0, 0, span, h), g)
        rng = random.Random(23)

        def band(x: float) -> float:
            u = x / span * math.tau
            return h * (0.46 + 0.17 * math.sin(u) + 0.07 * math.sin(2 * u + 1.0))

        def wrap(q: QPainter, x: float, y: float, r: float, colour: QColor, stretch: float = 1.0, turn: float = 0.0) -> None:
            for dx in (-span, 0, span):                      # so the two ends meet
                if -r < x + dx < span + r:
                    _blob(q, x + dx, y, r, colour, stretch, turn)

        tints = (self.t["nebula_a"], self.t["nebula_b"], self.t["nebula_c"])

        def nebula(q: QPainter) -> None:
            # Broad clouds, then smaller brighter wisps that give them their shape, dark lanes of
            # dust through them, and bright knots where stars are being born.
            for i in range(70):
                x = rng.uniform(0, span)
                wrap(q, x, band(x) + rng.gauss(0, h * 0.09), h * rng.uniform(0.07, 0.24),
                     _c(tints[i % 3 if i % 5 else 0], rng.uniform(0.06, 0.13)), rng.uniform(0.6, 1.0), rng.uniform(0, 180))
            for i in range(260):
                x = rng.uniform(0, span)
                wrap(q, x, band(x) + rng.gauss(0, h * 0.07), h * rng.uniform(0.012, 0.05),
                     _c(tints[rng.randrange(3)], rng.uniform(0.04, 0.10)), rng.uniform(0.25, 0.7), rng.uniform(-40, 40))
            for _ in range(34):
                x = rng.uniform(0, span)
                wrap(q, x, band(x) + rng.gauss(0, h * 0.045), h * rng.uniform(0.02, 0.07),
                     _c("#020108", rng.uniform(0.2, 0.4)), rng.uniform(0.3, 0.6), rng.uniform(-30, 30))
            for _ in range(16):
                x = rng.uniform(0, span)
                y = band(x) + rng.gauss(0, h * 0.06)
                wrap(q, x, y, h * rng.uniform(0.02, 0.045), _c("#ff8fd0", rng.uniform(0.10, 0.2)))
                wrap(q, x, y, h * rng.uniform(0.008, 0.015), _c("#ffe6f6", rng.uniform(0.25, 0.4)))

        p.save()
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.drawImage(QRectF(0, 0, span, h), _soft(span, h, nebula, 3.0))
        p.restore()
        # Stars: many faint ones, thicker along the band, in their colours; a few bright ones.
        tints = [QColor(self.t["star"]), QColor("#b9d0ff"), QColor("#ffe6c4"), QColor("#ffc9a0")]
        stars = []
        dust = int(1800 * max(0.05, min(3.0, span * h / BASE_AREA)))
        for i in range(dust):
            x = rng.uniform(0, span)
            y = band(x) + rng.gauss(0, h * 0.14) if i % 2 else rng.uniform(0, h)
            stars.append((x, y, 0.3 + 0.6 * rng.random() ** 3, rng.choices((0, 1, 2, 3), (6, 2, 2, 1))[0],
                          0.12 + 0.5 * rng.random() ** 2))
        _points(p, stars, tints)
        flares = [_flare(c) for c in tints]
        for _ in range(9):
            x, y = rng.uniform(0, span), rng.uniform(0, h)
            size = h * rng.uniform(0.012, 0.022)
            p.drawPixmap(QRectF(x - size, y - size, size * 2, size * 2), flares[rng.randrange(4)], QRectF(0, 0, 96, 96))
        for u, v, r, tilt, turn in ((0.14, 0.22, 0.075, 0.42, -18), (0.52, 0.80, 0.05, 0.55, 25),
                                    (0.83, 0.16, 0.10, 0.36, 8)):
            self._galaxy(p, span * u, h * v, h * r, tilt, turn, rng)
        self._planet(p, span * 0.36, h * 0.83, h * 0.06)
        _grain(p, span, h, 0.03, self.dpr)
        p.end()
        return pm

    def _galaxy(self, p: QPainter, x: float, y: float, r: float, tilt: float, turn: float, rng) -> None:
        p.save()
        p.translate(x, y)
        p.rotate(turn)
        p.scale(1.0, tilt)
        disk = QRadialGradient(QPointF(0, 0), r)
        disk.setColorAt(0.0, _c("#fff1d6", 0.55))
        disk.setColorAt(0.18, _c("#d8d2ff", 0.22))
        disk.setColorAt(0.6, _c("#8f8cff", 0.07))
        disk.setColorAt(1.0, _c("#8f8cff", 0.0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(disk)
        p.drawEllipse(QPointF(0, 0), r, r)
        for arm in range(2):
            for k in range(170):
                u = k / 170
                a = arm * math.pi + u * 3.3 * math.pi
                d = r * (0.1 + 0.9 * u)
                jx, jy = rng.gauss(0, r * 0.04), rng.gauss(0, r * 0.04)
                size = r * 0.03 * (1.1 - 0.7 * u) * rng.uniform(0.5, 1.3)
                pink = rng.random() < 0.07
                p.setBrush(_c("#ff9ad2" if pink else "#dfe6ff", (0.55 if pink else 0.4) * (1 - u) ** 1.1))
                p.drawEllipse(QPointF(math.cos(a) * d + jx, math.sin(a) * d + jy), size, size)
        core = QRadialGradient(QPointF(0, 0), r * 0.16)
        core.setColorAt(0.0, _c("#ffffff", 0.95))
        core.setColorAt(1.0, _c("#ffe7c4", 0.0))
        p.setBrush(core)
        p.drawEllipse(QPointF(0, 0), r * 0.16, r * 0.16)
        p.restore()

    def _planet(self, p: QPainter, x: float, y: float, r: float) -> None:
        """A ringed gas giant lit from the upper left: banded clouds, a soft terminator into its night
        side, a thin bright limb of atmosphere, and rings in several bands with a gap, passing
        behind it and in front, throwing their shadow across it."""
        tilt = -14.0

        def rings(front: bool) -> None:
            p.save()
            p.translate(x, y)
            p.rotate(tilt)
            p.setClipRect(QRectF(-r * 3, 0 if front else -r * 3, r * 6, r * 3))
            p.setBrush(Qt.BrushStyle.NoBrush)
            for scale, width, colour, alpha in ((1.42, 0.10, "#a89880", 0.35), (1.58, 0.16, "#d9c7a8", 0.55),
                                                (1.76, 0.14, "#e8d8bc", 0.62), (1.93, 0.05, "#8a7a66", 0.12),
                                                (2.05, 0.14, "#cdbb9c", 0.45), (2.22, 0.07, "#a89880", 0.25)):
                p.setPen(QPen(_c(colour, alpha * (1.0 if front else 0.8)), r * width))
                p.drawEllipse(QPointF(0, 0), r * scale, r * scale * 0.24)
            p.restore()

        rings(front=False)
        body = QPainterPath()
        body.addEllipse(QPointF(x, y), r, r)
        p.save()
        p.setClipPath(body)
        # Bands of cloud, turned with the rings.
        p.translate(x, y)
        p.rotate(tilt)
        bands = QLinearGradient(0, -r, 0, r)
        for pos, colour in ((0.0, "#e9cfa6"), (0.14, "#d9a978"), (0.24, "#efd7b0"), (0.36, "#c28a62"),
                            (0.46, "#e6c297"), (0.55, "#b77c58"), (0.63, "#dcb28a"), (0.74, "#c99a70"),
                            (0.86, "#e3c29a"), (1.0, "#b98a66")):
            bands.setColorAt(pos, _c(colour))
        p.fillRect(QRectF(-r, -r, 2 * r, 2 * r), bands)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_c("#a8604a", 0.55))
        p.drawEllipse(QPointF(r * 0.28, r * 0.32), r * 0.13, r * 0.07)                      # a great storm
        # The rings' shadow across it, a little away from the light.
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(_c("#1a0f14", 0.45), r * 0.22))
        p.drawEllipse(QPointF(r * 0.10, r * 0.14), r * 1.7, r * 1.7 * 0.24)
        p.resetTransform()
        # Night: the side away from the light falls into shadow, softly.
        shade = QRadialGradient(QPointF(x - r * 0.45, y - r * 0.40), r * 1.75)
        shade.setColorAt(0.0, _c("#000000", 0.0))
        shade.setColorAt(0.45, _c("#000000", 0.05))
        shade.setColorAt(0.70, _c("#07030c", 0.70))
        shade.setColorAt(0.9, _c("#05020a", 0.95))
        p.fillRect(QRectF(x - r, y - r, 2 * r, 2 * r), shade)
        p.restore()
        # The lit limb, and a faint haze of atmosphere round it.
        haze = QRadialGradient(QPointF(x, y), r * 1.12)
        haze.setColorAt(0.86, _c("#ffd9b0", 0.0))
        haze.setColorAt(0.9, _c("#ffd9b0", 0.18))
        haze.setColorAt(1.0, _c("#ffd9b0", 0.0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(haze)
        p.drawEllipse(QPointF(x, y), r * 1.12, r * 1.12)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(_c("#fff0dc", 0.55), max(1.0, r * 0.035), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawArc(QRectF(x - r, y - r, 2 * r, 2 * r), 95 * 16, 110 * 16)
        rings(front=True)

    # -- the ship and the debris ----------------------------------------------------------------
    def resize(self, w: int, h: int, dpr: float = 1.0) -> None:
        super().resize(w, h, dpr)
        self.length = max(40.0, min(self.w * 0.46, self.h * 1.05))
        self.ship = self._ship_sprite(self.length)
        area = max(0.4, min(2.6, self.w * self.h / BASE_AREA))
        self.rocks_far = [self._drift("far", True) for _ in range(max(1, int(11 * self.amount * area)))]
        self.rocks_near = [self._drift("near", True) for _ in range(max(1, int(3 * self.amount * area)))]
        self.dust = [self._drift("dust", True) for _ in range(int(18 * self.amount * area))]

    def _ship_sprite(self, L: float) -> QPixmap:
        t = self.t
        mx, my = 0.05 * L, 0.23 * L
        pm = _layer(L * 1.12, L * 0.44, self.dpr)
        q = QPainter(pm)
        q.setRenderHint(QPainter.RenderHint.Antialiasing)
        q.translate(mx, my)
        q.scale(L, L)

        def metal(top: float, bottom: float, light: str, mid: str, dark: str) -> QLinearGradient:
            g = QLinearGradient(0, top, 0, bottom)
            g.setColorAt(0.0, _c(light))
            g.setColorAt(0.45, _c(mid))
            g.setColorAt(1.0, _c(dark))
            return g

        def line(colour: QColor, width: float = 1.0) -> QPen:
            pen = QPen(colour, width)
            pen.setCosmetic(True)
            return pen

        hull, bridge, engine = _hull_path(), _bridge_path(), _engine_path()
        # The fin under it and the one on top go first, the hull overlaps their roots.
        wing = QPainterPath(QPointF(0.27, 0.07))
        wing.lineTo(QPointF(0.36, 0.165))
        wing.lineTo(QPointF(0.455, 0.168))
        wing.lineTo(QPointF(0.50, 0.075))
        wing.closeSubpath()
        fin = QPainterPath(QPointF(0.05, -0.10))
        fin.lineTo(QPointF(0.095, -0.192))
        fin.lineTo(QPointF(0.14, -0.192))
        fin.lineTo(QPointF(0.175, -0.10))
        fin.closeSubpath()
        for part, top, bottom in ((wing, 0.07, 0.17), (fin, -0.19, -0.10)):
            q.setPen(line(_c(t["hull_dark"], 0.9)))
            q.setBrush(metal(top, bottom, t["hull"], t["hull_dark"], "#1b1e29"))
            q.drawPath(part)
        q.setPen(line(_c(t["accent"], 0.9), 1.4))
        q.drawLine(QPointF(0.37, 0.155), QPointF(0.45, 0.157))           # a stripe on the wing
        # The pod along the belly.
        pod = QPainterPath()
        pod.addRoundedRect(QRectF(0.12, 0.052, 0.30, 0.052), 0.026, 0.026)
        q.setPen(line(_c("#141722", 0.9)))
        q.setBrush(metal(0.052, 0.104, t["hull"], t["hull_dark"], "#15171f"))
        q.drawPath(pod)
        # The hull, lit from above, with the nebula's colour thrown up on its underside.
        q.setPen(line(_c("#1a1d28", 0.9), 1.2))
        q.setBrush(metal(-0.105, 0.09, t["hull_light"], t["hull"], t["hull_dark"]))
        q.drawPath(hull)
        q.setBrush(metal(-0.152, -0.07, t["hull_light"], t["hull"], t["hull_dark"]))
        q.drawPath(bridge)
        q.setBrush(metal(-0.108, 0.10, t["hull"], t["hull_dark"], "#171a24"))
        q.drawPath(engine)
        q.save()
        q.setClipPath(hull)
        under = QLinearGradient(0, 0.0, 0, 0.09)
        under.setColorAt(0.0, _c(t["nebula_a"], 0.0))
        under.setColorAt(1.0, _c(t["nebula_a"], 0.35))
        q.fillRect(QRectF(0, 0, 1.1, 0.1), under)
        # Panel lines, and a row of lit windows.
        q.setPen(line(_c("#0d0f16", 0.35)))
        for u in (0.25, 0.38, 0.52, 0.66, 0.80, 0.90):
            q.drawLine(QPointF(u, -0.12), QPointF(u + 0.01, 0.10))
        q.drawLine(QPointF(0.1, -0.028), QPointF(1.0, -0.006))
        q.setPen(Qt.PenStyle.NoPen)
        rng = random.Random(8)
        for k in range(24):
            u = 0.27 + k * 0.019
            if rng.random() < 0.8:
                q.setBrush(_c("#bff3ff", rng.uniform(0.55, 0.95)))
                q.drawRoundedRect(QRectF(u, -0.046, 0.009, 0.007), 0.002, 0.002)
        q.restore()
        # Volume: a sheen along the upper hull, a dark chine lower down, a darker nose cap,
        # the collar where the engine block meets the hull, hatches and vents.
        q.save()
        q.setClipPath(hull)
        sheen = QPainterPath(QPointF(0.16, -0.066))
        sheen.cubicTo(QPointF(0.34, -0.090), QPointF(0.58, -0.086), QPointF(0.76, -0.058))
        sheen.cubicTo(QPointF(0.86, -0.043), QPointF(0.93, -0.022), QPointF(0.975, -0.004))
        q.setBrush(Qt.BrushStyle.NoBrush)
        q.setPen(line(_c("#ffffff", 0.28), max(2.0, L * 0.012)))
        q.drawPath(sheen)
        chine = QPainterPath(QPointF(0.18, 0.052))
        chine.cubicTo(QPointF(0.45, 0.058), QPointF(0.72, 0.042), QPointF(0.93, 0.016))
        q.setPen(line(_c("#0b0d14", 0.45), 1.3))
        q.drawPath(chine)
        cap = QLinearGradient(0.90, 0, 1.0, 0)
        cap.setColorAt(0.0, _c(t["hull_dark"], 0.0))
        cap.setColorAt(0.35, _c(t["hull_dark"], 0.85))
        cap.setColorAt(1.0, _c("#11131b", 0.95))
        q.fillRect(QRectF(0.9, -0.1, 0.12, 0.2), cap)
        q.setPen(line(_c("#0d0f16", 0.55)))
        for u, v, ww, hh in ((0.33, 0.012, 0.035, 0.022), (0.70, 0.004, 0.03, 0.02), (0.84, -0.012, 0.02, 0.014)):
            q.drawRoundedRect(QRectF(u, v, ww, hh), 0.004, 0.004)
        for k in range(5):
            q.drawLine(QPointF(0.215 + k * 0.009, 0.030), QPointF(0.215 + k * 0.009, 0.046))
        q.restore()
        collar = QPainterPath()
        collar.addRoundedRect(QRectF(0.172, -0.092, 0.022, 0.176), 0.008, 0.008)
        q.setPen(line(_c("#0b0d14", 0.8)))
        q.setBrush(metal(-0.092, 0.084, t["hull_light"], "#4a5166", "#12141c"))
        q.drawPath(collar)
        # The accent stripe along the side.
        stripe = QPainterPath(QPointF(0.19, 0.020))
        stripe.cubicTo(QPointF(0.45, 0.030), QPointF(0.72, 0.022), QPointF(0.93, 0.008))
        q.setPen(line(_c(t["accent"], 0.95), max(1.5, L * 0.006)))
        q.setBrush(Qt.BrushStyle.NoBrush)
        q.drawPath(stripe)
        # The bridge: a raked window lit from inside, portholes along its side, an antenna.
        glass = QPainterPath(QPointF(0.628, -0.146))
        glass.lineTo(QPointF(0.668, -0.121))
        glass.lineTo(QPointF(0.660, -0.112))
        glass.lineTo(QPointF(0.620, -0.138))
        glass.closeSubpath()
        gl = QLinearGradient(0.62, -0.146, 0.67, -0.112)
        gl.setColorAt(0.0, _c("#e6fbff"))
        gl.setColorAt(1.0, _c(t["engine"]))
        q.setPen(Qt.PenStyle.NoPen)
        q.setBrush(gl)
        q.drawPath(glass)
        q.setBrush(_c("#bff3ff", 0.8))
        for k in range(6):
            q.drawEllipse(QPointF(0.53 + k * 0.014, -0.128), 0.0035, 0.0035)
        q.setPen(line(_c(t["hull_light"], 0.8)))
        q.drawLine(QPointF(0.56, -0.150), QPointF(0.555, -0.182))
        q.drawLine(QPointF(0.548, -0.170), QPointF(0.564, -0.170))
        dome = QRadialGradient(QPointF(0.588, -0.16), 0.02)
        dome.setColorAt(0.0, _c("#ffffff"))
        dome.setColorAt(1.0, _c(t["hull"]))
        q.setPen(line(_c("#1a1d28", 0.8)))
        q.setBrush(dome)
        q.drawChord(QRectF(0.572, -0.166, 0.032, 0.028), 0, 180 * 16)          # a sensor dome
        # The ship's name, small on the bow.
        q.save()
        q.resetTransform()
        font = q.font()
        font.setPixelSize(max(6, round(L * 0.021)))
        font.setBold(True)
        font.setLetterSpacing(font.SpacingType.AbsoluteSpacing, max(0.5, L * 0.002))
        q.setFont(font)
        q.setPen(_c("#2b3042", 0.85))
        q.drawText(QPointF(mx + 0.755 * L, my - 0.016 * L), "LM-01")
        q.restore()
        # The engine block's machinery, and the nozzles.
        q.setPen(Qt.PenStyle.NoPen)
        for k, (u, v, ww, hh) in enumerate(((0.05, -0.09, 0.05, 0.03), (0.11, -0.085, 0.04, 0.05),
                                            (0.05, 0.03, 0.035, 0.05), (0.10, 0.045, 0.05, 0.03),
                                            (0.06, -0.04, 0.09, 0.012))):
            q.setPen(line(_c(t["hull_light"], 0.35)) if k % 2 else Qt.PenStyle.NoPen)
            q.setBrush(_c(t["hull"] if k % 2 else "#12141c", 0.22 if k % 2 else 0.5))
            q.drawRoundedRect(QRectF(u, v, ww, hh), 0.004, 0.004)
        q.setPen(line(_c("#0b0d14", 0.5)))
        for k in range(6):                                       # cooling ribs
            q.drawLine(QPointF(0.045, -0.02 + k * 0.008), QPointF(0.16, -0.02 + k * 0.008))
        q.setPen(Qt.PenStyle.NoPen)
        for v in (-0.055, 0.050):
            bell = QPainterPath(QPointF(0.035, v - 0.026))
            bell.lineTo(QPointF(0.035, v + 0.026))
            bell.lineTo(QPointF(0.0, v + 0.037))
            bell.lineTo(QPointF(0.0, v - 0.037))
            bell.closeSubpath()
            q.setBrush(metal(v - 0.037, v + 0.037, "#6d7488", "#2a2e3c", "#0c0d12"))
            q.drawPath(bell)
        # Rim light along the top, from a star somewhere above.
        rim = QPainterPath(QPointF(0.10, -0.075))
        rim.cubicTo(QPointF(0.30, -0.105), QPointF(0.55, -0.100), QPointF(0.74, -0.070))
        rim.cubicTo(QPointF(0.86, -0.052), QPointF(0.95, -0.024), QPointF(1.00, 0.0))
        q.setPen(line(_c("#ffffff", 0.55), 1.3))
        q.setBrush(Qt.BrushStyle.NoBrush)
        q.drawPath(rim)
        q.drawLine(QPointF(0.515, -0.150), QPointF(0.635, -0.152))
        q.end()
        self.ship_origin = (mx, my)
        return pm

    FRAMES = 16                # turned views drawn of each piece of debris

    def _lit(self, draw) -> "_Views":
        """A piece of debris in :data:`FRAMES` turned views, each lit from the upper left where the
        light is. ``draw`` paints it centred on the origin, in a 96-pixel square. Each view is then
        shown turned by only what is left over, so however it tumbles, its light stays put."""
        return _Views(draw, self.FRAMES)

    def _rock(self, rng) -> "_Views":
        s = 96
        n = rng.randint(9, 14)
        pts = []
        for i in range(n):
            a = math.tau * (i + rng.uniform(-0.25, 0.25)) / n
            r = s * 0.42 * rng.uniform(0.62, 1.0)
            pts.append(QPointF(math.cos(a) * r, math.sin(a) * r))
        path = QPainterPath((pts[-1] + pts[0]) / 2)
        for i, pt in enumerate(pts):
            nxt = pts[(i + 1) % n]
            path.quadTo(pt, (pt + nxt) / 2)
        craters = [(rng.uniform(-0.25, 0.25) * s, rng.uniform(-0.25, 0.25) * s, rng.uniform(0.04, 0.1) * s)
                   for _ in range(rng.randint(3, 6))]
        rock = QColor(self.t["rock"])

        def draw(q: QPainter) -> None:
            body = QRadialGradient(QPointF(0, 0), s * 0.5)
            body.setColorAt(0.0, rock.lighter(118))
            body.setColorAt(0.7, rock)
            body.setColorAt(1.0, rock.darker(170))
            q.setPen(QPen(rock.darker(260), 1.2))
            q.setBrush(body)
            q.drawPath(path)
            q.setClipPath(path)
            q.setPen(Qt.PenStyle.NoPen)
            for cx, cy, r in craters:
                q.setBrush(_c(rock.darker(200).name(), 0.6))
                q.drawEllipse(QPointF(cx, cy), r, r * 0.8)
                q.setBrush(_c(rock.lighter(130).name(), 0.3))
                q.drawEllipse(QPointF(cx + r * 0.2, cy + r * 0.25), r * 0.7, r * 0.45)

        return self._lit(draw)

    def _scrap(self, kind: int) -> "_Views":
        s = 96
        t = self.t

        def draw(q: QPainter) -> None:
            if kind == 0:                                    # a torn hull plate
                pts = [QPointF(-0.38 * s, -0.2 * s), QPointF(0.3 * s, -0.26 * s), QPointF(0.36 * s, 0.0),
                       QPointF(0.22 * s, 0.06 * s), QPointF(0.3 * s, 0.2 * s), QPointF(-0.32 * s, 0.24 * s)]
                g = QLinearGradient(0, -0.26 * s, 0, 0.24 * s)
                g.setColorAt(0.0, _c(t["hull_light"]))
                g.setColorAt(1.0, _c(t["hull_dark"]))
                q.setPen(QPen(_c("#15171f"), 1.2))
                q.setBrush(g)
                q.drawPolygon(QPolygonF(pts))
                q.setPen(QPen(_c(t["accent"], 0.9), 3))
                q.drawLine(QPointF(-0.3 * s, 0.1 * s), QPointF(0.2 * s, 0.07 * s))
                q.setPen(QPen(_c("#0d0f16", 0.5), 1))
                for u in (-0.15, 0.1):
                    q.drawLine(QPointF(u * s, -0.22 * s), QPointF(u * s + 0.02 * s, 0.22 * s))
            elif kind == 1:                                  # a length of truss
                q.setPen(QPen(_c(t["hull"]), 2.2))
                q.setBrush(Qt.BrushStyle.NoBrush)
                q.drawRect(QRectF(-0.42 * s, -0.07 * s, 0.84 * s, 0.14 * s))
                for k in range(6):
                    x = -0.42 * s + k * 0.14 * s
                    q.drawLine(QPointF(x, -0.07 * s), QPointF(x + 0.14 * s, 0.07 * s))
            else:                                            # half a solar panel
                q.setPen(QPen(_c("#8b93a8"), 1.5))
                q.setBrush(_c("#1d2a5c"))
                q.drawRect(QRectF(-0.36 * s, -0.22 * s, 0.6 * s, 0.44 * s))
                q.setPen(QPen(_c("#6f86d6", 0.7), 1))
                for k in range(1, 4):
                    q.drawLine(QPointF(-0.36 * s + k * 0.15 * s, -0.22 * s), QPointF(-0.36 * s + k * 0.15 * s, 0.22 * s))
                q.drawLine(QPointF(-0.36 * s, 0), QPointF(0.24 * s, 0))
                q.setPen(QPen(_c("#8b93a8"), 3))
                q.drawLine(QPointF(0.24 * s, 0), QPointF(0.42 * s, 0.05 * s))

        return self._lit(draw)

    def _drift(self, kind: str, anywhere: bool = False) -> _Drift:
        r, w, h = self.rng, self.w, self.h
        d = _Drift()
        scale = h / 760
        if kind == "far":
            d.size = r.uniform(4, 13) * scale
            d.vx = -r.uniform(35, 80) * scale
        elif kind == "near":
            d.size = r.uniform(18, 46) * scale
            d.vx = -r.uniform(140, 260) * scale
        else:
            d.size = r.uniform(0.6, 1.4)
            d.vx = -r.uniform(380, 760) * scale
        d.x = r.uniform(-d.size, w + d.size) if anywhere else w + d.size + r.uniform(0, w * 0.3)
        d.y = r.uniform(-0.05, 1.05) * h
        d.vy = r.uniform(-6, 6) * scale
        d.angle, d.spin = r.uniform(0, 360), r.uniform(-40, 40)
        d.sprite = r.randrange(len(self.debris)) if self.debris else 0
        d.phase, d.base = r.uniform(0, math.tau), r.uniform(0.35, 0.8)
        return d

    # -- the nearer stars (the moving things the amount counts) ---------------------------------------
    def spawn(self, anywhere=False):
        r = self.rng
        d = _Drift()
        near = r.random() < 0.35
        d.size = r.uniform(1.4, 2.6) if near else r.uniform(0.7, 1.5)
        d.vx = -(r.uniform(14, 24) if near else r.uniform(6, 11))
        d.x = r.uniform(0, self.w) if anywhere else self.w + 4
        d.y = r.uniform(0, self.h)
        d.vy = d.angle = d.spin = 0.0
        d.sprite = 0
        d.phase, d.base = r.uniform(0, math.tau), r.uniform(0.45, 1.0)
        return d

    def keep_inside(self, d) -> None:
        d.x %= self.w
        d.y %= self.h

    def step(self, dt: float) -> None:
        super().step(dt)
        for s in self.items:
            s.x += s.vx * dt
            if s.x < -4:
                s.x, s.y = self.w + 4, self.rng.uniform(0, self.h)
        for group, kind in ((self.rocks_far, "far"), (self.rocks_near, "near"), (self.dust, "dust")):
            for i, d in enumerate(group):
                d.x += d.vx * dt
                d.y += d.vy * dt
                d.angle += d.spin * dt
                if d.x < -d.size * 3 - 40:
                    group[i] = self._drift(kind)

    # -- painting --------------------------------------------------------------------------------
    def paint(self, p: QPainter) -> None:
        w = self.w
        if self.static is not None:
            off = int(self.time * self.FAR_SPEED) % self.span
            p.drawPixmap(QPointF(-off, 0), self.static)
            if self.span - off < w:
                p.drawPixmap(QPointF(self.span - off, 0), self.static)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        t = self.time
        for s in self.items:
            p.setOpacity(s.base * (0.7 + 0.3 * math.sin(s.phase + t * 1.7)))
            size = s.size * 2.6
            p.drawPixmap(QRectF(s.x - size, s.y - size, size * 2, size * 2), self.star_sprite, QRectF(0, 0, 32, 32))
        p.setOpacity(1.0)
        self._paint_rocks(p, self.rocks_far, dim=0.55)
        if self.ship is not None:
            self._paint_ship(p)
        self._paint_rocks(p, self.rocks_near, dim=0.0, smear=True)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
        pen = QPen(_c("#cfe6ff"), 1.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        for d in self.dust:
            p.setOpacity(d.base * 0.4)
            pen.setWidthF(d.size)
            p.setPen(pen)
            p.drawLine(QPointF(d.x, d.y), QPointF(d.x - d.vx * 0.016, d.y - d.vy * 0.016))
        p.setOpacity(1.0)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)

    def _paint_rocks(self, p: QPainter, group: list[_Drift], dim: float, smear: bool = False) -> None:
        step = 360 / self.FRAMES
        for d in group:
            frames = self.debris[d.sprite]
            size = d.size
            ghosts = ((0.024, 0.18), (0.012, 0.35), (0.0, 1.0)) if smear else ((0.0, 1.0),)
            for lag, opacity in ghosts:
                angle = (d.angle - d.spin * lag) % 360
                k = round(angle / step)
                sprite = frames[k % self.FRAMES]
                p.save()
                p.translate(d.x - d.vx * lag, d.y - d.vy * lag)
                p.rotate(angle - k * step)                 # the nearest view, turned the rest of the way
                p.setOpacity(opacity * (1.0 - dim))
                p.drawPixmap(QRectF(-size, -size, 2 * size, 2 * size), sprite, QRectF(0, 0, 96, 96))
                p.restore()
        p.setOpacity(1.0)

    def _paint_ship(self, p: QPainter) -> None:
        t, L = self.time, self.length
        cx, cy = self.w * 0.60, self.h * 0.50
        bob = self.h * 0.006 * math.sin(t * 0.55) + self.h * 0.002 * math.sin(t * 1.4 + 1)
        p.save()
        p.translate(cx, cy + bob)
        p.rotate(0.7 * math.sin(t * 0.37 + 0.5))
        p.translate(-0.5 * L, 0)
        # Exhaust first: the nozzles sit over its root.
        p.save()
        p.scale(L, L)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
        for i, (v, size) in enumerate(((-0.055, 1.0), (0.050, 1.0), (0.078, 0.45))):
            u = 0.12 if size < 1 else 0.0
            flicker = 1.0 + 0.07 * math.sin(t * 23 + i * 2.1) + 0.05 * math.sin(t * 41 + i)
            length = 0.30 * size * flicker
            height = 0.075 * size
            p.setOpacity(0.9)
            p.drawPixmap(QRectF(u - length, v - height / 2, length, height), self.plume, QRectF(0, 0, 256, 64))
            glow = 0.05 * size * flicker
            p.setOpacity(0.8)
            p.drawPixmap(QRectF(u - glow, v - glow, 2 * glow, 2 * glow), self.glows["engine"], QRectF(0, 0, 48, 48))
        p.setOpacity(1.0)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        p.restore()
        mx, my = self.ship_origin
        p.drawPixmap(QPointF(-mx, -my), self.ship)
        p.scale(L, L)
        # A glint running from the nose to the stern now and then.
        k = (t % self.GLINT_EVERY) / 1.6
        if k < 1.0:
            centre = 1.05 - 1.15 * k
            p.save()
            p.setClipPath(self.hull)
            g = QLinearGradient(centre - 0.06, 0, centre + 0.06, 0)
            g.setColorAt(0.0, _c("#ffffff", 0.0))
            g.setColorAt(0.5, _c("#ffffff", 0.32 * math.sin(math.pi * k)))
            g.setColorAt(1.0, _c("#ffffff", 0.0))
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
            p.fillRect(QRectF(centre - 0.06, -0.2, 0.12, 0.4), g)
            p.restore()
        # Lights: red blinking on the fin, a white strobe on the wing, green on the bridge.
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
        strobe = (t % 2.2)
        for name, (u, v), lit, size in (
                ("red", (0.117, -0.192), 1.0 if (t % 1.4) < 0.18 else 0.0, 0.03),
                ("white", (0.455, 0.168), 1.0 if strobe < 0.06 or 0.18 < strobe < 0.24 else 0.0, 0.035),
                ("green", (0.575, -0.152), 0.55 + 0.25 * math.sin(t * 2.0), 0.018)):
            if lit <= 0:
                continue
            p.setOpacity(lit)
            p.drawPixmap(QRectF(u - size, v - size, 2 * size, 2 * size), self.glows[name], QRectF(0, 0, 48, 48))
        p.setOpacity(1.0)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        p.restore()


# -- Warfare --------------------------------------------------------------------------------

def _puff(colour: str, light: str, seed: int, size: int = 64) -> QPixmap:
    """A lumpy puff of smoke, lit a little from above."""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    q = QPainter(pm)
    q.setRenderHint(QPainter.RenderHint.Antialiasing)
    q.setPen(Qt.PenStyle.NoPen)
    rng = random.Random(seed)
    for k in range(8):
        cx = size / 2 + rng.uniform(-0.17, 0.17) * size
        cy = size / 2 + rng.uniform(-0.15, 0.17) * size
        r = size * rng.uniform(0.2, 0.32)
        tint = light if k >= 6 else colour
        if k >= 6:
            cy -= size * 0.08
        g = QRadialGradient(QPointF(cx, cy), r)
        g.setColorAt(0.0, _c(tint, 0.6 if k < 6 else 0.35))
        g.setColorAt(0.6, _c(tint, 0.38 if k < 6 else 0.18))
        g.setColorAt(1.0, _c(tint, 0.0))
        q.setBrush(g)
        q.drawEllipse(QPointF(cx, cy), r, r)
    q.end()
    return pm


def _jet() -> QPainterPath:
    """A modern fighter side on, facing right, one unit long, the fuselage's middle at y 0."""
    p = QPainterPath(QPointF(0.0, -0.02))
    p.lineTo(QPointF(0.62, -0.035))
    p.cubicTo(QPointF(0.68, -0.085), QPointF(0.78, -0.085), QPointF(0.82, -0.035))       # the canopy
    p.cubicTo(QPointF(0.90, -0.02), QPointF(0.96, -0.008), QPointF(1.0, 0.0))
    p.cubicTo(QPointF(0.94, 0.015), QPointF(0.80, 0.03), QPointF(0.62, 0.04))
    p.lineTo(QPointF(0.30, 0.045))
    p.lineTo(QPointF(0.04, 0.03))
    p.lineTo(QPointF(0.0, 0.02))
    p.closeSubpath()
    shapes = QPainterPath(p)
    shapes.addPolygon(QPolygonF([QPointF(0.02, -0.02), QPointF(0.10, -0.21), QPointF(0.17, -0.21), QPointF(0.24, -0.03)]))
    shapes.addPolygon(QPolygonF([QPointF(0.22, 0.025), QPointF(0.50, 0.03), QPointF(0.58, 0.045), QPointF(0.26, 0.075)]))
    shapes.addPolygon(QPolygonF([QPointF(0.0, 0.02), QPointF(0.12, 0.02), QPointF(0.16, 0.035), QPointF(0.02, 0.05)]))
    shapes.setFillRule(Qt.FillRule.WindingFill)
    return shapes


def _helicopter() -> QPainterPath:
    """A utility helicopter side on, facing right, one unit long, without its main rotor."""
    p = QPainterPath(QPointF(0.35, -0.02))
    p.cubicTo(QPointF(0.45, -0.13), QPointF(0.80, -0.14), QPointF(0.92, -0.08))
    p.cubicTo(QPointF(1.0, -0.03), QPointF(0.99, 0.06), QPointF(0.92, 0.08))
    p.lineTo(QPointF(0.45, 0.08))
    p.cubicTo(QPointF(0.40, 0.07), QPointF(0.36, 0.04), QPointF(0.35, -0.02))
    p.closeSubpath()
    shapes = QPainterPath(p)
    shapes.addPolygon(QPolygonF([QPointF(0.02, -0.03), QPointF(0.40, -0.01), QPointF(0.40, 0.03), QPointF(0.02, 0.0)]))
    shapes.addPolygon(QPolygonF([QPointF(0.0, -0.12), QPointF(0.05, -0.12), QPointF(0.08, 0.0), QPointF(0.02, 0.0)]))
    shapes.addRect(QRectF(0.55, -0.17, 0.12, 0.04))                   # the rotor head
    shapes.addRect(QRectF(0.45, 0.13, 0.42, 0.015))                   # skids
    shapes.addRect(QRectF(0.52, 0.08, 0.015, 0.05))
    shapes.addRect(QRectF(0.78, 0.08, 0.015, 0.05))
    shapes.setFillRule(Qt.FillRule.WindingFill)
    return shapes


class _Flyer:
    """An aircraft crossing: a jet or a helicopter."""
    __slots__ = ("kind", "x", "y", "vx", "size", "phase")


class _Billow:
    __slots__ = ("t", "life", "side", "sprite")


class _Ash:
    __slots__ = ("x", "y", "vx", "vy", "r", "phase", "alpha")


class Warfare(Scene):
    """A city at dusk under a pall of smoke, seen from a hill where a tank stands.

    The sky is overcast and cold. From behind the city a black column of smoke climbs and
    spreads into a sheet across the sky, a glow of fire at its foot, and a thinner one rises
    far off. The city lies in layers fading into the haze: blocks of flats with lit windows,
    some going on and off, then houses, bare trees and street lamps. On the dry grass of the
    hill in front stands a Leopard 2A6 in three-colour camouflage, idling, its antennas
    swaying, and now and then its gun fires, away along the hill. Smoke billows up the column,
    ash drifts down, crows wheel, and now and then a pair of jets or a helicopter passes under
    the cloud. Nothing in it takes a side: no flags, no markings, no place that could be named,
    and nothing fired at.
    """

    count = 50                 # flakes of ash
    CITY = 0.665               # the foot of the blocks of flats, as a part of the height

    def __init__(self, t, amount=1.0, seed=11):
        super().__init__(t, amount, seed)
        self.smoke_puffs = [_puff("#15171b", "#50565f", 70 + i) for i in range(3)]
        self.exhaust_puff = _puff("#6f757d", "#a9aeb5", 80)
        self.fire = _sprite(_c("#ffc27a", 1.0), _c(t["fire"], 0.55), 48, 0.1)
        self.shapes = {"jet": _jet(), "heli": _helicopter()}
        self.billows: list[_Billow] = []
        self.flyers: list[_Flyer] = []
        self.exhaust: list[list[float]] = []       # [x, y, age, life, r]
        self.lamps: list[tuple] = []               # windows that go on and off
        self.birds: list[tuple] = []
        self.antennas: list[tuple[QPointF, float]] = []
        self.vent = QPointF(0, 0)
        self.tank_scale = 0.0
        self.next_air = self.rng.uniform(4.0, 12.0)
        self.next_puff = 0.0
        self.shot: float | None = None             # seconds since the gun last fired, while it matters
        self.next_shot = self.rng.uniform(6.0, 14.0)
        self.blast: list[list] = []                # the shot's smoke and dust
        self.blast_smoke = [_puff("#6f6e69", "#b3b2ab", 90 + i) for i in range(2)]
        self.blast_dust = [_puff("#5f5541", "#8f8367", 95 + i) for i in range(2)]
        self.flash = _sprite(_c("#ffffff", 1.0), _c("#ffd27a", 0.9), 48, 0.3)
        self.fireball = _sprite(_c("#fff2c8", 1.0), _c("#ff9a3a", 0.85), 48, 0.28)

    # -- where things are ----------------------------------------------------------------------
    def source(self) -> QPointF:
        """Where the smoke rises from, behind the city."""
        return QPointF(self.w * 0.31, self.h * (self.CITY - 0.02))

    def column(self, k: float) -> tuple[float, float, float]:
        """The middle of the smoke column ``k`` of the way up (0 to 1), and how wide it is there. It
        leans with the wind and flattens into a sheet at the top."""
        s = self.source()
        top = self.h * 0.13
        x = s.x() + self.w * 0.18 * k ** 1.4
        y = s.y() - (s.y() - top) * (1 - (1 - k) ** 1.2)
        return x, y, self.h * (0.075 + 0.22 * k ** 0.9)

    def crest(self, x: float) -> float:
        """How high the hill in front stands at ``x``: low on the left, higher to the right, where the
        tank is."""
        u = x / max(self.w, 1)
        k = max(0.0, min(1.0, (u - 0.2) / 0.55))
        k = k * k * (3 - 2 * k)
        return self.h * (0.835 - 0.06 * k + 0.004 * math.sin(u * 23) + 0.002 * math.sin(u * 61))

    # -- the still picture ------------------------------------------------------------------------
    def draw_still(self, p: QPainter) -> None:
        w, h, t = self.w, self.h, self.t
        sky = QLinearGradient(0, 0, 0, h * self.CITY)
        sky.setColorAt(0.0, _c(t["sky_top"]))
        sky.setColorAt(0.6, _c(t["sky_mid"]))
        sky.setColorAt(1.0, _c(t["sky_low"]))
        p.fillRect(QRectF(0, 0, w, h), sky)
        rng = random.Random(61)

        def overcast(q: QPainter) -> None:
            for level in (0.04, 0.12, 0.21, 0.31, 0.42, 0.52):
                x = -w * 0.1
                while x < w * 1.1:
                    r = h * rng.uniform(0.05, 0.11)
                    dark = rng.random() < 0.6
                    _blob(q, x, h * level + rng.gauss(0, h * 0.015), r,
                          _c("#303741" if dark else "#7a828d", rng.uniform(0.10, 0.22) if dark else rng.uniform(0.05, 0.12)),
                          rng.uniform(0.22, 0.35), rng.uniform(-4, 4))
                    x += r * rng.uniform(0.6, 1.2)

        _spread(p, _soft(w, h, overcast, 4.0), QRectF(0, 0, w, h))
        _spread(p, _soft(w, h, lambda q: self._smoke(q, rng), 2.0), QRectF(0, 0, w, h))
        self._city(p, rng)
        self._hill(p, rng)
        self._tank(p)
        # The glow of the fire on the underside of the smoke and the haze over the city.
        self.glow(p, self.source().x(), self.source().y(), h * 0.16, _c(t["fire"], 0.10))
        _vignette(p, w, h, 0.4)
        _grain(p, w, h, 0.03, self.dpr)

    def _smoke(self, q: QPainter, rng) -> None:
        """The column and the sheet it spreads into: heavy black masses rolling over each other, their
        undersides greyed by what light is left low in the sky, warmed by the fire near the foot; and
        a thinner column far off to the left."""
        w, h = self.w, self.h
        for k in (rng.random() for _ in range(50)):                     # far off, thin and grey
            x, y = w * 0.06 + w * 0.06 * k ** 1.5, h * (self.CITY - 0.03) - h * 0.36 * k
            _blob(q, x + rng.gauss(0, h * 0.012), y, h * (0.014 + 0.06 * k), _c("#4a5059", 0.28 * (1 - 0.6 * k)), 0.8)
        cx, cy, _ = self.column(1.0)
        sheet = []
        for _ in range(240):
            x = rng.uniform(-0.08, 1.1) * w
            d = min(1.0, abs(x - cx) / (w * 0.75))
            y = cy + rng.gauss(0, h * 0.06) - h * 0.03 + h * 0.06 * d * d
            sheet.append((x, y, h * rng.uniform(0.07, 0.16) * (1 - 0.3 * d), 1 - 0.55 * d))
        body = []
        for _ in range(320):
            k = rng.random() ** 0.8
            x, y, width = self.column(k)
            body.append((x + rng.gauss(0, width * 0.32), y + rng.gauss(0, width * 0.14), width * rng.uniform(0.22, 0.62), k))
        for _ in range(40):                                              # lumps rolling out at its sides
            k = rng.uniform(0.15, 0.9)
            x, y, width = self.column(k)
            side = rng.choice((-1, 1))
            body.append((x + side * width * rng.uniform(0.45, 0.7), y + rng.gauss(0, width * 0.1),
                         width * rng.uniform(0.18, 0.32), k))
        for off in (-0.035, 0.028):                                      # smaller fires feeding it from below
            for _ in range(24):
                k = rng.random() ** 1.2 * 0.3
                x, y, width = self.column(k)
                x += self.w * off * (1 - k / 0.3)
                body.append((x + rng.gauss(0, width * 0.15), y, width * rng.uniform(0.2, 0.4), k))
        # The light first, low on each mass, so the dark over it leaves it along their lower edges.
        for x, y, r, strength in sheet:
            _blob(q, x + r * 0.05, y + r * 0.35, r * 0.9, _c("#6a717b", 0.30 * strength), 0.5)
        for x, y, r, k in body:
            _blob(q, x + r * 0.2, y + r * 0.2, r * 0.9, _c("#5d646d", 0.28), 0.9)
        for x, y, r, strength in sheet:
            _blob(q, x, y, r, _c(rng.choice(("#121418", "#181b1f", "#1f2227", "#262a30")), rng.uniform(0.55, 0.8) * strength),
                  rng.uniform(0.45, 0.6), rng.uniform(-8, 8))
        for x, y, r, k in body:
            warm = k < 0.15
            _blob(q, x, y, r, _c("#3b2518" if warm else rng.choice(("#111316", "#181b1f", "#1f2328", "#282c32")),
                                 rng.uniform(0.65, 0.9)), rng.uniform(0.75, 1.0))
        # Deeper shadow inside the masses, for their depth.
        for x, y, r, strength in sheet[::3]:
            _blob(q, x, y - r * 0.15, r * 0.5, _c("#0b0c0e", 0.35 * strength), 0.5)

    def _city(self, p: QPainter, rng) -> None:
        """Blocks of flats in two rows fading into the haze, slabs and towers, windows lit here and
        there; then houses among trees coming down towards the hill, and street lamps."""
        w, h, t = self.w, self.h, self.t
        base = h * self.CITY
        # Far off: only shapes in the haze.
        far = QPainterPath()
        far.setFillRule(Qt.FillRule.WindingFill)
        x = -w * 0.02
        while x < w * 1.02:
            bw = w * rng.uniform(0.012, 0.04)
            bh = h * (rng.uniform(0.05, 0.075) if rng.random() < 0.12 else rng.uniform(0.012, 0.04))
            far.addRect(QRectF(x, base - h * 0.045 - bh, bw, bh + h * 0.05))
            x += bw * rng.uniform(0.7, 1.2)
        _fill_each(p, far, _c(t["city"], 0.5))
        haze = QLinearGradient(0, base - h * 0.14, 0, base)
        haze.setColorAt(0.0, _c(t["sky_low"], 0.0))
        haze.setColorAt(1.0, _c(t["sky_low"], 0.5))
        p.fillRect(QRectF(0, base - h * 0.14, w, h * 0.14), haze)
        self.lamps = []
        self._flats(p, rng, base - h * 0.012, max(1.8, h * 0.0056), (0.65, "#8c939c", "#6f767f"), 0.5)
        mist = QLinearGradient(0, base - h * 0.06, 0, base)
        mist.setColorAt(0.0, _c(t["sky_low"], 0.0))
        mist.setColorAt(1.0, _c(t["sky_low"], 0.4))
        p.fillRect(QRectF(0, base - h * 0.06, w, h * 0.06), mist)
        self._flats(p, rng, base + h * 0.012, max(2.2, h * 0.0074), (1.0, "#7a818b", "#565c65"), 1.0)
        mist = QLinearGradient(0, base - h * 0.02, 0, base + h * 0.04)
        mist.setColorAt(0.0, _c(t["sky_low"], 0.0))
        mist.setColorAt(1.0, _c(t["sky_low"], 0.35))
        p.fillRect(QRectF(0, base - h * 0.02, w, h * 0.06), mist)
        # The land the city stands on, coming down towards the hill, darker as it nears.
        land = QLinearGradient(0, base, 0, h * 0.84)
        land.setColorAt(0.0, _c("#5d625f"))
        land.setColorAt(0.5, _c("#474c45"))
        land.setColorAt(1.0, _c("#34382f"))
        p.fillRect(QRectF(0, base + h * 0.004, w, h - base), land)
        self._suburb(p, rng, base)
        self.glow(p, self.source().x(), base, h * 0.14, _c("#ff9a52", 0.10))          # the city's lights in the haze

    def _suburb(self, p: QPainter, rng, base: float) -> None:
        """In front of the flats, houses in their gardens among trees, coming down to a road with its
        lamps, then a field with a hedge along it, before the hill. Everything stands on the ground
        at some distance, and is drawn furthest first, bigger and less hazy the nearer it is."""
        w, h, t = self.w, self.h, self.t
        top, road = base + h * 0.008, base + h * 0.09
        sky = QColor(t["sky_low"])

        def at(k: float) -> float:
            return top + (road - h * 0.012 - top) * k

        things = []
        for row in (0.05, 0.42, 0.8):
            x = -w * rng.uniform(0.02, 0.05)
            while x < w * 1.03:
                k = max(0.0, min(1.0, row + rng.uniform(-0.06, 0.08)))
                s = 0.75 + 0.75 * k
                hw = h * rng.uniform(0.028, 0.048) * s
                things.append((at(k), "house", x, hw, k))
                x += hw * rng.uniform(1.5, 2.8)
        for _ in range(int(w / max(1.0, h * 0.02))):
            k = rng.random()
            things.append((at(k) + h * 0.002, "fir" if rng.random() < 0.25 else "tree", rng.uniform(-0.02, 1.02) * w, 0.0, k))
        # The gardens: darker grass in patches, under the trees and round the houses.
        def gardens(q: QPainter) -> None:
            for _ in range(int(w / max(1.0, h * 0.012))):
                k = rng.random()
                _blob(q, rng.uniform(0, w), at(k), h * rng.uniform(0.012, 0.03) * (0.75 + 0.75 * k),
                      _c(rng.choice(("#3c4139", "#454a40", "#353a33")), rng.uniform(0.25, 0.5)), 0.3)

        _spread(p, _soft(w, h, gardens, 2.0), QRectF(0, 0, w, h))
        things.sort(key=lambda thing: thing[0])
        for y, kind, x, size, k in things:
            haze = 0.5 * (1 - k) ** 1.2
            s = 0.75 + 0.75 * k
            if kind == "house":
                self._house(p, x, y, size, haze, sky, rng)
            elif kind == "fir":
                self._fir(p, x, y, h * rng.uniform(0.03, 0.05) * s, _mix(QColor("#1f2528"), sky, haze))
            else:
                self._tree(p, x, y, h * rng.uniform(0.035, 0.06) * s, _mix(QColor("#22252a"), sky, haze), rng)
        # The garden walls along the road, the road, and its lamps with their pools of light.
        p.setPen(Qt.PenStyle.NoPen)
        p.fillRect(QRectF(0, road - h * 0.005, w, h * 0.005), _c("#383b3f"))
        p.fillRect(QRectF(0, road - h * 0.005, w, max(1.0, h * 0.001)), _c("#5d6268"))
        tarmac = QLinearGradient(0, road, 0, road + h * 0.013)
        tarmac.setColorAt(0.0, _c("#4d5257"))
        tarmac.setColorAt(1.0, _c("#3c4045"))
        p.fillRect(QRectF(0, road, w, h * 0.013), tarmac)
        p.fillRect(QRectF(0, road + h * 0.013, w, max(1.0, h * 0.0012)), _c("#6a6f75"))
        spacing = h * 0.11
        post = QPen(_c("#202327"), max(0.9, h * 0.0019), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        n = int(w / spacing) + 2
        for i in range(n):
            lx = (i + 0.3 + rng.uniform(-0.1, 0.1)) * spacing
            head = QPointF(lx + h * 0.009, road - h * 0.038)
            _blob(p, head.x(), road + h * 0.007, h * 0.03, _c("#ffc27a", 0.28), 0.22)      # light on the road
            p.setPen(post)
            p.drawLine(QPointF(lx, road + h * 0.004), QPointF(lx, road - h * 0.038))
            p.drawLine(QPointF(lx, road - h * 0.038), head)
            p.setPen(Qt.PenStyle.NoPen)
            self.glow(p, head.x(), head.y() + h * 0.002, h * 0.018, _c("#ffc27a", 0.45))
            p.setBrush(_c("#fff0cc"))
            p.drawEllipse(QPointF(head.x(), head.y() + h * 0.0015), max(0.9, h * 0.0022), max(0.7, h * 0.0014))
        # The field between the road and the hill, a hedge along its far side.
        near = road + h * 0.0142
        field = QLinearGradient(0, near, 0, h * 0.86)
        field.setColorAt(0.0, _c("#3d4035"))
        field.setColorAt(1.0, _c("#2c2f27"))
        p.fillRect(QRectF(0, near, w, h - near), field)
        x = 0.0
        while x < w:
            if rng.random() < 0.12:
                x += h * rng.uniform(0.02, 0.06)                              # a gate, a gap
                continue
            # A clump of hedge: a few leafless tangles, darker low down, lighter where the sky catches it.
            r = h * rng.uniform(0.005, 0.009)
            for _ in range(4):
                cx, cy = x + rng.uniform(-1, 1) * r, near + h * 0.003 - rng.uniform(0.2, 1.1) * r
                p.setBrush(_c(rng.choice(("#23281f", "#2a2f25", "#1f231c", "#30352a"))))
                p.drawEllipse(QPointF(cx, cy), r * rng.uniform(0.9, 1.4), r * rng.uniform(0.7, 1.0))
            p.setBrush(_c("#4c5244", 0.35))
            p.drawEllipse(QPointF(x, near + h * 0.003 - r * 1.3), r * 0.8, r * 0.35)
            x += r * rng.uniform(1.3, 2.0)
        p.setPen(QPen(_c("#4b4f41", 0.5), max(0.6, h * 0.001)))
        for _ in range(int(w / max(1.0, h * 0.01))):                       # furrows in the field
            fx, fy = rng.uniform(0, w), rng.uniform(near + h * 0.012, h * 0.86)
            p.drawLine(QPointF(fx, fy), QPointF(fx + h * rng.uniform(0.02, 0.06), fy))
        p.setPen(Qt.PenStyle.NoPen)

    def _house(self, p: QPainter, x: float, y: float, hw: float, haze: float, sky: QColor, rng) -> None:
        """A house standing on the ground at ``y``, ``hw`` wide: plastered walls lit a little from above,
        a roof across it or its gable to us, a chimney now and then, windows (some lit, glowing),
        a door."""

        def tone(colour: str) -> QColor:
            return _mix(QColor(colour), sky, haze)

        wall = tone(rng.choice(("#5b5e5b", "#666157", "#575c63", "#6a6254", "#555759", "#615b55")))
        roof = tone(rng.choice(("#3b3533", "#2f3337", "#4b3a33", "#373737", "#41332e")))
        hh = hw * rng.uniform(0.55, 0.72)
        gable = rng.random() < 0.35
        face = QLinearGradient(0, y - hh, 0, y)
        face.setColorAt(0.0, wall.lighter(106))
        face.setColorAt(1.0, wall.darker(118))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_c("#000000", 0.25))
        p.drawRect(QRectF(x - hw * 0.04, y - hw * 0.02, hw * 1.1, hw * 0.04))              # its shadow on the ground
        if gable:
            peak = hw * rng.uniform(0.4, 0.5)
            p.setBrush(face)
            p.drawPolygon(QPolygonF([QPointF(x, y), QPointF(x, y - hh), QPointF(x + hw / 2, y - hh - peak),
                                     QPointF(x + hw, y - hh), QPointF(x + hw, y)]))
            edge = QPen(roof, hw * 0.08, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap, Qt.PenJoinStyle.MiterJoin)
            p.setPen(edge)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPolyline(QPolygonF([QPointF(x - hw * 0.07, y - hh + hw * 0.05), QPointF(x + hw / 2, y - hh - peak - hw * 0.02),
                                      QPointF(x + hw * 1.07, y - hh + hw * 0.05)]))
            p.setPen(Qt.PenStyle.NoPen)
            self._window(p, QRectF(x + hw * 0.44, y - hh - peak * 0.55, hw * 0.12, hw * 0.12), haze, sky, rng)
        else:
            p.setBrush(face)
            p.drawRect(QRectF(x, y - hh, hw, hh))
            rise = hw * rng.uniform(0.26, 0.38)
            slope = QLinearGradient(0, y - hh - rise, 0, y - hh)
            slope.setColorAt(0.0, roof.lighter(125))
            slope.setColorAt(1.0, roof.darker(110))
            p.setBrush(slope)
            p.drawPolygon(QPolygonF([QPointF(x - hw * 0.06, y - hh + hw * 0.015), QPointF(x + hw * 0.16, y - hh - rise),
                                     QPointF(x + hw * 0.84, y - hh - rise), QPointF(x + hw * 1.06, y - hh + hw * 0.015)]))
            p.setBrush(_c("#000000", 0.28))
            p.drawRect(QRectF(x, y - hh + hw * 0.015, hw, hh * 0.08))                       # under the eaves
            top = y - hh - rise
            if rng.random() < 0.55:                                                    # a chimney
                cx = x + hw * rng.uniform(0.25, 0.7)
                p.setBrush(roof.darker(115))
                p.drawRect(QRectF(cx, top - hw * 0.1, hw * 0.07, hw * 0.1 + rise * 0.4))
                p.setBrush(roof.lighter(120))
                p.drawRect(QRectF(cx - hw * 0.01, top - hw * 0.1, hw * 0.09, hw * 0.02))
        # Windows along the front, and a door.
        n = 2 if hw < self.h * 0.04 else 3
        door = rng.random() < 0.5
        for i in range(n):
            if door and i == n // 2:
                p.setBrush(tone("#2a2622"))
                p.drawRect(QRectF(x + hw * (0.14 + 0.72 * i / max(1, n - 1)) - hw * 0.06, y - hh * 0.55, hw * 0.12, hh * 0.55))
                continue
            cx = x + hw * (0.16 + 0.68 * i / max(1, n - 1))
            self._window(p, QRectF(cx - hw * 0.07, y - hh * 0.72, hw * 0.14, hh * 0.32), haze, sky, rng)

    def _window(self, p: QPainter, rect: QRectF, haze: float, sky: QColor, rng) -> None:
        """A window: lit warm, and glowing a little, or dark with the sky's grey in its glass."""
        p.setPen(Qt.PenStyle.NoPen)
        if rng.random() < 0.45:
            self.glow(p, rect.center().x(), rect.center().y(), rect.width() * 1.8, _c("#ffc27a", 0.22 * (1 - haze)))
            p.setBrush(_mix(QColor(rng.choice(("#ffcf8a", "#ffdca6", "#f7b96a"))), sky, haze * 0.6))
        else:
            p.setBrush(_mix(QColor("#2a2f36"), sky, haze))
        p.drawRect(rect)
        p.setBrush(_c("#000000", 0.3))
        p.drawRect(QRectF(rect.left() - rect.width() * 0.1, rect.bottom(), rect.width() * 1.2, max(0.6, rect.height() * 0.12)))

    @staticmethod
    def _fir(p: QPainter, x: float, y: float, tall: float, colour: QColor) -> None:
        """A fir: its tiers darker on the side away from the sky's light."""
        path = QPainterPath()
        _spruce(path, x, y, tall)
        g = QLinearGradient(x - tall * 0.2, 0, x + tall * 0.2, 0)
        g.setColorAt(0.0, colour.lighter(125))
        g.setColorAt(1.0, colour.darker(125))
        p.setPen(Qt.PenStyle.NoPen)
        p.fillPath(path, g)

    @staticmethod
    def _tree(p: QPainter, x: float, y: float, tall: float, colour: QColor, rng) -> None:
        """A tree in winter: a trunk that forks into thinner and thinner limbs, tapering as they go, and
        the haze of its finest twigs round the crown."""
        _blob(p, x, y - tall * 0.62, tall * 0.42, _c(colour.name(), 0.22), 0.75)
        pen = QPen(colour, 1.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        stack = [(QPointF(x, y), -90.0 + rng.uniform(-5, 5), tall * 0.36, tall * 0.05, 0)]
        while stack:
            start, angle, length, width, depth = stack.pop()
            a = math.radians(angle)
            end = QPointF(start.x() + math.cos(a) * length, start.y() + math.sin(a) * length)
            pen.setWidthF(max(0.5, width))
            p.setPen(pen)
            p.drawLine(start, end)
            if depth < 4:
                forks = 3 if depth < 2 and rng.random() < 0.5 else 2
                for k in range(forks):
                    turn = rng.uniform(18, 38) * (1 if k % 2 else -1) if k < 2 else rng.uniform(-8, 8)
                    stack.append((end, angle + turn, length * rng.uniform(0.62, 0.78), width * 0.62, depth + 1))
        p.setPen(Qt.PenStyle.NoPen)

    def _flats(self, p: QPainter, rng, base: float, floor: float, look: tuple, near: float) -> None:
        """A row of blocks of flats: long slabs, and now and then a tower, their windows a floor at a
        time, some lit warm, a few cool, and now and then one that goes on and off."""
        w = self.w
        shade, top_colour, foot_colour = look
        warm = (QColor("#ffcf8a"), QColor("#ffdca6"), QColor("#f7b96a"), QColor("#dfe7f2"))
        x = -w * rng.uniform(0.0, 0.04)
        while x < w * 1.03:
            tower = rng.random() < 0.2
            bw = w * (rng.uniform(0.022, 0.035) if tower else rng.uniform(0.045, 0.11)) * (0.7 + 0.3 * near)
            floors = rng.randint(14, 22) if tower else rng.randint(5, 12)
            bh = floors * floor + floor * 0.8
            y0 = base + rng.uniform(-0.5, 1.0) * floor
            rect = QRectF(x, y0 - bh, bw, bh)
            tint = rng.uniform(-8, 8)
            face = QLinearGradient(0, rect.top(), 0, rect.bottom())
            face.setColorAt(0.0, QColor(top_colour).lighter(int(100 + tint)))
            face.setColorAt(1.0, QColor(foot_colour).lighter(int(100 + tint)))
            p.fillRect(rect, face)
            side = bw * (0.18 if tower else 0.08)
            p.fillRect(QRectF(rect.right() - side, rect.top(), side, bh), _c("#454a52", 0.85 * shade + 0.15))
            p.fillRect(QRectF(rect.left(), rect.top(), bw, max(1.0, floor * 0.35)), _c("#a2a9b1", 0.6))
            if rng.random() < 0.6:                                             # a lift house on the roof
                p.fillRect(QRectF(rect.left() + bw * rng.uniform(0.2, 0.6), rect.top() - floor * 1.1, bw * 0.14,
                                  floor * 1.1), QColor(foot_colour))
            cols = max(2, int((bw - side) / (floor * 1.3)))
            span = (bw - side) * 0.94 / cols
            for f in range(floors):
                wy = rect.top() + floor * 0.8 + f * floor + floor * 0.22
                for c in range(cols):
                    win = QRectF(rect.left() + bw * 0.03 + c * span + span * 0.22, wy, span * 0.56, floor * 0.48)
                    lit = rng.random() < 0.26
                    if lit:
                        colour = QColor(rng.choice(warm))
                        colour.setAlphaF(rng.uniform(0.5, 1.0) * (0.6 + 0.4 * near))
                        p.fillRect(win, colour)
                    else:
                        p.fillRect(win, _c("#2c3037", 0.55 + 0.25 * near))
                    if near > 0.9 and rng.random() < 0.012:
                        self.lamps.append((win, QColor(rng.choice(warm)), rng.uniform(12, 40), rng.uniform(0, 40), lit))
            x += bw * rng.uniform(0.8, 1.25) + (w * rng.uniform(0.0, 0.03) if rng.random() < 0.3 else 0.0)

    def _hill(self, p: QPainter, rng) -> None:
        """Dry winter grass on the hill in front, in patches, the sky's last light along its crest,
        and the tank's tracks through it."""
        w, h = self.w, self.h
        pts = [QPointF(i / 120 * w, self.crest(i / 120 * w)) for i in range(121)]
        hill = QPainterPath(QPointF(0, h))
        for pt in pts:
            hill.lineTo(pt)
        hill.lineTo(QPointF(w, h))
        hill.closeSubpath()
        top = min(pt.y() for pt in pts)
        grass = QColor(self.t["grass"])
        g = QLinearGradient(0, top, 0, h)
        g.setColorAt(0.0, grass)
        g.setColorAt(0.45, grass.darker(140))
        g.setColorAt(1.0, grass.darker(210))
        p.fillPath(hill, g)
        p.save()
        p.setClipPath(hill)

        def patches(q: QPainter) -> None:
            for _ in range(90):
                x = rng.uniform(-0.05, 1.05) * w
                y = rng.uniform(self.crest(max(0.0, min(w, x))), h * 1.02)
                light = rng.random() < 0.45
                _blob(q, x, y, h * rng.uniform(0.03, 0.09), _c("#7a7550" if light else "#26241a", rng.uniform(0.15, 0.3)),
                      rng.uniform(0.2, 0.35), rng.uniform(-6, 6))

        _spread(p, _soft(w, h, patches, 3.0), QRectF(0, 0, w, h))
        # The churned track the tank came up by, from the lower right.
        L = self.tank_length()
        rear = self.tank_x() + L * 0.85
        for off in (-L * 0.03, L * 0.03):
            rut = QPainterPath(QPointF(rear, self.crest(rear) + h * 0.004))
            rut.cubicTo(QPointF(rear + L * 0.25 + off, self.crest(rear) + h * 0.03),
                        QPointF(rear + L * 0.35 + off, h * 0.93), QPointF(rear + L * 0.25 + off * 2, h * 1.05))
            p.strokePath(rut, QPen(_c("#1c1a12", 0.22), h * 0.02, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        # Tufts and stalks all over it, bigger nearer.
        stalk = QPen(_c("#000000"), 1.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        for _ in range(int(1300 * max(0.3, min(2.6, w * h / BASE_AREA)))):
            x = rng.uniform(0, w)
            y = rng.uniform(self.crest(x), h)
            near = (y - top) / max(1.0, h - top)
            length = h * rng.uniform(0.004, 0.012) * (0.5 + 1.3 * near)
            stalk.setColor(_c(rng.choice(("#76704b", "#5f5a3d", "#353222", "#8a8460")), rng.uniform(0.3, 0.65)))
            stalk.setWidthF(max(0.6, h * 0.0012 * (0.6 + near)))
            p.setPen(stalk)
            lean = rng.uniform(-0.35, 0.45)
            p.drawLine(QPointF(x, y), QPointF(x + lean * length, y - length))
        p.restore()
        # Along the crest, the sky's last light, and stalks standing up against the city.
        rim = QPainterPath(pts[0])
        for pt in pts[1:]:
            rim.lineTo(pt)
        p.strokePath(rim, QPen(_c("#9a9670", 0.35), max(1.0, h * 0.002)))
        x = 0.0
        while x < w:
            y = self.crest(x)
            length = h * rng.uniform(0.004, 0.02)
            stalk.setColor(_c(rng.choice(("#6b6647", "#57533a", "#7d7752", "#3f3c29"))))
            stalk.setWidthF(max(0.6, h * 0.0012))
            p.setPen(stalk)
            lean = rng.uniform(-0.3, 0.45)
            p.drawLine(QPointF(x, y + 1), QPointF(x + lean * length, y - length))
            x += rng.uniform(1.2, 3.5)
        p.setPen(Qt.PenStyle.NoPen)

    # -- the tank ----------------------------------------------------------------------------------
    #
    # The Leopard 2A6 is drawn side on, facing left, in its own units: the hull's length is 1, its
    # front is at x 0, the ground is at y 0 and up is negative. The numbers are measured off a
    # photograph of one side on: a long, low hull, the side a flat slab down to just above the
    # track's top run; the turret long and low, its wedge armour coming to a point over the front
    # of the hull where the gun comes out, a box on its back; seven road wheels, the idler raised
    # at the front and the sprocket at the back. It is drawn once per size into two sprites, the
    # tank and its gun, so that when it fires the gun can run back and the hull rock on its tracks.

    GUN_Y = -0.256             # the height of the gun's axis
    MUZZLE = -0.392
    ROOF = -0.31
    WHEELS = tuple(0.181 + i * 0.1067 for i in range(7))
    WHEEL_Y, WHEEL_R = -0.060, 0.046
    IDLER, IDLER_R = (0.054, -0.098), 0.042
    SPROCKET, SPROCKET_R = (0.915, -0.083), 0.048
    SKIRT = -0.117             # the foot of the hull's side
    DECK = -0.223              # the top of the hull's side
    TURRET = ((0.091, -0.254), (0.161, -0.306), (0.30, -0.31), (0.775, -0.31), (0.779, -0.298), (0.842, -0.298),
              (0.846, -0.285), (0.842, -0.242), (0.775, -0.241), (0.735, -0.226), (0.171, -0.223))

    def tank_length(self) -> float:
        return min(self.w * 0.34, self.h * 0.72)

    def tank_x(self) -> float:
        """Where the front of its hull is; the gun reaches well past it, to the left."""
        return self.w * 0.58

    def _tank(self, p: QPainter) -> None:
        """Where the tank stands, and its shadow on the grass. The tank itself moves (see _tank_sprites)."""
        L = self.tank_length()
        x0 = self.tank_x()
        front, back = self.crest(x0 + L * 0.18), self.crest(x0 + L * 0.84)
        angle = math.degrees(math.atan2(back - front, L * 0.66))
        ground = (front + back) / 2 + L * 0.006
        _blob(p, x0 + L * 0.5, ground + L * 0.008, L * 0.6, _c("#0d0e0b", 0.6), 0.07)
        self.tank_xf = QTransform().translate(x0 + L * 0.51, ground).rotate(angle).translate(-L * 0.51, 0).scale(L, L)
        self.tank_scale = L

    def _tank_sprites(self) -> None:
        xf, L = self.tank_xf, self.tank_scale
        camo = self._camouflage()

        def sprite(draw, rect: QRectF) -> tuple[QPixmap, QPointF]:
            world = xf.mapRect(rect).adjusted(-2, -2, 2, 2)
            pm = _layer(world.width(), world.height(), self.dpr)
            q = QPainter(pm)
            q.setRenderHint(QPainter.RenderHint.Antialiasing)
            q.translate(-world.left(), -world.top())
            q.setTransform(xf, True)
            draw(q)
            q.end()
            return pm, world.topLeft()

        self.hull_sprite = sprite(lambda q: self._draw_leopard(q, camo), QRectF(-0.03, -0.37, 1.06, 0.39))
        self.gun_sprite = sprite(lambda q: self._draw_gun(q, camo), QRectF(self.MUZZLE - 0.01, self.GUN_Y - 0.03, 0.56, 0.06))
        # Only the barrel outside the turret shows; as it runs back, it goes in behind the wedge.
        everything = QPainterPath()
        everything.addRect(QRectF(-2.0, -1.0, 4.0, 2.0))
        turret = QPainterPath()
        turret.addPolygon(QPolygonF([QPointF(x, y) for x, y in self.TURRET]))
        turret.closeSubpath()
        self.gun_clip = xf.map(everything.subtracted(turret))
        self.muzzle = xf.map(QPointF(self.MUZZLE, self.GUN_Y))
        ahead = xf.map(QPointF(self.MUZZLE - 1, self.GUN_Y)) - self.muzzle
        length = math.hypot(ahead.x(), ahead.y()) or 1.0
        self.aim = QPointF(ahead.x() / length, ahead.y() / length)
        self.pivot = xf.map(QPointF(0.82, 0.0))
        self.antennas = [(QPointF(0.664, self.ROOF), 0.20), (QPointF(0.734, self.ROOF), 0.26)]
        self.vent = xf.map(QPointF(0.975, -0.236))
        # Grass in front of its tracks, so it stands in the grass, not on it.
        rng = random.Random(77)
        x0 = self.tank_x()
        rect = QRectF(x0 - L * 0.02, self.crest(x0 + L * 0.5) - L * 0.12, L * 1.08, L * 0.2)
        pm = _layer(rect.width(), rect.height(), self.dpr)
        q = QPainter(pm)
        q.setRenderHint(QPainter.RenderHint.Antialiasing)
        q.translate(-rect.left(), -rect.top())
        stalk = QPen(_c("#000000"), max(0.6, self.h * 0.0012), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        x = x0
        while x < x0 + L * 1.03:
            y = self.crest(x) + L * 0.012
            length = L * rng.uniform(0.006, 0.022)
            stalk.setColor(_c(rng.choice(("#6b6647", "#57533a", "#7d7752", "#3f3c29"))))
            q.setPen(stalk)
            q.drawLine(QPointF(x, y), QPointF(x + rng.uniform(-0.3, 0.45) * length, y - length))
            x += rng.uniform(0.8, 2.6)
        q.end()
        self.fringe = (pm, rect.topLeft())

    def _camouflage(self) -> list[tuple[QPainterPath, QColor]]:
        """The three-colour pattern: big patches of brown and black over the green, the black often along
        an edge of the brown, their edges wandering, lobed, the way the painted pattern does."""
        t = self.t
        brown, black = QColor(t["camo_brown"]), QColor(t["camo_black"])
        rng = random.Random(55)
        patches = []

        def patch(c: QPointF, rx: float, ry: float, turn: float, colour: QColor) -> None:
            waves = [(rng.uniform(0, math.tau), rng.uniform(0.1, 0.25), n) for n in (2, 3, 5, 8)]
            pts = []
            for k in range(40):
                a = math.tau * k / 40
                r = 1 + sum(amp * math.sin(n * a + phase) / (n ** 0.35) for phase, amp, n in waves)
                x, y = math.cos(a) * rx * r, math.sin(a) * ry * r
                pts.append(QPointF(c.x() + x * math.cos(turn) - y * math.sin(turn), c.y() + x * math.sin(turn) + y * math.cos(turn)))
            path = QPainterPath()
            path.addPolygon(QPolygonF(pts))
            path.closeSubpath()
            patches.append((path, colour))

        x = -0.42
        while x < 1.04:
            c = QPointF(x + rng.uniform(-0.02, 0.02), rng.uniform(-0.28, -0.13))
            rx = rng.uniform(0.07, 0.11)
            ry = rx * rng.uniform(0.5, 0.75)
            turn = math.radians(rng.uniform(-40, 10))
            first, second = (brown, black) if rng.random() < 0.6 else (black, brown)
            patch(c, rx, ry, turn, first)
            side = rng.choice((-1, 1))
            patch(c + QPointF(rx * 0.9 * side, ry * rng.uniform(-0.8, 0.8)), rx * rng.uniform(0.55, 0.8), ry * 0.75,
                  turn + math.radians(rng.uniform(-30, 30)), second)
            x += rng.uniform(0.15, 0.21)
        return patches

    def _draw_leopard(self, p: QPainter, camo) -> None:
        t = self.t
        green = QColor(t["camo_green"])
        p.setPen(Qt.PenStyle.NoPen)

        def poly(*pts) -> QPolygonF:
            return QPolygonF([QPointF(x, y) for x, y in pts])

        def pen(colour, width: float, alpha: float = 1.0) -> QPen:
            return QPen(_c(QColor(colour).name(), alpha), width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)

        skirt, deck = self.SKIRT, self.DECK
        # The hull in the shadow behind the running gear, then the running gear.
        p.setBrush(green.darker(190))
        p.drawPolygon(poly((0.02, skirt), (0.98, skirt), (0.99, -0.09), (0.03, -0.09)))
        self._running_gear(p)
        # The hull's side, a flat slab from the long, shallow upper front plate back to the stern;
        # and the turret on it.
        side = poly((0.0, -0.177), (0.171, deck), (0.996, -0.232), (0.996, skirt + 0.004), (0.985, skirt),
                    (0.004, skirt), (0.0, skirt - 0.006))
        turret = poly(*self.TURRET)
        painted = QPainterPath()
        painted.setFillRule(Qt.FillRule.WindingFill)
        for part in (side, turret):
            painted.addPolygon(part)
            painted.closeSubpath()
        p.fillPath(painted, green)
        p.save()
        p.setClipPath(painted)
        for path, colour in camo:
            p.fillPath(path, colour)
        # Light from the overcast above: faces that look up lighter, low down darker; the wedge,
        # angled up to the sky, lighter still; dust on the lower side.
        light = QLinearGradient(0, self.ROOF, 0, skirt)
        light.setColorAt(0.0, _c("#ffffff", 0.15))
        light.setColorAt(0.35, _c("#ffffff", 0.03))
        light.setColorAt(0.65, _c("#000000", 0.04))
        light.setColorAt(1.0, _c("#000000", 0.30))
        p.fillRect(QRectF(-0.05, -0.34, 1.1, 0.24), light)
        p.setBrush(_c("#ffffff", 0.09))
        p.drawPolygon(poly((0.091, -0.254), (0.161, -0.306), (0.29, -0.31), (0.29, -0.262), (0.12, -0.25)))
        p.setBrush(_c("#000000", 0.12))
        p.drawPolygon(poly((0.091, -0.254), (0.12, -0.25), (0.29, -0.262), (0.29, -0.226), (0.171, deck)))
        dust = QLinearGradient(0, -0.16, 0, skirt)
        dust.setColorAt(0.0, _c("#6b5d45", 0.0))
        dust.setColorAt(1.0, _c("#6b5d45", 0.35))
        p.fillRect(QRectF(0.0, -0.16, 1.0, 0.045), dust)
        # The turret's shadow on the hull, under its overhanging back.
        p.fillRect(QRectF(0.70, -0.236, 0.3, 0.01), _c("#000000", 0.35))
        p.fillPath(painted, _c("#303842", 0.2))                                       # the dusk dulling it all
        p.restore()
        # Lines: the hull's top edge, the skirt sections, the heavy front sections' bolts; the
        # turret's foot, its wedge module, the ledge along its side, its side armour.
        seam, lit = pen("#0f100c", 0.0022, 0.7), pen("#e2e8cc", 0.002, 0.3)
        p.setPen(seam)
        for x in (0.155, 0.30, 0.42, 0.54, 0.66, 0.78, 0.90):
            p.drawLine(QPointF(x, deck + 0.004 if x > 0.17 else -0.18 - x * 0.25), QPointF(x, skirt - 0.001))
        p.drawLine(QPointF(0.171, deck), QPointF(0.996, -0.232))
        p.drawLine(QPointF(0.30, -0.226), QPointF(0.735, -0.226))
        p.drawLine(QPointF(0.29, -0.31), QPointF(0.29, -0.227))                        # the wedge module's back
        p.drawLine(QPointF(0.12, -0.25), QPointF(0.29, -0.262))                        # its crease
        p.drawLine(QPointF(0.29, -0.274), QPointF(0.775, -0.274))                      # the ledge
        p.drawLine(QPointF(0.46, -0.305), QPointF(0.46, -0.229))
        p.drawLine(QPointF(0.775, -0.31), QPointF(0.775, -0.242))                     # the box on its back
        p.drawLine(QPointF(0.779, -0.285), QPointF(0.842, -0.285))
        p.setPen(lit)
        p.drawLine(QPointF(0.161, -0.306), QPointF(0.775, -0.31))
        p.drawLine(QPointF(0.091, -0.254), QPointF(0.161, -0.306))
        p.drawLine(QPointF(0.0, -0.177), QPointF(0.171, deck))
        p.drawLine(QPointF(0.735, -0.229), QPointF(0.996, -0.231))
        for x in (0.157, 0.302, 0.422, 0.542, 0.662, 0.782, 0.902):
            p.drawLine(QPointF(x, deck + 0.006 if x > 0.17 else -0.18 - x * 0.25), QPointF(x, skirt - 0.002))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_c("#16170f", 0.85))
        for x0 in (0.0, 0.155):
            for k in range(4):
                for y in (-0.2 if x0 else -0.175, skirt + 0.009):
                    p.drawEllipse(QPointF(x0 + 0.022 + k * 0.034, y), 0.0032, 0.0032)
        p.setBrush(_c("#1a1b15", 0.9))
        p.drawRect(QRectF(0.30, skirt - 0.008, 0.69, 0.008))                         # rubber at the skirts' foot
        # The front: headlights in their guards up on the front plate, a towing eye low down.
        p.setBrush(_c("#23251d"))
        p.drawRoundedRect(QRectF(0.012, -0.203, 0.024, 0.024), 0.004, 0.004)
        p.drawRoundedRect(QRectF(0.068, -0.2, 0.016, 0.022), 0.004, 0.004)
        p.setBrush(_c("#c9d0bd", 0.5))
        p.drawEllipse(QPointF(0.02, -0.193), 0.0045, 0.0045)
        # The tow cable along the top of the side, the tail light, the exhaust louvres at the back.
        p.setPen(pen("#1b1c17", 0.005, 0.9))
        cable = QPainterPath(QPointF(0.2, -0.217))
        cable.cubicTo(QPointF(0.4, -0.221), QPointF(0.6, -0.216), QPointF(0.84, -0.219))
        p.drawPath(cable)
        p.drawEllipse(QPointF(0.195, -0.216), 0.008, 0.006)
        p.setPen(pen("#131410", 0.002, 0.8))
        for k in range(5):
            p.drawLine(QPointF(0.91 + k * 0.014, -0.222), QPointF(0.91 + k * 0.014, -0.19))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_c("#8a2a1c", 0.95))
        p.drawRoundedRect(QRectF(0.955, -0.25, 0.03, 0.014), 0.004, 0.004)
        self._turret_fittings(p, green)

    def _running_gear(self, p: QPainter) -> None:
        """The track round the raised idler at the front and the sprocket at the back, seven road wheels,
        dust low down."""
        (ix, iy), ir = self.IDLER, self.IDLER_R + 0.012
        (sx, sy), sr = self.SPROCKET, self.SPROCKET_R + 0.012
        pts = [QPointF(0.15, 0.0), QPointF(0.85, 0.0)]
        for k in range(0, 19):
            a = math.radians(115 - k * 11.5)                      # round the back of the sprocket, up to its top
            pts.append(QPointF(sx + math.cos(a) * sr, sy + math.sin(a) * sr))
        for k in range(0, 17):
            a = math.radians(-90 - k * 11.5)                      # round the front of the idler, down its face
            pts.append(QPointF(ix + math.cos(a) * ir, iy + math.sin(a) * ir))
        track = QPainterPath()
        track.addPolygon(QPolygonF(pts))
        track.closeSubpath()
        p.setBrush(_c("#26261f"))
        p.drawPath(track)
        # The track's links: their end connectors along the ground run, the pads beneath.
        p.setBrush(_c("#3d3c33"))
        x = 0.15
        while x < 0.85:
            p.drawRect(QRectF(x, -0.012, 0.012, 0.007))
            x += 0.018
        p.setBrush(_c("#17170f"))
        x = 0.155
        while x < 0.85:
            p.drawRect(QRectF(x, -0.003, 0.006, 0.003))
            x += 0.018
        wheel = QColor(self.t["wheel"])
        for x in self.WHEELS:
            self._road_wheel(p, QPointF(x, self.WHEEL_Y), self.WHEEL_R, wheel)
        self._road_wheel(p, QPointF(ix, iy), self.IDLER_R, wheel, rubber=False)
        sprocket = QPainterPath()
        teeth = 11
        for k in range(teeth * 2):
            a = math.tau * k / (teeth * 2)
            r = self.SPROCKET_R if k % 2 == 0 else self.SPROCKET_R * 0.84
            pt = QPointF(sx + math.cos(a) * r, sy + math.sin(a) * r)
            if k == 0:
                sprocket.moveTo(pt)
            else:
                sprocket.lineTo(pt)
        sprocket.closeSubpath()
        p.setBrush(wheel.darker(135))
        p.drawPath(sprocket)
        disc = QRadialGradient(QPointF(sx - 0.01, sy - 0.012), self.SPROCKET_R)
        disc.setColorAt(0.0, wheel.lighter(110))
        disc.setColorAt(1.0, wheel.darker(160))
        p.setBrush(disc)
        p.drawEllipse(QPointF(sx, sy), self.SPROCKET_R * 0.72, self.SPROCKET_R * 0.72)
        p.setBrush(_c("#1b1c16"))
        for k in range(8):
            a = math.tau * k / 8
            p.drawEllipse(QPointF(sx + math.cos(a) * 0.022, sy + math.sin(a) * 0.022), 0.003, 0.003)
        # The track's top run, just under the skirts; shade beneath them; dust low down.
        p.setBrush(_c("#23231c"))
        p.drawRect(QRectF(0.06, self.SKIRT, 0.86, 0.008))
        shade = QLinearGradient(0, self.SKIRT, 0, self.SKIRT + 0.04)
        shade.setColorAt(0.0, _c("#000000", 0.45))
        shade.setColorAt(1.0, _c("#000000", 0.0))
        p.fillRect(QRectF(0.03, self.SKIRT, 0.94, 0.04), shade)
        wheels = QPainterPath()
        for x in self.WHEELS:
            wheels.addEllipse(QPointF(x, self.WHEEL_Y), self.WHEEL_R, self.WHEEL_R)
        dust = QLinearGradient(0, -0.06, 0, 0.0)
        dust.setColorAt(0.0, _c("#6f6248", 0.0))
        dust.setColorAt(1.0, _c("#6f6248", 0.4))
        p.save()
        p.setClipPath(track.united(wheels))
        p.fillRect(QRectF(0.0, -0.06, 1.0, 0.06), dust)
        p.restore()

    @staticmethod
    def _road_wheel(p: QPainter, c: QPointF, r: float, colour: QColor, rubber: bool = True) -> None:
        """A dished road wheel: its rubber tyre, the rim catching the light, a ring of bolts round the hub."""
        if rubber:
            p.setBrush(_c("#191915"))
            p.drawEllipse(c, r, r)
        disc = QRadialGradient(c + QPointF(-r * 0.25, -r * 0.3), r)
        disc.setColorAt(0.0, colour.lighter(112))
        disc.setColorAt(0.7, colour)
        disc.setColorAt(1.0, colour.darker(160))
        p.setBrush(disc)
        p.drawEllipse(c, r * 0.8, r * 0.8)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(_c(colour.lighter(135).name(), 0.5), r * 0.05))
        p.drawArc(QRectF(c.x() - r * 0.68, c.y() - r * 0.68, r * 1.36, r * 1.36), 100 * 16, 120 * 16)
        p.setPen(QPen(_c(colour.darker(170).name(), 0.6), r * 0.05))
        p.drawEllipse(c, r * 0.55, r * 0.55)
        p.setPen(Qt.PenStyle.NoPen)
        hub = QRadialGradient(c + QPointF(-r * 0.1, -r * 0.1), r * 0.36)
        hub.setColorAt(0.0, colour.lighter(108))
        hub.setColorAt(1.0, colour.darker(170))
        p.setBrush(hub)
        p.drawEllipse(c, r * 0.34, r * 0.34)
        p.setBrush(_c("#1d1e17"))
        for k in range(8):
            a = math.tau * k / 8
            p.drawEllipse(QPointF(c.x() + math.cos(a) * r * 0.25, c.y() + math.sin(a) * r * 0.25), r * 0.035, r * 0.035)
        p.setBrush(_c("#8a3a2a", 0.8))                                              # the red-brown hub cap
        p.drawEllipse(c, r * 0.11, r * 0.11)

    def _turret_fittings(self, p: QPainter, green: QColor) -> None:
        """What stands on the roof: the gunner's sight, the loader's hatch, the commander's cupola with
        his periscope on its mast, the wind sensor; the smoke dischargers on the flank; the box and
        its lid at the back; the antennas' bases."""

        def poly(*pts) -> QPolygonF:
            return QPolygonF([QPointF(x, y) for x, y in pts])

        dark, mid = green.darker(150), green.darker(118)
        glass = _c("#1a2027")
        roof = self.ROOF
        p.setPen(Qt.PenStyle.NoPen)
        # The gunner's sight: a low armoured housing towards the front of the roof.
        p.setBrush(mid)
        p.drawPolygon(poly((0.2, roof + 0.001), (0.212, roof - 0.014), (0.29, roof - 0.014), (0.296, roof + 0.001)))
        p.setBrush(glass)
        p.drawPolygon(poly((0.203, roof - 0.001), (0.211, roof - 0.012), (0.219, roof - 0.012), (0.212, roof - 0.001)))
        # The loader's hatch, and the commander's cupola with its ring of periscopes.
        p.setBrush(mid)
        p.drawRoundedRect(QRectF(0.37, roof - 0.009, 0.075, 0.01), 0.004, 0.004)
        p.drawRoundedRect(QRectF(0.465, roof - 0.014, 0.08, 0.015), 0.005, 0.005)
        p.setBrush(dark)
        for x in (0.475, 0.495, 0.515, 0.533):
            p.drawRect(QRectF(x, roof - 0.02, 0.009, 0.007))
        # The commander's periscope: a head on a short mast, looking forward.
        p.setBrush(dark)
        p.drawRect(QRectF(0.566, roof - 0.018, 0.016, 0.018))
        head = QLinearGradient(0, roof - 0.05, 0, roof - 0.018)
        head.setColorAt(0.0, green.lighter(108))
        head.setColorAt(1.0, green.darker(150))
        p.setBrush(head)
        p.drawRoundedRect(QRectF(0.548, roof - 0.05, 0.054, 0.033), 0.006, 0.006)
        p.setBrush(glass)
        p.drawRect(QRectF(0.548, roof - 0.045, 0.009, 0.022))
        p.setBrush(_c("#8fb4cc", 0.55))
        p.drawRect(QRectF(0.549, roof - 0.042, 0.004, 0.007))
        p.setBrush(dark)
        p.drawRect(QRectF(0.556, roof - 0.054, 0.04, 0.005))
        # The wind sensor on its mast at the back of the roof.
        p.drawRect(QRectF(0.758, roof - 0.045, 0.004, 0.045))
        p.drawRoundedRect(QRectF(0.752, roof - 0.052, 0.016, 0.009), 0.003, 0.003)
        # The smoke dischargers: a bank of four tubes on the flank, angled up and forward.
        p.setBrush(dark)
        p.drawRoundedRect(QRectF(0.585, -0.297, 0.095, 0.02), 0.004, 0.004)
        for k in range(4):
            c = QPointF(0.598 + k * 0.022, -0.29)
            p.save()
            p.translate(c)
            p.rotate(-25)
            tube = QLinearGradient(0, -0.008, 0, 0.008)
            tube.setColorAt(0.0, green.lighter(110))
            tube.setColorAt(1.0, green.darker(160))
            p.setBrush(tube)
            p.drawRoundedRect(QRectF(-0.019, -0.008, 0.026, 0.016), 0.003, 0.003)
            p.setBrush(_c("#121310"))
            p.drawEllipse(QPointF(-0.019, 0.0), 0.0035, 0.007)
            p.restore()
        # The box on the turret's back: its lid, its latches.
        p.setBrush(_c("#000000", 0.12))
        p.drawRect(QRectF(0.779, -0.285, 0.063, 0.043))
        p.setBrush(dark)
        for x in (0.795, 0.825):
            p.drawRect(QRectF(x, -0.287, 0.008, 0.006))
        # A stowage box on the flank, with a handle; the antennas' bases.
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(_c("#0f100c", 0.6), 0.002))
        p.drawRect(QRectF(0.47, -0.302, 0.1, 0.024))
        p.drawLine(QPointF(0.51, -0.29), QPointF(0.53, -0.29))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(dark)
        for x in (0.664, 0.734):
            p.drawRect(QRectF(x - 0.004, roof - 0.009, 0.008, 0.01))

    def _draw_gun(self, p: QPainter, camo) -> None:
        """The L55 gun: a long barrel in its thermal sleeve, clamped in sections, thicker where the bore
        evacuator is, near the turret; the muzzle with its reference sensor on top; round, so lit
        on top and dark beneath."""
        green = QColor(self.t["camo_green"])
        y = self.GUN_Y
        muzzle = self.MUZZLE
        # (from, to, half thickness at from, at to)
        parts = [(0.16, 0.066, 0.0125, 0.0125), (0.066, -0.004, 0.0165, 0.0165), (-0.004, muzzle + 0.02, 0.0112, 0.0095),
                 (muzzle + 0.02, muzzle, 0.0108, 0.0108)]
        barrel = QPainterPath()
        barrel.setFillRule(Qt.FillRule.WindingFill)
        for a, b, ha, hb in parts:
            barrel.addPolygon(QPolygonF([QPointF(a, y - ha), QPointF(b, y - hb), QPointF(b, y + hb), QPointF(a, y + ha)]))
            barrel.closeSubpath()
        p.setPen(Qt.PenStyle.NoPen)
        p.fillPath(barrel, green)
        p.save()
        p.setClipPath(barrel)
        for path, colour in camo:
            p.fillPath(path, colour)
        round_ = QLinearGradient(0, y - 0.017, 0, y + 0.017)
        round_.setColorAt(0.0, _c("#ffffff", 0.22))
        round_.setColorAt(0.3, _c("#ffffff", 0.06))
        round_.setColorAt(0.55, _c("#000000", 0.05))
        round_.setColorAt(1.0, _c("#000000", 0.55))
        p.fillRect(QRectF(muzzle - 0.01, y - 0.02, 0.6, 0.04), round_)
        p.fillPath(barrel, _c("#303842", 0.2))
        p.restore()
        # The bore evacuator's shoulders, the clamps holding the sleeve's sections, and the muzzle's
        # reference sensor.
        p.setBrush(_c("#15160f", 0.55))
        for x in (0.062, -0.004):
            p.drawRect(QRectF(x, y - 0.0165, 0.004, 0.033))
        p.setBrush(_c("#1a1b15", 0.8))
        for x, half in ((0.12, 0.0125), (-0.08, 0.011), (-0.17, 0.0105), (-0.26, 0.01), (-0.33, 0.0098)):
            p.drawRect(QRectF(x - 0.002, y - half - 0.001, 0.004, 2 * half + 0.002))
        p.setBrush(_c("#2a2c24"))
        p.drawRect(QRectF(muzzle + 0.004, y - 0.017, 0.018, 0.007))
        p.setBrush(_c("#0c0d0a"))
        p.drawEllipse(QPointF(muzzle, y), 0.0018, 0.007)

    # -- what moves --------------------------------------------------------------------------------
    def resize(self, w: int, h: int, dpr: float = 1.0) -> None:
        super().resize(w, h, dpr)
        r = self.rng
        self._tank_sprites()
        self.billows = []
        for i in range(max(4, int(10 * self.amount))):
            b = _Billow()
            b.life = r.uniform(24, 36)
            b.t = (i / max(4, int(10 * self.amount)) + r.uniform(0.0, 0.03)) % 1.0
            b.side = r.uniform(-0.25, 0.25)
            b.sprite = r.randrange(len(self.smoke_puffs))
            self.billows.append(b)
        # Keep the flickering windows clear of the tank.
        left = self.tank_x() + self.MUZZLE * self.tank_length()
        self.lamps = [lamp for lamp in self.lamps if lamp[0].right() < left]
        self.birds = [(r.uniform(0, math.tau), r.uniform(0.6, 1.3), r.uniform(-1, 1), r.uniform(0, math.tau),
                       r.uniform(7.0, 10.0)) for _ in range(7)]
        self.blast = []

    def spawn(self, anywhere=False):
        r = self.rng
        a = _Ash()
        a.x = r.uniform(-0.05, 1.0) * self.w
        a.y = r.uniform(0, self.h) if anywhere else -6
        a.vx, a.vy = r.uniform(4, 14), r.uniform(8, 20)
        a.r = r.uniform(0.6, 1.6)
        a.phase = r.uniform(0, math.tau)
        a.alpha = r.uniform(0.25, 0.55)
        return a

    def keep_inside(self, a) -> None:
        a.x %= self.w
        a.y %= self.h

    # -- firing ---------------------------------------------------------------------------------------
    RECOIL = 0.05              # how far the gun runs back, as a part of the hull's length

    def shoot(self) -> None:
        """The gun fires: along the hill, away over the grass, not at anything in sight. A flash at the
        muzzle and a flash of light round it, a tracer, the barrel running back into its mantlet
        and the hull rocking on its tracks, a cloud of smoke rolling forward from the muzzle and
        dust thrown up from the ground under it."""
        r, L = self.rng, self.tank_scale
        self.shot = 0.0
        m, d = self.muzzle, self.aim
        up = QPointF(d.y(), -d.x()) if d.x() < 0 else QPointF(-d.y(), d.x())          # square to the barrel, upward
        for _ in range(22):
            along = r.uniform(0.0, 0.1) * L
            across = r.gauss(0, 0.025) * L
            speed = r.uniform(0.6, 2.6) * L
            self.blast.append([m.x() + d.x() * along + up.x() * across, m.y() + d.y() * along + up.y() * across,
                               d.x() * speed + r.gauss(0, 0.2) * L, d.y() * speed + r.gauss(0, 0.15) * L,
                               L * 0.04, L * r.uniform(0.08, 0.16), 0.0, r.uniform(3.0, 5.0), r.randrange(2), "smoke"])
        for _ in range(16):
            x = m.x() + r.uniform(-0.15, 0.4) * L
            y = self.crest(x) - L * 0.015
            away = 1 if x > m.x() else -1
            self.blast.append([x, y, away * r.uniform(0.15, 0.6) * L, -r.uniform(0.03, 0.15) * L,
                               L * 0.04, L * r.uniform(0.06, 0.12), 0.0, r.uniform(3.5, 5.5), r.randrange(2), "dust"])

    def recoil(self) -> float:
        """How far the gun has run back now, as a part of the hull's length."""
        if self.shot is None:
            return 0.0
        t = self.shot
        return self.RECOIL * (t / 0.04 if t < 0.04 else math.exp(-(t - 0.04) / 0.28))

    def rock(self) -> float:
        """How far the hull has pitched back, in degrees: nose up at the shot, then settling."""
        if self.shot is None:
            return 0.0
        t = self.shot
        return -1.3 * math.sin(math.tau * t / 0.75) * math.exp(-t / 0.5)

    def step(self, dt: float) -> None:
        super().step(dt)
        r, w, h = self.rng, self.w, self.h
        for a in self.items:
            a.x += (a.vx + 6 * math.sin(self.time * 0.8 + a.phase)) * dt
            a.y += a.vy * dt
            if a.y > h + 4 or a.x > w + 4:
                fresh = self.spawn()
                for name in _Ash.__slots__:
                    setattr(a, name, getattr(fresh, name))
        for b in self.billows:
            b.t += dt / b.life
            if b.t >= 1.0:
                b.t -= 1.0
                b.side = r.uniform(-0.25, 0.25)
        # Now and then a pair of jets, or a helicopter, under the cloud.
        self.next_air -= dt
        if self.next_air <= 0:
            self.next_air = r.uniform(18.0, 40.0) / max(self.amount, 0.3)
            left = r.random() < 0.5
            if r.random() < 0.6:
                y, size = h * r.uniform(0.35, 0.44), w * r.uniform(0.03, 0.045)
                for i in range(2):
                    f = _Flyer()
                    f.kind, f.size = "jet", size
                    f.x = (-size - i * size * 1.6) if left else (w + size + i * size * 1.6)
                    f.y = y + i * size * 0.35
                    f.vx = w * r.uniform(0.28, 0.4) * (1 if left else -1)
                    f.phase = 0.0
                    self.flyers.append(f)
            else:
                f = _Flyer()
                f.kind, f.size = "heli", w * r.uniform(0.025, 0.035)
                f.x = -f.size if left else w + f.size
                f.y = h * r.uniform(0.45, 0.54)
                f.vx = w * r.uniform(0.04, 0.06) * (1 if left else -1)
                f.phase = r.uniform(0, math.tau)
                self.flyers.append(f)
        for f in self.flyers:
            f.x += f.vx * dt
        self.flyers = [f for f in self.flyers if -f.size * 4 < f.x < w + f.size * 4]
        # The gun, now and then.
        self.next_shot -= dt
        if self.next_shot <= 0 and self.tank_scale:
            self.next_shot = r.uniform(14.0, 30.0) / max(self.amount, 0.3)
            self.shoot()
        if self.shot is not None:
            self.shot += dt
            if self.shot > 2.5:
                self.shot = None
        for b in self.blast:
            b[6] += dt
            slow = math.exp(-2.2 * dt)
            b[2] *= slow
            b[3] = b[3] * slow - h * 0.004 * dt            # it rises, warm
            b[0] += (b[2] + 8) * dt
            b[1] += b[3] * dt
            b[4] = min(b[4] + b[5] * dt, self.tank_scale * 0.28)       # spreading, but big soft sprites cost
        self.blast = [b for b in self.blast if b[6] < b[7]]
        # The engine idling: a breath of exhaust now and then, a big one just after the shot.
        self.next_puff -= dt
        if self.next_puff <= 0 and self.vent.x():
            self.next_puff = r.uniform(0.25, 0.6)
            self.exhaust.append([self.vent.x(), self.vent.y(), 0.0, r.uniform(2.0, 3.2), h * 0.008])
        for puff in self.exhaust:
            puff[2] += dt
            puff[0] += (10 + 6 * puff[2]) * dt
            puff[1] -= (h * 0.012) * dt
            puff[4] += h * 0.012 * dt
        self.exhaust = [puff for puff in self.exhaust if puff[2] < puff[3]]

    def paint_moving(self, p: QPainter) -> None:
        t, w, h = self.time, self.w, self.h
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        plus = QPainter.CompositionMode.CompositionMode_Plus
        over = QPainter.CompositionMode.CompositionMode_SourceOver
        # The fire at the foot of the smoke, flickering.
        s = self.source()
        p.setCompositionMode(plus)
        flicker = 0.55 + 0.25 * math.sin(t * 5.3) + 0.2 * math.sin(t * 12.7 + 1.1)
        size = h * 0.05
        p.setOpacity(0.35 * flicker)
        p.drawPixmap(QRectF(s.x() - size * 1.6, s.y() - size * 0.7, size * 3.2, size * 1.4), self.fire, QRectF(0, 0, 48, 48))
        p.setOpacity(1.0)
        p.setCompositionMode(over)
        # Smoke billowing up the column.
        for b in self.billows:
            x, y, width = self.column(0.2 + 0.8 * b.t)                  # from above the roofs
            r = width * 0.6
            p.setOpacity(0.5 * max(0.0, math.sin(math.pi * b.t)) ** 0.8)
            p.drawPixmap(QRectF(x + b.side * width - r, y - r, 2 * r, 2 * r), self.smoke_puffs[b.sprite], QRectF(0, 0, 64, 64))
        p.setOpacity(1.0)
        # Windows going on and off.
        for rect, colour, period, phase, _lit in self.lamps:
            on = ((t + phase) % period) < period * 0.6
            p.fillRect(rect, colour if on else _c("#30353d"))
        # Crows wheeling over the city.
        cx, cy = w * (0.55 + 0.12 * math.sin(t * 0.045)), h * (0.36 + 0.04 * math.sin(t * 0.07))
        pen = QPen(_c("#15171a", 0.85), max(1.0, h * 0.0022), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        for phase, radius, lift, flap_phase, flap in self.birds:
            a = phase + t * 0.25
            bx = cx + math.cos(a) * w * 0.09 * radius
            by = cy + math.sin(a) * h * 0.04 * radius + lift * h * 0.01
            span = h * 0.011
            beat = math.sin(t * flap + flap_phase)
            wing = QPainterPath(QPointF(bx - span, by - beat * span * 0.6))
            wing.quadTo(QPointF(bx - span * 0.4, by - span * 0.2), QPointF(bx, by))
            wing.quadTo(QPointF(bx + span * 0.4, by - span * 0.2), QPointF(bx + span, by - beat * span * 0.6))
            p.drawPath(wing)
        p.setPen(Qt.PenStyle.NoPen)
        # Aircraft.
        for f in self.flyers:
            p.save()
            p.translate(f.x, f.y)
            p.scale(f.size * (1 if f.vx > 0 else -1), f.size)
            p.translate(-0.5, 0)
            p.setBrush(_c("#1c2025", 0.8))
            p.drawPath(self.shapes[f.kind])
            if f.kind == "heli":
                blade = 0.55 + 0.1 * math.sin(t * 40 + f.phase)
                p.setBrush(_c("#1c2025", 0.45))
                p.drawRect(QRectF(0.61 - blade, -0.19, blade * 2, 0.02))
                p.drawRect(QRectF(-0.03, -0.18, 0.12, 0.02 + 0.1 * abs(math.sin(t * 50 + f.phase))))
            p.restore()
        self._paint_tank(p)
        # The shot's smoke and dust, the exhaust, and ash drifting down in front of it all.
        for x, y, vx, vy, r, grow, age, life, k, kind in self.blast:
            fade = min(1.0, age / 0.06) * (1 - age / life) ** 1.3
            p.setOpacity((0.85 if kind == "smoke" else 0.75) * fade)
            sprite = (self.blast_smoke if kind == "smoke" else self.blast_dust)[k]
            p.drawPixmap(QRectF(x - r, y - r, 2 * r, 2 * r), sprite, QRectF(0, 0, 64, 64))
        for x, y, age, life, r in self.exhaust:
            p.setOpacity(0.28 * math.sin(math.pi * age / life))
            p.drawPixmap(QRectF(x - r, y - r, 2 * r, 2 * r), self.exhaust_puff, QRectF(0, 0, 64, 64))
        p.setOpacity(1.0)
        for a in self.items:
            p.setBrush(_c("#b8bdc4", a.alpha))
            p.drawEllipse(QPointF(a.x, a.y), a.r, a.r * 0.7)

    def _paint_tank(self, p: QPainter) -> None:
        if not self.tank_scale or not hasattr(self, "hull_sprite"):
            return
        t, L = self.time, self.tank_scale
        shot = self.shot
        p.save()
        pitch = self.rock()
        if pitch:
            p.translate(self.pivot)
            p.rotate(pitch)
            p.translate(-self.pivot)
        pm, at = self.hull_sprite
        p.drawPixmap(at, pm)
        # The gun, run back by the recoil, only what is in front of the armour showing.
        p.save()
        p.setClipPath(self.gun_clip)
        back = self.recoil() * L
        pm, at = self.gun_sprite
        p.drawPixmap(at - self.aim * back, pm)
        p.restore()
        # The antennas, swaying; whipping about after a shot.
        kick = 4.0 * math.exp(-shot / 0.6) if shot is not None else 0.0
        p.setPen(QPen(_c("#1c1e18", 0.9), max(0.8, L * 0.0025)))
        p.setBrush(Qt.BrushStyle.NoBrush)
        for i, (base, length) in enumerate(self.antennas):
            root = self.tank_xf.map(base)
            sway = L * 0.02 * math.sin(t * 1.3 + i * 1.7) + L * 0.008 * math.sin(t * 3.1 + i)
            if shot is not None:
                sway += L * 0.012 * kick * math.sin(shot * 11 + i)
            whip = QPainterPath(root)
            whip.quadTo(QPointF(root.x() + sway * 0.2, root.y() - L * length * 0.6),
                        QPointF(root.x() + sway, root.y() - L * length))
            p.drawPath(whip)
        p.setPen(Qt.PenStyle.NoPen)
        # The muzzle flash.
        if shot is not None and shot < 0.16:
            # A ball of fire in front of the muzzle, stretched along the shot, white at its heart;
            # it swells for an instant, then goes.
            k = (1 - shot / 0.16) ** 1.4
            grow = 0.7 + 0.3 * min(1.0, shot / 0.03)
            m, d = self.muzzle, self.aim
            p.save()
            p.translate(m)
            p.rotate(math.degrees(math.atan2(d.y(), d.x())))
            p.setOpacity(k)
            p.drawPixmap(QRectF(-L * 0.04, -L * 0.1 * grow, L * 0.46 * grow, L * 0.2 * grow), self.fireball, QRectF(0, 0, 48, 48))
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
            p.drawPixmap(QRectF(-L * 0.02, -L * 0.075 * grow, L * 0.32 * grow, L * 0.15 * grow), self.fireball, QRectF(0, 0, 48, 48))
            p.drawPixmap(QRectF(-L * 0.04, -L * 0.06, L * 0.18, L * 0.12), self.flash, QRectF(0, 0, 48, 48))
            p.restore()
        p.restore()
        pm, at = self.fringe
        p.drawPixmap(at, pm)
        if shot is None:
            return
        # Its light over the hill for a moment, and the tracer going away along it.
        if shot < 0.3:
            self.glow(p, self.muzzle.x(), self.muzzle.y(), L * 1.1, _c("#ffb060", 0.35 * (1 - shot / 0.3) ** 2))
        if shot < 0.4:
            head = self.muzzle + self.aim * (shot * self.w * 3.0)
            tail = head - self.aim * (self.w * 0.1)
            g = QLinearGradient(head, tail)
            g.setColorAt(0.0, _c("#fff4d8", 0.95))
            g.setColorAt(0.3, _c("#ff9a40", 0.6))
            g.setColorAt(1.0, _c("#ff6a20", 0.0))
            p.setPen(QPen(QBrush(g), max(1.2, L * 0.004), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(head, tail)
            p.setPen(Qt.PenStyle.NoPen)


# -- Your picture --------------------------------------------------------------------

class Picture(Scene):
    """The player's own picture: darkened, soft at the edges, and wandering slowly.

    It is scaled once per size to a little more than the window, the veil and the
    vignette baked in, and then only moved: each frame is one plain copy, and a frame
    is only drawn when the picture has moved a whole pixel.
    """

    MARGIN = 0.07          # how far past the window's edges it reaches, each side, as a share of the size
    PERIODS = (97.0, 71.0)  # seconds for one sway across and one up and down: never quite the same path

    def __init__(self, t: dict, amount: float = 1.0, seed: int = 11):
        super().__init__(t, amount, seed)
        from .picture import DIMS
        path = t.get("_picture") or ""
        self.image = QImage(path) if path else QImage()
        self.veil = DIMS.get(t.get("_dim"), DIMS["medium"])
        self.layer: QPixmap | None = None
        self.time = self.rng.uniform(0, 60)          # not always from the middle
        self._at: tuple[int, int] | None = None
        self._moved = True

    def _render_static(self) -> QPixmap:
        pm = super()._render_static()
        self.layer = None
        if self.image.isNull():
            return pm
        dpr = self.dpr
        lw, lh = self.w * (1 + 2 * self.MARGIN), self.h * (1 + 2 * self.MARGIN)
        scaled = self.image.scaled(int(lw * dpr) + 2, int(lh * dpr) + 2, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                   Qt.TransformationMode.SmoothTransformation)
        layer = QPixmap.fromImage(scaled)
        layer.setDevicePixelRatio(dpr)
        w, h = layer.width() / dpr, layer.height() / dpr
        p = QPainter(layer)
        p.fillRect(QRectF(0, 0, w, h), QColor(4, 5, 8, round(255 * self.veil)))
        edge = QRadialGradient(QPointF(w / 2, h / 2), max(w, h) * 0.62)
        edge.setColorAt(0.55, QColor(0, 0, 0, 0))
        edge.setColorAt(1.0, QColor(0, 0, 0, round(150 * (0.5 + self.veil))))
        p.fillRect(QRectF(0, 0, w, h), edge)
        p.end()
        self.layer = layer
        self._at = None
        return pm

    def draw_still(self, p: QPainter) -> None:
        if not self.image.isNull():
            return
        # No picture (yet): a frame with hills in it, faintly, where the picture will be.
        s = min(self.w, self.h) * 0.16
        cx, cy = self.w / 2, self.h / 2
        p.setPen(QPen(_c(self.t["muted"], 0.22), max(1.5, s / 28)))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(QRectF(cx - s, cy - s * 0.72, 2 * s, 1.44 * s), s * 0.12, s * 0.12)
        hills = QPainterPath(QPointF(cx - s * 0.8, cy + s * 0.5))
        hills.lineTo(cx - s * 0.25, cy - s * 0.12)
        hills.lineTo(cx + s * 0.1, cy + s * 0.22)
        hills.lineTo(cx + s * 0.38, cy - s * 0.02)
        hills.lineTo(cx + s * 0.8, cy + s * 0.5)
        p.drawPath(hills)
        p.drawEllipse(QPointF(cx + s * 0.42, cy - s * 0.36), s * 0.12, s * 0.12)

    def _offset(self) -> tuple[float, float]:
        lw, lh = self.layer.width() / self.dpr, self.layer.height() / self.dpr
        mx, my = max(0.0, lw - self.w), max(0.0, lh - self.h)
        ax = math.sin(2 * math.pi * self.time / self.PERIODS[0])
        ay = math.sin(2 * math.pi * self.time / self.PERIODS[1] + 1.1)
        return -mx / 2 * (1 + ax), -my / 2 * (1 + ay)

    def step(self, dt: float) -> None:
        super().step(dt)
        if self.layer is None:
            self._moved = False
            return
        x, y = self._offset()
        at = (round(x * self.dpr), round(y * self.dpr))         # whole pixels of the screen
        self._moved = at != self._at
        self._at = at

    def changed(self) -> bool:
        return self._moved

    def paint(self, p: QPainter) -> None:
        if self.layer is None:
            super().paint(p)
            return
        if self._at is None:
            x, y = self._offset()
            self._at = (round(x * self.dpr), round(y * self.dpr))
        p.drawPixmap(QPointF(self._at[0] / self.dpr, self._at[1] / self.dpr), self.layer)


SCENES = {"none": Plain, "stars": Stars, "petals": Petals, "snow": Snow, "embers": Embers,
          "aurora": Aurora, "factory": Factory, "space": Space, "warfare": Warfare, "picture": Picture}


class Backdrop(QWidget):
    """The scene, filling the window behind everything else.

    Its timer runs only while there is something moving, the window is shown
    and not minimised, and motion is on; otherwise nothing ticks at all.

    It runs at the frame rate the player chose (:meth:`set_fps`), and watches
    what the whole app costs meanwhile (the cards over the scene are redrawn
    with it every frame). While that is over its budget, :data:`BUDGET` at 30
    frames and more for more, it eases down to slower rates, and back up to the
    chosen one once there is room again. While the window
    is being resized, the last picture is stretched to fit and the scene is
    only drawn anew for the new size once the size holds still.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.scene: Scene = Plain({"grad_top": "#15171b", "grad_bottom": "#15171b"})
        self.theme_key = "standard"
        self.motion = True
        self.paused = False
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.setInterval(round(1000 / FPS))
        self.timer.timeout.connect(self._tick)
        self.clock = QElapsedTimer()
        self.clock.start()
        self.cap = FPS                               # the rate chosen
        self.ladder = ladder(FPS)
        self.level = 0                               # where on the ladder it runs now
        self.pace = QElapsedTimer()
        self.cpu_mark = 0.0
        self.settle = QTimer(self)
        self.settle.setSingleShot(True)
        self.settle.setInterval(SETTLE_MS)
        self.settle.timeout.connect(self._fit)

    def configure(self, theme: Theme, t: dict, motion: bool, amount: str, fps: int | None = None) -> None:
        self.theme_key = theme.key
        self.scene = SCENES.get(theme.scene, Plain)(t, AMOUNTS.get(amount, 1.0))
        self.motion = motion
        if fps is not None and fps != self.cap:
            self.set_fps(fps)
        self.scene.fps = self.rate
        if self.width() > 0:
            self.scene.resize(self.width(), self.height(), self.devicePixelRatioF())
        self._sync()
        self.update()

    def set_paused(self, paused: bool) -> None:
        self.paused = paused
        self._sync()

    def set_motion(self, motion: bool) -> None:
        """Moving or still, without drawing the scene anew."""
        self.motion = motion
        self._sync()
        self.update()

    @property
    def moving(self) -> bool:
        return self.timer.isActive()

    def _sync(self) -> None:
        run = (self.motion and not self.paused and self.isVisible()
               and not isinstance(self.scene, Plain))
        if run and not self.timer.isActive():
            self.clock.restart()
            self.pace.restart()
            self.cpu_mark = time.process_time()
            self.timer.start()
        elif not run and self.timer.isActive():
            self.timer.stop()

    @property
    def rate(self) -> int:
        """Frames a second now."""
        return self.ladder[self.level]

    @property
    def budget(self) -> float:
        """The share of one core it may take: more frames chosen, more allowed."""
        return BUDGET * self.cap / FPS

    def set_fps(self, fps: int) -> None:
        """The rate to run at, and ease down from. It starts measuring afresh."""
        fps = max(FPS_RANGE[0], min(FPS_RANGE[1], int(fps)))
        self.cap = fps
        self.ladder = ladder(fps)
        self.level = 0
        self._set_interval()
        self.pace.restart()
        self.cpu_mark = time.process_time()

    def _set_interval(self) -> None:
        self.timer.setInterval(round(1000 / self.rate))
        self.scene.fps = self.rate

    def _pace(self) -> None:
        wall = self.pace.elapsed()
        if wall < PACE_MS:
            return
        cpu = (time.process_time() - self.cpu_mark) / (wall / 1000.0)
        self.pace.restart()
        self.cpu_mark = time.process_time()
        level = self.level
        if cpu > self.budget and level < len(self.ladder) - 1:
            level += 1
        elif level > 0 and cpu * self.ladder[level - 1] / self.rate < self.budget * 0.85:
            level -= 1           # the faster rate would fit, with room to spare: a burst has passed
        if level != self.level:
            self.level = level
            self._set_interval()

    def showEvent(self, e) -> None:
        super().showEvent(e)
        self._sync()

    def hideEvent(self, e) -> None:
        super().hideEvent(e)
        self._sync()

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        if self.scene.static is None or not self.isVisible():
            self._fit()
        else:
            self.settle.start()          # dragging a window edge sends many; draw anew once it stops

    def _fit(self) -> None:
        self.settle.stop()
        self.scene.resize(self.width(), self.height(), self.devicePixelRatioF())
        self.update()

    def _tick(self) -> None:
        self.advance(min(max(self.clock.restart() / 1000.0, 0.0), MAX_STEP))
        self._pace()

    def advance(self, seconds: float) -> None:
        self.scene.step(seconds)
        if self.scene.changed():
            self.update()

    def paintEvent(self, e) -> None:
        p = QPainter(self)
        sw, sh = self.scene.w, self.scene.h
        if self.scene.static is not None and (sw, sh) != (self.width(), self.height()):
            p.scale(self.width() / sw, self.height() / sh)          # a resize still settling
        self.scene.paint(p)
        p.end()


_previews: dict[tuple, QPixmap] = {}


def preview(theme: Theme, t: dict, size: QSize, seconds: float = 2.0) -> QPixmap:
    """A still of a theme with a little of its interface on top, for the theme picker.
    Drawn once per look and size; the picker asks again every time Settings opens."""
    key = (theme.key, theme.dark, t.get("bg"), t.get("accent"), t.get("_picture"), t.get("_dim"),
           size.width(), size.height(), seconds)
    if key not in _previews:
        _previews[key] = _preview(theme, t, size, seconds)
    return _previews[key]


def _preview(theme: Theme, t: dict, size: QSize, seconds: float) -> QPixmap:
    scene = SCENES.get(theme.scene, Plain)(t, 1.0, seed=4)
    scene.resize(size.width(), size.height(), 2.0)
    for _ in range(int(seconds * 10)):
        scene.step(0.1)
    pm = QPixmap(size * 2)
    pm.setDevicePixelRatio(2.0)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    clip = QPainterPath()
    clip.addRoundedRect(QRectF(0, 0, size.width(), size.height()), 8, 8)
    p.setClipPath(clip)
    scene.paint(p)
    w, h = size.width(), size.height()
    p.setOpacity(1.0)
    p.fillRect(QRectF(0, 0, w, h * 0.16), QColor(t["topbar"]))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(t["accent"]))
    p.drawRoundedRect(QRectF(w * 0.05, h * 0.045, w * 0.07, h * 0.07), 2, 2)
    p.drawRoundedRect(QRectF(w * 0.74, h * 0.04, w * 0.21, h * 0.08), 3, 3)
    for rect in (QRectF(w * 0.05, h * 0.24, w * 0.34, h * 0.68), QRectF(w * 0.43, h * 0.24, w * 0.52, h * 0.68)):
        p.setBrush(QColor(t["panel"]))
        p.setPen(QPen(QColor(t["border"]), 1))
        p.drawRoundedRect(rect, 5, 5)
    p.setPen(Qt.PenStyle.NoPen)
    for i in range(4):
        y = h * (0.31 + i * 0.13)
        p.setBrush(QColor(t["sel"]) if i == 0 else QColor(t["raised"]))
        p.drawRoundedRect(QRectF(w * 0.07, y, w * 0.30, h * 0.09), 3, 3)
    for i, frac in enumerate((0.40, 0.46, 0.30, 0.42)):
        p.setBrush(QColor(t["muted"] if i else t["text"]))
        p.drawRoundedRect(QRectF(w * 0.46, h * (0.32 + i * 0.1), w * frac, h * 0.035), 1.5, 1.5)
    p.end()
    return pm
